"""Persist the short incoming record and its independently editable processing routes."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
from uuid import uuid4

from app import ae_card, crud, schemas
from app.audit import log_audit
from app.concurrency import ONBOARDING_INPUTS, lock_snapshot_inputs, require_version
from app.duplicates import DuplicateMessageError, message_candidates, message_fingerprint
from app.message_contacts import prepare_message_contacts
from app.models import Attachment, CaseNarrative, ContractContact, SafetyReport, SafetyReportProduct, SafetyReportRoute, SafetyReportRouteDocument


ROUTES = [("quality", "Качество", "КЧ"), ("medical", "Медицинский запрос", "МЗ"),
          ("pv", "НЯ и особые ситуации", "ФН"), ("other", "Другое", "ДР")]
COMPLAINT_GROUPS = [
    ("10069172", "Проблемы с контаминацией и стерильностью продукта"),
    ("10069219", "Проблемы с физическими характеристиками продукта"),
    ("10069265", "Проблемы с маркировкой продукта"),
    ("10069291", "Проблемы с упаковкой продукта"),
    ("10069324", "Проблемы с качеством продукта, НКДР"),
    ("10077538", "Контрафактные, фальсифицированные и недоброкачественные продукты"),
    ("10077544", "Проблемы с распространением и хранением продукта"),
    ("10077545", "Проблемы с поставками и доступностью продукта"),
]
COMPLAINTS = [label for _, label in COMPLAINT_GROUPS]
# Keep previously recorded descriptions editable without reclassifying them.
LEGACY_COMPLAINTS = ["Повреждение упаковки", "Нарушение герметичности", "Изменение внешнего вида, цвета или запаха",
              "Посторонние включения / загрязнение", "Ошибка маркировки или инструкции",
              "Неполная комплектация / неверное количество", "Неисправность устройства введения / дозирования",
              "Подозрение на нарушение подлинности", "Другое"]
SPECIAL_SITUATIONS = ["Применение во время беременности", "Применение при грудном вскармливании", "Передозировка",
                      "Ошибка применения", "Отсутствие эффективности", "Применение вне инструкции",
                      "Неправильное применение", "Злоупотребление", "Профессиональное воздействие", "Другое"]
COMMON_FIELDS = ("received_date", "received_time", "source_type", "message_direction", "partner_id",
                 "sender_contact_id", "sender_name", "sender_email", "sender_phone", "reporter_contact_id",
                 "reporter_same_as_sender", "reporter_unknown", "reporter_name", "reporter_email",
                 "reporter_phone", "reporter_country_code", "raw_subject", "raw_text", "original_text")


def flag(form, name):
    return str(form.get(name, "")).lower() in {"1", "yes", "true", "on"}


def text(form, name, maximum=10000):
    supplied = form.get(name, "")
    if supplied is not None and not isinstance(supplied, str):
        raise ValueError("Ожидается текстовое значение: " + name)
    value = (supplied or "").strip()
    if len(value) > maximum:
        raise ValueError("Поле слишком длинное: " + name)
    return value or None


def choice(form, name, allowed, default=""):
    value = text(form, name, 100) or default
    if value not in allowed:
        raise ValueError("Некорректный вариант ответа: " + name)
    return value


def common_payload(form, *, creating=False):
    values = {name: text(form, name, 100000 if name in {"raw_text", "original_text"} else 500) for name in COMMON_FIELDS}
    for role in ["sender", "reporter"]:
        if values[role + "_contact_id"] == "__new__":
            values[role + "_contact_id"] = None
    if not values["received_date"] or not values["raw_text"]:
        raise ValueError("Укажите дату получения и краткое содержание сообщения.")
    for name in ["reporter_same_as_sender", "reporter_unknown"]:
        values[name] = flag(form, name)
    values["message_direction"] = values["message_direction"] or "our_company_to_partner"
    if values["reporter_country_code"]:
        values["reporter_country_code"] = values["reporter_country_code"].upper()
    values["product_ids"] = form.getlist("product_ids")
    values["save_sender_contact"] = flag(form, "save_sender_contact")
    values["allow_duplicate_message"] = flag(form, "allow_duplicate_message")
    values["allow_duplicate_contact"] = flag(form, "allow_duplicate_contact")
    values["intake_enabled"] = True
    if creating:
        values["creation_token"] = text(form, "creation_token", 36) or str(uuid4())
    return schemas.SafetyReportCreate(**values)


def posted_values(form):
    """Only ordinary fields are returned to HTML after validation, never uploaded bytes."""
    result = {}
    for key in form.keys():
        values = [value for value in form.getlist(key) if isinstance(value, str)]
        result[key] = values if key.endswith("_ids") or key in {"routes", "quality_complaints", "pv_special_types"} else (values[-1] if values else "")
    return result


def report_values(report):
    values = {field: getattr(report, field) for field in COMMON_FIELDS}
    values["received_date"] = report.received_date.isoformat() if report.received_date else ""
    values["received_time"] = report.received_time.isoformat() if report.received_time else ""
    values["product_ids"] = report.product_ids
    values["creation_token"] = report.creation_token or ""
    values["routes"] = []
    for route in report.processing_routes:
        if route.is_deleted:
            continue
        if route.is_active:
            values["routes"].append(route.route_kind)
        values.update({route.route_kind + "_" + key: value for key, value in (route.details or {}).items()})
        values[route.route_kind + "_attachment_ids"] = [link.attachment_id for link in route.document_links if not link.is_deleted]
    if report.import_batch_id and not any(route.route_kind == "pv" for route in report.processing_routes):
        values["routes"].append("pv")
    return values


def uploads(form):
    from app.routers.placeholders import MAX_UPLOAD_BYTES, validate_upload_name
    result = []
    total = 0
    for field in ["original_files"] + [key + "_files" for key, _, _ in ROUTES]:
        for upload in form.getlist(field):
            if not hasattr(upload, "file") or not upload.filename:
                continue
            filename = validate_upload_name(upload.filename)
            digest = hashlib.sha256()
            size = 0
            upload.file.seek(0)
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ValueError("Общий размер файлов превышает допустимый размер загрузки.")
                digest.update(chunk)
            upload.file.seek(0)
            if not size:
                raise ValueError("Выбран пустой файл: " + filename)
            result.append((field, upload, filename, digest.hexdigest()))
    return result


def store_uploads(db, report, prepared, actor_id):
    from app.routers.placeholders import store_document_file
    existing = {row.checksum_sha256: row for row in db.query(Attachment).filter(
        Attachment.safety_report_id == report.id, Attachment.is_deleted.is_(False)).all() if row.checksum_sha256}
    by_route = {key: set() for key, _, _ in ROUTES}
    for field, upload, filename, checksum in prepared:
        document = existing.get(checksum)
        if document is None:
            stored = store_document_file(upload)
            document = crud.create_attachment(db, file_name=filename, safety_report_id=report.id,
                related_object_type="SafetySource", related_object_id=report.id,
                attachment_type="source", document_type="source", mime_type=upload.content_type,
                storage_path=stored["storage_path"], file_size_bytes=stored["file_size_bytes"],
                checksum_sha256=checksum, uploaded_by_user_id=actor_id, commit=False)
            existing[checksum] = document
        key = field.removesuffix("_files")
        if key in by_route:
            by_route[key].add(document.id)
    return by_route


def route_details(db, report, form, kind, *, can_save_contacts=False):
    prefix = kind + "_"
    overridden = flag(form, prefix + "products_overridden")
    products = crud.validate_message_product_ids(db, form.getlist(prefix + "product_ids") if overridden else report.product_ids)
    result = {"product_ids": products, "products_overridden": overridden}
    if kind == "quality":
        complaints = form.getlist("quality_complaints")
        if any(value not in COMPLAINTS + LEGACY_COMPLAINTS for value in complaints):
            raise ValueError("Неизвестный вид претензии к качеству.")
        previous = next((route.details or {} for route in report.processing_routes
                         if route.route_kind == kind and not route.is_deleted), {})
        result.update({key: text(form, prefix + key, 2000) for key in ["other_text", "batch", "expiry", "detected_date", "location"]})
        result.update(complaints=list(dict.fromkeys(complaints)),
                      materials=choice(form, prefix + "materials", {"", "yes", "no"}),
                      health=choice(form, prefix + "health", {"unknown", "yes", "no"}, previous.get("health") or "unknown"))
        if result["detected_date"]:
            date.fromisoformat(result["detected_date"])
    elif kind == "medical":
        source = choice(form, prefix + "person_source", {"sender", "reporter", "manual"}, "sender")
        result.update(question=text(form, prefix + "question"), person_source=source)
        contact_id = text(form, prefix + "contact_id", 36) if source == "manual" else None
        contact = db.get(ContractContact, contact_id) if contact_id else None
        previous = next((route.details or {} for route in report.processing_routes
                         if route.route_kind == kind and not route.is_deleted), {})
        unchanged_contact = previous.get("contact_id") == contact_id
        if contact_id and (not contact or (contact.is_deleted and not unchanged_contact)):
            raise ValueError("Выбранный обращающийся не найден. Обновите список контактных лиц.")
        result["contact_id"] = contact_id
        contact_values = {"name": contact.known_name, "email": contact.email,
                          "phone": contact.mobile_phone or contact.work_phone} if contact else {}
        for field in ["name", "email", "phone"]:
            result[field] = text(form, prefix + field, 255) if source == "manual" else getattr(report, source + "_" + field)
            if contact and not unchanged_contact and prefix + field not in form:
                result[field] = contact_values[field]
        result["due_manual"] = flag(form, prefix + "due_manual")
        due = text(form, prefix + "due_date", 10)
        result["due_date"] = ((date.fromisoformat(due).isoformat() if due else None) if result["due_manual"]
                              else (report.received_date + timedelta(days=30)).isoformat())
    elif kind == "pv":
        existing_card = ae_card.route_for(report)
        existing_card = existing_card and (existing_card.details or {}).get("card")
        if form.get("pv_card_json") or existing_card:
            state = ae_card.decode(form["pv_card_json"]) if form.get("pv_card_json") else None
            if not flag(form, "pv_card_dirty"):
                previous = ae_card.route_for(report)
                state = ae_card.state_for(report, previous.details if previous else {})
                # Adding another route must not write a short-card projection
                # back into the detailed clinical data imported from E2B.
                if report.import_batch_id:
                    return ae_card.details_from_state(state)
            state = ae_card.validate_state(db, state, report, save_contacts=True, can_save_contacts=can_save_contacts)
            ae_card.sync_report_people(db, report, state)
            ae_card.apply_to_case(db, report, state, form.get("expected_case_version") or None)
            return ae_card.details_from_state(state)
        special = form.getlist("pv_special_types")
        if any(value not in SPECIAL_SITUATIONS for value in special):
            raise ValueError("Неизвестная особая ситуация.")
        result.update(ae=flag(form, "pv_ae"), special=flag(form, "pv_special"), special_types=list(dict.fromkeys(special)),
                      special_other=text(form, "pv_special_other", 2000), patient_text=text(form, "pv_patient_text", 2000),
                      patient_unknown=flag(form, "pv_patient_unknown"), event_text=text(form, "pv_event_text", 10000),
                      event_unknown=flag(form, "pv_event_unknown"), seriousness=choice(form, "pv_seriousness", {"unknown", "yes", "no"}, "unknown"),
                      seriousness_text=text(form, "pv_seriousness_text", 2000))
    else:
        result.update({key: text(form, prefix + key, 2000) for key in ["description", "responsible", "action"]})
    return result


def save_routes(db, report, form, new_documents, *, can_save_contacts=False):
    selected = list(dict.fromkeys(form.getlist("routes")))
    if any(key not in {row[0] for row in ROUTES} for key in selected):
        raise ValueError("Неизвестное направление обработки.")
    if "quality" in selected and form.get("quality_health") == "yes" and "pv" not in selected:
        selected.append("pv")
    existing = {route.route_kind: route for route in report.processing_routes if not route.is_deleted}
    allowed_documents = {row.id for row in db.query(Attachment).filter(Attachment.safety_report_id == report.id,
                                                                     Attachment.is_deleted.is_(False)).all()}
    for key, _, code in ROUTES:
        route = existing.get(key)
        if key not in selected:
            if route and route.is_active:
                route.is_active = False
                log_audit(db, entity_type="SafetyReportRoute", entity_id=route.id, action="update",
                          field_name="is_active", old_value=True, new_value=False, user_id=crud.audit_user_id(db), source_module="PV Intake")
            continue
        details = route_details(db, report, form, key, can_save_contacts=can_save_contacts)
        is_new = route is None
        if route is None:
            route = SafetyReportRoute(safety_report_id=report.id, route_kind=key,
                                      route_number=f"{report.safety_report_number}/{code}-01")
            db.add(route)
            db.flush()
        old_details = route.details
        was_active = route.is_active
        route.details = details
        route.is_active = True
        requested = set(form.getlist(key + "_attachment_ids")) | new_documents[key]
        if not requested <= allowed_documents:
            raise ValueError("Документ не относится к этому входящему сообщению.")
        if key == "quality" and details["materials"] != "yes":
            requested = set()
        current = {link.attachment_id: link for link in route.document_links}
        for identifier, link in current.items():
            if identifier not in requested:
                db.delete(link)
        for identifier in requested - current.keys():
            db.add(SafetyReportRouteDocument(route_id=route.id, attachment_id=identifier))
        db.flush()
        if is_new:
            crud.log_create_event(db, entity_type="SafetyReportRoute", entity_id=route.id,
                data={"safety_report_id": report.id, "route_kind": key, "route_number": route.route_number, "details": details})
        elif old_details != details:
            log_audit(db, entity_type="SafetyReportRoute", entity_id=route.id, action="update",
                      field_name="details", old_value=old_details, new_value=details, source_module="Incoming messages", user_id=crud.audit_user_id(db))
        if not was_active:
            log_audit(db, entity_type="SafetyReportRoute", entity_id=route.id, action="update",
                      field_name="is_active", old_value=False, new_value=True, user_id=crud.audit_user_id(db), source_module="PV Intake")
        if set(current) != requested:
            log_audit(db, entity_type="SafetyReportRoute", entity_id=route.id, action="update",
                      field_name="attachments", old_value=", ".join(sorted(current)), new_value=", ".join(sorted(requested)),
                      user_id=crud.audit_user_id(db), source_module="PV Intake")
    return selected


def save_intake(db, form, *, report=None, actor_id=None, can_save_contacts=False):
    lock_snapshot_inputs(db, ONBOARDING_INPUTS + ("SafetyReport", "SafetyReportRoute", "SafetyReportRouteDocument"))
    if report is not None:
        db.refresh(report)
        if report.is_deleted:
            raise ValueError("Сообщение удалено. Обновите список сообщений.")
        try:
            expected = int(form.get("expected_version", ""))
        except (ValueError, TypeError):
            raise ValueError("Обновите страницу перед сохранением сообщения.")
        require_version(report, expected)
    else:
        token = text(form, "creation_token", 36)
        if token:
            previous = db.query(SafetyReport).filter_by(creation_token=token, is_deleted=False).first()
            if previous:
                return previous
    payload = common_payload(form, creating=report is None)
    prepared = uploads(form)
    hashes = [row[3] for row in prepared]
    source_text = payload.original_text or payload.raw_text
    text_changed = report is None or message_fingerprint(source_text) != message_fingerprint(report.original_text or report.raw_text)
    if (text_changed or hashes) and not payload.allow_duplicate_message:
        matches = message_candidates(db, text=source_text if text_changed else None, received_date=payload.received_date,
                                     sender_email=payload.sender_email, sender_phone=payload.sender_phone,
                                     checksums=hashes, exclude_id=report.id if report else None)
        if matches:
            raise DuplicateMessageError(matches)
    if report is None:
        report = crud.create_safety_report(db, payload)
    else:
        values = crud.clean_form_data(payload.model_dump())
        products = crud.validate_message_product_ids(db, payload.product_ids)
        prepare_message_contacts(db, values, save_sender=payload.save_sender_contact,
                                allow_duplicate=payload.allow_duplicate_contact, existing_report=report)
        case = ae_card.linked_case(report)
        if case and crud.case_is_controlled(case) and any(
                values.get(key) != getattr(report, key) for key in COMMON_FIELDS):
            raise ValueError("Связанный ИСНР закрыт или направлен. Повторно откройте его перед изменением исходных сведений.")
        old = {key: getattr(report, key) for key in COMMON_FIELDS}
        for key in COMMON_FIELDS:
            setattr(report, key, values.get(key))
        report.content_fingerprint = message_fingerprint(report.original_text or report.raw_text)
        report.intake_enabled = True
        report.version += 1
        crud.sync_message_product_links(db, report, SafetyReportProduct, products)
        crud.log_field_changes(db, entity_type="SafetyReport", entity_id=report.id, old_values=old,
                               new_values={key: getattr(report, key) for key in COMMON_FIELDS})
    db.flush()
    new_documents = store_uploads(db, report, prepared, actor_id)
    selected = save_routes(db, report, form, new_documents, can_save_contacts=can_save_contacts)
    active_routes = db.query(SafetyReportRoute).filter_by(safety_report_id=report.id, is_active=True, is_deleted=False).all()
    all_products = list(report.product_ids)
    for route in active_routes:
        for identifier in (route.details or {}).get("product_ids", []):
            if identifier not in all_products:
                all_products.append(identifier)
    if set(all_products) != set(report.product_ids):
        case = ae_card.linked_case(report)
        if case and crud.case_is_controlled(case):
            raise ValueError("Связанный ИСНР закрыт или направлен. Повторно откройте его перед изменением списка ЛП сообщения.")
        crud.sync_message_product_links(db, report, SafetyReportProduct, all_products)
        for route in active_routes:
            if not (route.details or {}).get("products_overridden"):
                route.details = {**route.details, "product_ids": all_products}
    handoff = form.get("action") == "handoff" or (form.get("action") == "next" and form.get("current_step") == "pv"
        and not any(key in selected for key, _, _ in ROUTES[3:]))
    if handoff:
        if "pv" not in selected:
            raise ValueError("Для перехода в карточку ИСНР выберите шаг «НЯ и особые ситуации».")
        if not ae_card.linked_case(report):
            pv = db.query(SafetyReportRoute).filter_by(safety_report_id=report.id, route_kind="pv").one()
            details = pv.details or {}
            case = crud.create_case_from_report(db, report, as_draft=True, selected_product_ids=[] if details.get("card") else details.get("product_ids", report.product_ids))
            if details.get("card"):
                db.refresh(report)
                state = deepcopy(details["card"])
                ae_card.apply_to_case(db, report, state)
                pv.details = ae_card.details_from_state(state)
                db.commit()
                db.refresh(report)
                return report
            case.seriousness = {"yes": "serious", "no": "non-serious"}.get(details.get("seriousness"), "unknown")
            notes = []
            information = []
            if details.get("ae"):
                information.append("Нежелательное явление")
            if details.get("special"):
                information.append("Особая ситуация")
            if information:
                notes.append("Поступившая информация: " + "; ".join(information))
            for label, field in [("НЯ / особая ситуация", "event_text")]:
                if details.get(field):
                    notes.append(label + ": " + details[field])
            if details.get("patient_unknown"):
                notes.append("Сведения о пациенте пока неизвестны.")
            if details.get("event_unknown"):
                notes.append("Описание НЯ / особой ситуации пока неизвестно.")
            if details.get("special") and details.get("special_types"):
                notes.append("Особые ситуации: " + "; ".join(details["special_types"]) +
                             (". " + details["special_other"] if details.get("special_other") else ""))
            if details.get("seriousness") == "yes" and details.get("seriousness_text"):
                notes.append("Первичные сведения о серьёзности: " + details["seriousness_text"])
            for note in notes:
                narrative = CaseNarrative(case_id=case.id, narrative_type="source_information", source="Исходное сообщение", narrative_text=note)
                db.add(narrative)
                db.flush()
                crud.log_create_event(db, entity_type="CaseNarrative", entity_id=narrative.id, case_id=case.id,
                                      data={"narrative_type": narrative.narrative_type, "narrative_text": note}, user_id=actor_id)
    db.commit()
    db.refresh(report)
    return report
