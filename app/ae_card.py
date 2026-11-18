"""Shared short PV card; directory links and case records remain the source of truth."""
from copy import deepcopy
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm.exc import StaleDataError

from app import crud, models, schemas
from app.concurrency import require_version
from app.duplicates import contact_candidates


CRITERIA = ["Смерть", "Угроза жизни", "Госпитализация или её продление",
            "Стойкая или значительная нетрудоспособность / инвалидизация",
            "Врождённая аномалия / порок развития", "Другое клинически значимое событие"]
REACTION_FIELDS = ["seriousness_death", "seriousness_life_threatening", "seriousness_hospitalization",
                   "seriousness_disability", "seriousness_congenital_anomaly", "seriousness_other_medically_important"]


class PatientData(BaseModel):
    initials: str = Field(default="", max_length=255)  # UI label is Ф.И.О.; retained widget key.
    age: str = Field(default="", max_length=100)
    sex: Literal["", "female", "male"] = ""
    phone: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=255)
    other: str = Field(default="", max_length=100000)


class Person(BaseModel):
    id: str = Field(max_length=80)
    name: str = Field(default="", max_length=255)
    country: str = Field(default="", max_length=100)
    phone: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=255)
    saved: bool = False


class Binding(BaseModel):
    mode: Literal["", "patient", "same", "existing", "new", "local", "unknown"] = ""
    personId: str = Field(default="", max_length=80)
    role: str = Field(default="", max_length=255)


class Contacts(BaseModel):
    sender: Binding = Field(default_factory=Binding)
    primary: Binding = Field(default_factory=Binding)
    additional: list[Binding] = Field(default_factory=list, max_length=100)


class Medication(BaseModel):
    id: str = Field(max_length=80)
    companyId: str = Field(default="", max_length=36)
    reported: str = Field(default="", max_length=255)
    caseProductId: str = Field(default="", max_length=36)


class Card(BaseModel):
    version: int = 4
    patient: PatientData = Field(default_factory=PatientData)
    unknown: dict[str, bool] = Field(default_factory=dict)
    people: list[Person] = Field(default_factory=list, max_length=300)
    contacts: Contacts = Field(default_factory=Contacts)
    medications: list[Medication] = Field(default_factory=list, max_length=200)
    specials: list[str] = Field(default_factory=list, max_length=10)
    other: str = Field(default="", max_length=2000)
    ae: bool = False
    special: bool = False
    description: str = Field(default="", max_length=100000)
    serious: Literal["no", "yes"] = "no"
    criteria: list[str] = Field(default_factory=list, max_length=6)
    touched: dict[str, bool] = Field(default_factory=dict)


def empty_state():
    state = Card().model_dump()
    state["unknown"] = dict.fromkeys(["patient", "reporter", "drug", "reaction"], False)
    state["medications"] = [dict(id=str(uuid4()), companyId="", reported="", caseProductId="")]
    return state


def route_for(report):
    return next((row for row in report.processing_routes if row.route_kind == "pv" and not row.is_deleted), None)


def linked_case(report):
    """Follow-ups retain their own case link, including multi-case packages."""
    if report is None:
        return None
    if report.case is not None:
        return report.case
    if report.import_batch_id and report.case_id:
        from sqlalchemy.orm import object_session
        session = object_session(report)
        case = session.get(models.Case, report.case_id) if session else None
        if case and not case.is_deleted:
            return case
    batch = report.e2b_import_batch if report.import_batch_id else None
    if batch and batch.created_case_id == report.case_id:
        return batch.created_case
    return None


def directory_person(contact):
    return dict(id=contact.id, name=contact.known_name or "", country=contact.country or "",
                phone=contact.mobile_phone or contact.work_phone or "", email=contact.email or "", saved=True)


def state_for(report=None, details=None):
    details = details or {}
    state = deepcopy(details.get("card")) if details.get("card") else empty_state()
    if details.get("card") and report:
        for role, key in [("sender", "sender"), ("reporter", "primary")]:
            binding = state["contacts"][key]
            if binding["mode"] in {"patient", "same", "unknown"}:
                continue
            identifier = getattr(report, role + "_contact_id")
            fields = {field: getattr(report, role + "_" + field) or "" for field in ["name", "phone", "email"]}
            if not identifier and not any(fields.values()):
                binding["mode"] = ""
                continue
            person = next((p for p in state["people"] if p["id"] == (identifier or binding["personId"])), None)
            if person is None:
                person = dict(id=identifier or str(uuid4()), country="", saved=bool(identifier), **fields)
                state["people"].append(person)
            else:
                person.update(fields)
            binding.update(mode="existing" if identifier else "new", personId=person["id"])
    if not details.get("card"):
        state.update(ae=bool(details.get("ae")), special=bool(details.get("special")),
                     specials=details.get("special_types") or [], other=details.get("special_other") or "",
                     description=details.get("event_text") or "", serious="yes" if details.get("seriousness") == "yes" else "no")
        state["unknown"].update(patient=bool(details.get("patient_unknown")), reaction=bool(details.get("event_unknown")))
        if report:
            for role, key in [("sender", "sender"), ("reporter", "primary")]:
                if key == "primary" and report.reporter_same_as_sender:
                    state["contacts"][key]["mode"] = "same"
                elif key == "primary" and report.reporter_unknown:
                    state["contacts"][key]["mode"] = "unknown"
                elif any(getattr(report, role + "_" + field) for field in ["name", "phone", "email"]) or getattr(report, role + "_contact_id"):
                    identifier = getattr(report, role + "_contact_id") or str(uuid4())
                    existing = next((p for p in state["people"] if p["id"] == identifier), None)
                    if not existing:
                        state["people"].append(dict(id=identifier, name=getattr(report, role + "_name") or "",
                            phone=getattr(report, role + "_phone") or "", email=getattr(report, role + "_email") or "",
                            country=(report.reporter_country_code or "") if key == "primary" else "", saved=bool(getattr(report, role + "_contact_id"))))
                    state["contacts"][key] = dict(mode="existing" if getattr(report, role + "_contact_id") else "new", personId=identifier, role="")
            state["medications"] = [dict(id=link.id, companyId=link.product_id, reported=link.reported_product_name or "", caseProductId="")
                                    for link in report.product_links if not link.is_deleted and link.product_id in details.get("product_ids", report.product_ids)] or state["medications"]
    case = linked_case(report)
    if case:
        patients = [row for row in case.patients if not row.is_deleted and row.patient_role == "patient"]
        if patients:
            patient = patients[0]
            age = patient.age_text
            if age is None and patient.age_value is not None:
                units = {"years": "лет", "months": "мес.", "days": "дн.", "weeks": "нед.",
                         "a": "лет", "mo": "мес.", "d": "дн.", "wk": "нед."}
                age = f"{patient.age_value:g} {units.get(patient.age_unit, patient.age_unit or '')}".strip()
            state["patient"] = dict(initials=patient.full_name if patient.full_name is not None else patient.patient_initials or "", age=age or "",
                sex=patient.sex if patient.sex in {"male", "female"} else "", phone=patient.phone or "", email=patient.email or "", other=patient.medical_history_text or "")
        elif details.get("card"):
            state["patient"] = PatientData().model_dump()
        state["description"] = case.narrative or ""
        state["serious"] = "yes" if case.seriousness == "serious" else "no"
        state["criteria"] = case.seriousness_criteria
        if state["criteria"] is None:
            state["criteria"] = [label for label, field in zip(CRITERIA, REACTION_FIELDS)
                                 if any(getattr(row, field) for row in case.reactions if not row.is_deleted)]
        ranks = {row.get("caseProductId"): index for index, row in enumerate(state["medications"]) if row.get("caseProductId")}
        medicines = sorted((row for row in case.case_products if not row.is_deleted),
            key=lambda row: (0, ranks[row.id], "") if row.id in ranks else (1, 0, (row.created_at.isoformat() if row.created_at else "") + row.id))
        state["medications"] = [dict(id=row.id, caseProductId=row.id, companyId=row.product_id or "", reported=row.reported_product_name or "")
                                for row in medicines] or empty_state()["medications"]
    return state


def decode(value):
    if not isinstance(value, str) or len(value) > 500000:
        raise ValueError("Некорректный размер карточки НЯ.")
    try:
        return Card.model_validate_json(value).model_dump()
    except (ValidationError, ValueError) as exc:
        raise ValueError("Не удалось прочитать карточку НЯ. Проверьте введённые сведения.") from exc


def validate_state(db, state, report, *, save_contacts=False, can_save_contacts=False):
    from app.message_intake import SPECIAL_SITUATIONS
    if any(value not in SPECIAL_SITUATIONS for value in state["specials"]) or any(value not in CRITERIA for value in state["criteria"]):
        raise ValueError("Неизвестная особая ситуация или критерий серьёзности.")
    state["specials"] = list(dict.fromkeys(state["specials"]))
    state["criteria"] = list(dict.fromkeys(state["criteria"]))
    if state["contacts"]["sender"]["mode"] == "patient" and not state["contacts"]["primary"]["mode"]:
        state["contacts"]["primary"]["mode"] = "patient"
    med_ids = [med["id"] for med in state["medications"]]
    case_med_ids = [med["caseProductId"] for med in state["medications"] if med["caseProductId"]]
    if len(set(med_ids)) != len(med_ids) or len(set(case_med_ids)) != len(case_med_ids):
        raise ValueError("Повторяется идентификатор строки препарата.")
    if state["criteria"]:
        state["serious"] = "yes"
    previous = (route_for(report).details or {}).get("card", {}) if report and route_for(report) else {}
    old_products = {row.get("companyId") for row in previous.get("medications", [])}
    case = linked_case(report)
    if case:
        old_products.update(row.product_id for row in case.case_products if not row.is_deleted)
    for med in state["medications"]:
        if med["companyId"]:
            product = db.get(models.Product, med["companyId"])
            if not product or (product.is_deleted and product.id not in old_products):
                raise ValueError("Препарат не найден в справочнике. Обновите список препаратов.")
    people = {person["id"]: person for person in state["people"]}
    if len(people) != len(state["people"]):
        raise ValueError("Повторяется идентификатор контактного лица.")
    bindings = [state["contacts"]["sender"], state["contacts"]["primary"], *state["contacts"]["additional"]]
    if state["contacts"]["sender"]["mode"] == "same":
        raise ValueError("Для отправителя нельзя выбрать «Тот же контакт».")
    for binding in bindings:
        if binding["mode"] not in {"new", "existing", "local"}:
            continue
        person = people.get(binding["personId"])
        if person is None:
            raise ValueError("Выберите контактное лицо или внесите его известные сведения.")
        if binding["mode"] == "existing":
            contact = db.get(models.ContractContact, person["id"])
            previously_used = any(p.get("id") == person["id"] for p in previous.get("people", []))
            if not contact or (contact.is_deleted and not previously_used):
                raise ValueError("Контактное лицо не найдено. Обновите список контактов.")
        elif person["saved"] and save_contacts:
            if not can_save_contacts:
                raise PermissionError("Для сохранения в контактных лицах требуется право создания контактов.")
            if not any(person[key].strip() for key in ["name", "country", "phone", "email"]):
                raise ValueError("Заполните известные сведения о контактном лице.")
            if contact_candidates(db, name=person["name"], email=person["email"], phones=[person["phone"]], partner_id=report.partner_id):
                raise ValueError("Такое контактное лицо, возможно, уже существует. Выберите его из общего списка контактных лиц.")
            contact = crud.create_contract_contact(db, schemas.ContractContactCreate(display_name=person["name"] or None,
                country=person["country"] or None, email=person["email"] or None, mobile_phone=person["phone"] or None,
                partner_id=report.partner_id, contact_type="pv", is_pv_contact=True, is_reconciliation_recipient=False,
                cc_reconciliation=False, is_primary=False, is_billing_contact=False))
            old_id = person["id"]
            person["id"] = contact.id
            people[contact.id] = person
            for other in bindings:
                if other["personId"] == old_id:
                    other.update(personId=contact.id, mode="existing")
    return state


def resolve_person(state, binding, *, sender=False):
    mode = binding["mode"]
    if mode == "patient":
        return dict(name=state["patient"]["initials"], phone=state["patient"]["phone"], email=state["patient"]["email"], country="", id="")
    if mode == "same" and not sender:
        return resolve_person(state, state["contacts"]["sender"], sender=True)
    return next((p for p in state["people"] if p["id"] == binding["personId"]), {}) if mode in {"new", "existing", "local"} else {}


def sync_report_people(db, report, state):
    old = {role + "_" + field: getattr(report, role + "_" + field)
           for role in ["sender", "reporter"] for field in ["name", "phone", "email", "contact_id"]}
    old.update(reporter_same_as_sender=report.reporter_same_as_sender, reporter_unknown=report.reporter_unknown)
    for role, key in [("sender", "sender"), ("reporter", "primary")]:
        binding = state["contacts"][key]
        person = resolve_person(state, binding)
        for field in ["name", "phone", "email"]:
            setattr(report, role + "_" + field, person.get(field) or None)
        saved = binding["mode"] == "existing" or (binding["mode"] == "same" and state["contacts"]["sender"]["mode"] == "existing")
        setattr(report, role + "_contact_id", person.get("id") if saved else None)
    report.reporter_same_as_sender = state["contacts"]["primary"]["mode"] == "same"
    report.reporter_unknown = state["contacts"]["primary"]["mode"] == "unknown"
    case = linked_case(report)
    if case and crud.case_is_controlled(case) and any(getattr(report, key) != value for key, value in old.items()):
        raise ValueError("Связанный ИСНР закрыт или направлен. Повторно откройте его перед изменением контактов сообщения.")
    crud.log_field_changes(db, entity_type="SafetyReport", entity_id=report.id, old_values=old,
                          new_values={key: getattr(report, key) for key in old})
    for route in db.query(models.SafetyReportRoute).filter_by(safety_report_id=report.id, is_deleted=False).all():
        if route.route_kind != "medical" or route.is_deleted:
            continue
        previous = route.details or {}
        source = previous.get("person_source")
        if source in {"sender", "reporter"}:
            details = {**previous, **{field: getattr(report, source + "_" + field) for field in ["name", "phone", "email"]}}
            if details != previous:
                route.details = details
                crud.log_field_changes(db, entity_type="SafetyReportRoute", entity_id=route.id,
                                      old_values={"details": previous}, new_values={"details": details})


def apply_to_case(db, report, state, expected_version=None):
    case = linked_case(report)
    if not case:
        return
    if expected_version is not None:
        require_version(case, int(expected_version))
    changed = state_for(report, {"card": state})
    if crud.case_is_controlled(case):
        fields = ["patient", "medications", "description", "serious", "criteria"]
        # Medication UI row IDs are independent from persistent IDs.
        same_meds = [(m["companyId"], m["reported"]) for m in state["medications"] if m["companyId"] or m["reported"].strip()] == [(m["companyId"], m["reported"]) for m in changed["medications"] if m["companyId"] or m["reported"].strip()]
        if not same_meds or any(state[field] != changed[field] for field in fields if field != "medications"):
            raise ValueError("Связанный ИСНР закрыт или направлен. Повторно откройте его перед изменением сведений.")
        return
    old_case = dict(narrative=case.narrative, seriousness=case.seriousness, seriousness_criteria=case.seriousness_criteria)
    clinical_before = {**old_case,
        'seriousness': 'serious' if changed['serious']=='yes' or changed['criteria'] else 'non-serious',
        'seriousness_criteria': changed['criteria']}
    case.narrative = state["description"] or None
    case.seriousness = "serious" if state["serious"] == "yes" else "non-serious"
    case.seriousness_criteria = state["criteria"]
    crud.log_field_changes(db, entity_type="Case", entity_id=case.id, case_id=case.id,
                          old_values=old_case, new_values={key: getattr(case, key) for key in old_case})
    patient = next((p for p in case.patients if not p.is_deleted and p.patient_role == "patient"), None)
    if len([p for p in case.patients if not p.is_deleted and p.patient_role == "patient"]) > 1:
        raise ValueError("В случае несколько записей пациента. Измените нужную запись в клинических данных.")
    old_patient = {key: getattr(patient, key) if patient else None
                   for key in ("sex", "age_value", "age_unit", "medical_history_text")}
    data = state["patient"]
    if patient or any(value.strip() for value in data.values()):
        new = patient is None
        if new:
            patient = models.Patient(case=case, patient_role="patient")
            db.add(patient)
        fields = dict(full_name=data["initials"], age_text=data["age"], sex=data["sex"] or None,
                      phone=data["phone"] or None, email=data["email"] or None, medical_history_text=data["other"] or None)
        # Keep the reported text and the existing numeric age in agreement.
        if patient.age_text != data["age"]:
            age = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(лет|год|года|г\.?|years?|мес\.?|месяц(?:а|ев)?|months?|дн\.?|день|дня|дней|days?|нед\.?|недел[яьиь]|weeks?)?\s*", data["age"], re.I)
            unit = age.group(2).lower() if age and age.group(2) else "лет"
            fields.update(age_value=float(age.group(1).replace(",", ".")) if age else None,
                          age_unit=("months" if unit.startswith(("мес", "month")) else "days" if unit.startswith(("д", "day")) else "weeks" if unit.startswith(("нед", "week")) else "years") if age else None)
        old = {key: getattr(patient, key) for key in fields}
        for key, value in fields.items():
            setattr(patient, key, value)
        db.flush()
        if new:
            crud.log_create_event(db, entity_type="Patient", entity_id=patient.id, case_id=case.id, data=fields)
        else:
            crud.log_field_changes(db, entity_type="Patient", entity_id=patient.id, case_id=case.id, old_values=old, new_values=fields)
    existing = {row.id: row for row in case.case_products if not row.is_deleted}
    used = set()
    for med in state["medications"]:
        if not med["companyId"] and not med["reported"].strip():
            continue
        row = existing.get(med["caseProductId"])
        if med["caseProductId"] and row is None:
            raise StaleDataError("Препарат ИСНР изменён. Обновите страницу.")
        is_new = row is None
        if is_new:
            row = models.CaseProduct(case=case, drug_role="suspect")
            db.add(row)
        old = dict(product_id=row.product_id, reported_product_name=row.reported_product_name)
        row.product_id = med["companyId"] or None
        row.reported_product_name = med["reported"] or None
        db.flush()
        med["caseProductId"] = row.id
        used.add(row.id)
        values = dict(product_id=row.product_id, reported_product_name=row.reported_product_name)
        changed_fields = {key for key, value in values.items() if old.get(key) != value}
        if changed_fields:
            import json
            from app.crud import audit_user_id
            row.reviewed_fields_json = json.dumps(sorted(set(json.loads(row.reviewed_fields_json or '[]')) | changed_fields))
            origins = json.loads(row.current_sources_json or '{}')
            origins.update({key: dict(origin='manual', author_id=audit_user_id(db),
                confirmed_at=models.utcnow().isoformat(), reason='Изменение через краткую карточку НЯ') for key in changed_fields})
            row.current_sources_json = json.dumps(origins, ensure_ascii=False)
        if is_new:
            crud.log_create_event(db, entity_type="CaseProduct", entity_id=row.id, case_id=case.id, data=values)
        else:
            crud.log_field_changes(db, entity_type="CaseProduct", entity_id=row.id, case_id=case.id,
                                  old_values=old, new_values=values)
    for identifier, row in existing.items():
        if identifier not in used:
            if any(not child.is_deleted for children in (row.dosages, row.indications, row.rechallenges)
                   for child in children):
                raise ValueError("У препарата есть клинические повторы. Разберите их в подробной карточке перед удалением препарата.")
            row.is_deleted = True
            crud.log_field_changes(db, entity_type="CaseProduct", entity_id=row.id, case_id=case.id,
                                  old_values={"is_deleted": False}, new_values={"is_deleted": True})
    from app.short_card_clinical import sync_short_card
    sync_short_card(db, case, patient, old_patient, clinical_before)


def details_from_state(state):
    return dict(card=state, product_ids=list(dict.fromkeys(m["companyId"] for m in state["medications"] if m["companyId"])),
                products_overridden=True, ae=state["ae"], special=state["special"], special_types=state["specials"],
                special_other=state["other"], patient_unknown=state["unknown"].get("patient", False),
                event_text=state["description"], event_unknown=state["unknown"].get("reaction", False), seriousness=state["serious"])
