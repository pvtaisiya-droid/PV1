"""Independent incoming messages, explicit repeat matching and atomic application."""
import copy
import hashlib
import json
from xml.etree import ElementTree as E
from fastapi import HTTPException
from sqlalchemy.orm.exc import StaleDataError
from app import models as m
from app.clinical_values import dump
from app import clinical_graph as g, clinical_revision as revisions, clinical_data
from app.crud import audit_user_id, assert_case_editable, case_is_controlled
from app.concurrency import protect_record, lock_snapshot_inputs
from app.audit import log_audit

H = "{urn:hl7-org:v3}"


def ingest(db, service, batch, item, xml):
    from app.e2b_import import MAX_SINGLE_FILE_BYTES
    if len(xml) > MAX_SINGLE_FILE_BYTES or b"<!doctype" in xml.lower() or b"<!entity" in xml.lower():
        raise ValueError('XML превышает лимит или содержит запрещённое объявление DTD/entity.')
    try:
        root = E.fromstring(xml)
    except E.ParseError:
        return False
    if not root.tag.startswith(H):
        return False
    messages = list(root.iter(H + "PORR_IN049016UV"))
    if not messages:
        return False
    if db.query(m.IcsrMessage).filter_by(item_id=item.id).count():
        return True
    for ordinal, original in enumerate(messages, 1):
        wrapper = copy.deepcopy(root)
        if wrapper.tag != H + "PORR_IN049016UV":
            for parent in wrapper.iter():
                for child in list(parent):
                    if child.tag == H + "PORR_IN049016UV":
                        parent.remove(child)
            wrapper.append(copy.deepcopy(original))
        fragment = E.tostring(wrapper, encoding="utf-8")
        row = m.IcsrMessage(id=m.new_uuid(), batch_id=batch.id, item_id=item.id, ordinal=ordinal,
                            source_checksum=item.checksum_sha256, fragment_checksum=hashlib.sha256(fragment).hexdigest(),
                            source_xml=fragment.decode("utf-8"), status="review")
        try:
            parsed = service.parser.parse(fragment).as_dict()
            source = dict(batch_id=batch.id, item_id=item.id, message_id=row.id, ordinal=ordinal,
                          checksum_sha256=item.checksum_sha256, fragment_checksum=row.fragment_checksum)
            parsed["message"]["import_source"] = source
            for record in parsed["clinical_records"]:
                record["source_facts"]["import_source"] = source
                for value in record["fields"].values():
                    value.setdefault("context", {})["import_source"] = source
            row.normalized_json = dump(parsed)
            row.issues_json = dump(parsed["warnings"])
            if any(w.get("blocking") for w in parsed["warnings"]):
                row.status = "error"
            else:
                g.graph(parsed)  # verify links before presenting a confirm action
                duplicates = db.query(m.IcsrMessage).filter_by(source_checksum=row.source_checksum,
                    fragment_checksum=row.fragment_checksum, status='applied').all()
                if duplicates:
                    cases = {d.case_id for d in duplicates}
                    if len(cases) != 1:
                        raise ValueError('Одинаковый исходник ранее отнесён к разным случаям; требуется разбор.')
                    row.status, row.case_id = 'duplicate', cases.pop()
                    row.decision_json = dump(dict(duplicate_of=[d.id for d in duplicates], reason='Exact physical and fragment SHA256 already applied; no clinical changes'))
        except (ValueError, TypeError) as exc:
            row.status = "error"
            row.issues_json = dump([dict(type="message_parse", message=str(exc), blocking=True)])
        db.add(row)
    item.e2b_version, item.status = "E2B(R3)", "messages"
    item.normalized_json = row.normalized_json if len(messages) == 1 and row.normalized_json else dump({"message": {}, "message_model": "icsr-message-v1", "messages": len(messages)})
    return True


def message(db, message_id):
    row = db.get(m.IcsrMessage, message_id)
    if not row or row.is_deleted:
        raise HTTPException(404, "Сообщение не найдено.")
    return row


def existing(db, row):
    from app.e2b_import import E2BImportService
    return E2BImportService().find_existing_case(db, json.loads(row.normalized_json)["message"])


def candidates(db, case, node):
    if case is None:
        return []
    cls = getattr(m, node["model"])
    query = db.query(cls).filter_by(is_deleted=False)
    if hasattr(cls, "case_id"):
        query = query.filter_by(case_id=case.id)
    else:
        query = query.join(m.CaseProduct).filter(m.CaseProduct.case_id == case.id)
    if cls is m.CaseClinicalRecord:
        query = query.filter_by(kind=node["kind"])
    result=[]
    for r in query:
        context={}
        for field,model,title in [('case_product_id',m.CaseProduct,'Препарат'),('reaction_id',m.Reaction,'Реакция'),('parent_id',m.CaseClinicalRecord,'Родительский блок')]:
            key=getattr(r,field,None)
            if key:
                linked=db.get(model,key)
                context[title]=dict(id=key,name=getattr(linked,'reported_product_name',None) or getattr(linked,'reported_term',None) or getattr(linked,'kind',None))
        result.append(dict(id=r.id,values=g.current_fields(r),version=r.version,context=context))
    return json.loads(dump(result))


def inspect_message(db, row):
    if row.status == "error" or not row.normalized_json:
        return dict(message_id=row.id, status=row.status, issues=json.loads(row.issues_json), nodes=[])
    case = existing(db, row)
    nodes = g.graph(json.loads(row.normalized_json))
    return dict(message_id=row.id, status=row.status, issues=json.loads(row.issues_json),
                case_id=case.id if case else None, source=json.loads(row.normalized_json)["message"],
                documents=[dict(id=i.id, name=i.file_name, checksum=i.checksum_sha256) for i in db.query(m.E2BImportItem).filter_by(batch_id=row.batch_id, is_deleted=False) if i.file_role in ('pdf','cioms','literature_pdf','other')],
                nodes=[{**n, "fields": g.fields(n), "candidates": candidates(db, case, n)} for n in nodes])


def prepare(db, row, mappings, reason, sequence_reason="", attachment_item_ids=None):
    if row.status == "applied":
        raise ValueError("Сообщение уже применено; повторное подтверждение не создаёт записи.")
    if row.status != "review" or len(reason.strip()) < 10:
        raise ValueError("Нужны исправное сообщение и содержательное основание решения.")
    case = existing(db, row)
    if case and case_is_controlled(case):
        raise ValueError('Случай закрыт или заблокирован; перед разбором требуется штатное открытие с основанием.')
    nodes = g.graph(json.loads(row.normalized_json))
    if set(mappings) != {n["key"] for n in nodes}:
        raise ValueError("Для каждого входящего повтора выберите добавить, сопоставить или не применять.")
    seen, decisions, diff = set(), {}, []
    for node in nodes:
        target = mappings[node["key"]]
        current = None
        if target not in ("new", "ignore"):
            if not case:
                raise ValueError("Первичное сообщение не может ссылаться на повторы другого случая.")
            current = db.get(getattr(m, node["model"]), target)
            if not current or current.is_deleted or g.owner(db, current) != case.id or (node["model"] == "CaseClinicalRecord" and current.kind != node["kind"]):
                raise ValueError("Сопоставление указывает на чужую, архивную запись или другой тип блока.")
            if node["kind"] == "assessment" and current.causality_method not in ("imported_text", "manual_text", None):
                raise ValueError("Рассчитанную причинность нельзя заменять входящим текстом; добавьте отдельную оценку.")
            ident = (node["model"], target)
            if ident in seen:
                raise ValueError("Два входящих повтора нельзя скрыто объединить в одну запись.")
            seen.add(ident)
        old = g.current_fields(current) if current else {}
        new = g.fields(node)
        def semantic(value):
            if isinstance(value, dict) and 'raw' in value:
                return {k:value.get(k) for k in ('raw','value','null_flavor','system','system_version','precision','timezone')}
            return value
        changes = {f: dict(old=old.get(f), incoming=v, conflict=bool(current and semantic(old.get(f)) != semantic(v))) for f, v in new.items()}
        diff.append(dict(key=node["key"], kind=node["kind"], target=target, changes=changes, links=node["links"]))
        decisions[node["key"]] = target
        for linked in node["links"].values():
            if target != "ignore" and mappings.get(linked) == "ignore":
                raise ValueError("Применяемая запись ссылается на пропущенный повтор; разберите обе записи.")
    # Same-case pending sources require an explicit operator order decision.
    incoming = json.loads(row.normalized_json)["message"]
    siblings = []
    for other in db.query(m.IcsrMessage).filter(m.IcsrMessage.id != row.id, m.IcsrMessage.status == "review"):
        other_id = json.loads(other.normalized_json)["message"]
        if (incoming.get("worldwide_case_id") and incoming.get("worldwide_case_id") == other_id.get("worldwide_case_id")) or (incoming.get("local_sender_case_id") and incoming.get("local_sender_case_id") == other_id.get("local_sender_case_id") and incoming.get("sender_scope") == other_id.get("sender_scope")):
            siblings.append(other.id)
    if siblings and len(sequence_reason.strip()) < 10:
        raise ValueError("Есть другие сообщения этого случая. Укажите основание применения именно этого сообщения следующим; порядок файлов не используется.")
    preview = dict(nodes=nodes, mappings=decisions, diff=diff, reason=reason, sequence_reason=sequence_reason, pending_same_case=siblings)
    selected = []
    for item_id in sorted(set(attachment_item_ids or [])):
        item = db.get(m.E2BImportItem, item_id)
        if not item or item.batch_id != row.batch_id or item.is_deleted or item.file_role not in ('pdf','cioms','literature_pdf','other'):
            raise ValueError('Документ не принадлежит этому пакету или недоступен.')
        selected.append(dict(id=item.id, name=item.file_name, checksum=item.checksum_sha256))
    preview['documents'] = selected
    plan = m.IcsrReviewPlan(source_message_id=row.id, case_id=case.id if case else None, source_version=row.version,
        case_token=str(case.version) if case else "new", current_checksum=revisions.digest(revisions.snapshot(db, case.id)) if case else revisions.digest(None),
        incoming_checksum=revisions.digest(json.loads(row.normalized_json)), preview_json=dump(preview), author_id=audit_user_id(db))
    db.add(plan)
    db.commit()
    return plan


def apply(db, plan, choices):
    from app.e2b_import import E2BImportService, transaction_directory
    service = E2BImportService()
    lock_snapshot_inputs(db, ("Case", "IcsrMessage", "IcsrReviewPlan"))
    row = message(db, plan.source_message_id)
    if row.status in ('applied','duplicate'):
        return row.case_id
    if plan.applied_at or row.status != "review" or row.version != plan.source_version:
        raise StaleDataError("Сообщение изменилось; подготовьте новый просмотр.")
    parsed = json.loads(row.normalized_json)
    duplicates = db.query(m.IcsrMessage).filter_by(source_checksum=row.source_checksum,
        fragment_checksum=row.fragment_checksum, status='applied').all()
    if duplicates:
        cases = {d.case_id for d in duplicates}
        if len(cases) != 1:
            raise ValueError('Одинаковый исходник ранее отнесён к разным случаям; требуется разбор.')
        row.status, row.case_id = 'duplicate', cases.pop()
        row.decision_json = dump(dict(duplicate_of=[d.id for d in duplicates], plan_id=plan.id, clinical_changes=False))
        plan.applied_at, plan.decision_json = m.utcnow(), row.decision_json
        db.commit()
        return row.case_id
    case = existing(db, row)
    if (case.id if case else None) != plan.case_id or revisions.digest(revisions.snapshot(db, case.id) if case else None) != plan.current_checksum or revisions.digest(parsed) != plan.incoming_checksum:
        raise StaleDataError("Случай или входящие данные изменились; сопоставление нужно просмотреть заново.")
    preview = json.loads(plan.preview_json)
    required = {(n["key"], f) for n in preview["nodes"] if preview["mappings"][n["key"]] not in ("new", "ignore") for f in g.fields(n)}
    if {(k, f) for k, fs in choices.items() for f in fs} != required or any(v not in ("accept", "keep") for fs in choices.values() for v in fs.values()):
        raise ValueError("Для каждого поля сопоставленного повтора явно выберите входящее или текущее значение.")
    batch = db.get(m.E2BImportBatch, row.batch_id)
    if batch.status in ("cancelled", "not_pv_ars"):
        raise ValueError("Пакет отменён или выведен из обработки.")
    if any(i.is_blocking and i.item_id is None and i.issue_type not in ("multiple_cases_not_supported",) for i in batch.issues):
        raise ValueError("Пакет содержит блокирующую ошибку уровня архива.")
    if case:
        protect_record(db, m.Case, case.id, int(plan.case_token))
        assert_case_editable(case)
        revisions.create(db, case.id, "Состояние перед подтверждённым follow-up: " + preview["reason"])
    partner_id, _ = service.matching.find_partner(db, parsed)
    report = service.create_safety_report(db, batch, parsed, partner_id)
    request = service.create_incoming_request(db, batch, parsed, partner_id)
    if case is None:
        case = service.create_case(db, batch, parsed, partner_id, report.id)
        case.case_type = None  # ICH report type is held in the explicit report block
    report.case_id = case.id
    service.link_incoming_request(db, request, case, "follow_up" if plan.case_id else "created_from_message")
    mapped = {}
    for node in preview["nodes"]:
        target = preview["mappings"][node["key"]]
        if target == "ignore":
            continue
        cls = getattr(m, node["model"])
        new = target == "new"
        obj = cls() if new else db.get(cls, target)
        before = None if new else clinical_data.columns(obj)
        if hasattr(cls, "case_id"):
            obj.case_id = case.id
        if new:
            for field, linked in node["links"].items():
                setattr(obj, field, mapped[linked].id)
            if cls is m.CaseClinicalRecord:
                obj.kind, obj.interpretation_profile = node["kind"], "ich-clinical-v1"
                if obj.kind in clinical_data.SINGLETON_KINDS and db.query(cls).filter_by(case_id=case.id, kind=obj.kind,
                        reaction_id=obj.reaction_id, case_product_id=obj.case_product_id, is_deleted=False).first():
                    raise ValueError('Одиночный блок уже существует; сопоставьте его с текущим, а не добавляйте второй: '+obj.kind)
        else:
            for field, linked in node["links"].items():
                if getattr(obj, field) != mapped[linked].id:
                    raise ValueError("Сопоставление нарушает связь повтора; выберите согласованные родительские записи.")
        incoming_fields = g.fields(node)
        selected = incoming_fields if new else {f: v for f, v in incoming_fields.items() if choices[node["key"]][f] == "accept"}
        if cls is m.CaseClinicalRecord:
            current = {} if new else json.loads(obj.fields_json)
            current.update(selected)
            obj.fields_json = dump(current)
        else:
            for f, v in selected.items():
                setattr(obj, f, v)
            if cls is m.Reaction:
                from app.reaction_semantics import aggregate, CRITERIA
                obj.is_serious = aggregate([getattr(obj,k) for k in CRITERIA], obj.reported_seriousness)
            if new and cls is m.CaseProduct:
                obj.product_id, obj.product_match_status = service.matching.find_product(db, node["values"])
            origins = json.loads(obj.current_sources_json or '{}')
            origins.update({f: dict(origin='source_xml', import_source=node['source']['import_source'], confirmed_by=audit_user_id(db), plan_id=plan.id, source_fact={k:v for k,v in node['source'].items() if k not in ('xml','subobservations')}, reason=preview['reason']) for f in selected})
            if cls is m.Reaction:
                origins["is_serious"] = dict(origin="computed", rule="reaction_semantics.aggregate", inputs=list(CRITERIA)+["reported_seriousness"])
            if new and cls is m.CaseProduct:
                origins["product_id"] = dict(origin="catalog", match_status=obj.product_match_status, product_id=obj.product_id)
            obj.current_sources_json = dump(origins)
        if new:
            obj.source_facts_json = dump(node["source"])
            db.add(obj)
        # Later sources remain in immutable message/revision; never replace the original source facts.
        db.flush()
        mapped[node["key"]] = obj
        if cls is m.CaseClinicalRecord:
            clinical_data.check_links(db, obj)
            clinical_data.project(db, obj, set(selected))
        log_audit(db, entity_type=cls.__name__, entity_id=obj.id, case_id=case.id, action="create" if new else "update",
            user_id=audit_user_id(db), old_value=dump(before) if before else None, new_value=dump(clinical_data.columns(obj)),
            change_reason=preview["reason"], source_module="clinical_followup")
    # Extract only this message's documents. Other physical XML and sibling attachments are not assigned.
    with transaction_directory(db, batch, "embedded") as directory:
        service.save_attachments(db, batch, case, report, embedded_dir=directory,
                                 selected_item_id=row.item_id, selected_normalized=parsed,
                                 selected_attachment_ids=[i['id'] for i in preview.get('documents', [])])
    for obj in mapped.values():
        if not isinstance(obj, m.CaseClinicalRecord) or obj.kind not in ('source_document', 'literature'):
            continue
        fields = json.loads(obj.fields_json)
        code = 'C.4.r.2' if obj.kind == 'literature' else 'C.1.6.1.r.2'
        try:
            checksum = json.loads(fields.get(code, {}).get('raw') or '{}').get('sha256')
        except ValueError:
            checksum = None
        if checksum:
            attachments = db.query(m.Attachment).filter_by(case_id=case.id, related_object_id=row.item_id, checksum_sha256=checksum, is_deleted=False).all()
            if len(attachments) == 1:
                obj.attachment_id = attachments[0].id
                clinical_data.project(db, obj, set())
    tx = m.E2BTransmission(batch_id=batch.id, import_item_id=row.item_id, import_message_id=row.id, case_id=case.id,
        safety_report_id=report.id, incoming_request_id=request.id, xml_checksum_sha256=row.source_checksum, normalized_json=row.normalized_json)
    for f in ("message_identifier", "message_version", "worldwide_case_id", "local_sender_case_id", "receiver", "sender", "message_type", "followup_type"):
        setattr(tx, f, parsed["message"].get(f))
    db.add(tx)
    row.status, row.case_id, row.applied_at, row.applied_by = "applied", case.id, m.utcnow(), audit_user_id(db)
    row.decision_json = dump(dict(mappings=preview["mappings"], choices=choices, reason=preview["reason"], sequence_reason=preview["sequence_reason"], plan_id=plan.id))
    plan.applied_at, plan.decision_json = row.applied_at, row.decision_json
    db.flush()
    validation = revisions.validate(revisions.snapshot(db, case.id))
    report.is_valid_icsr = validation['minimum_icsr']
    for field, criterion in (('minimum_criteria_patient','patient'),('minimum_criteria_reporter','reporter'),('minimum_criteria_product','suspect_product'),('minimum_criteria_event','reaction')):
        setattr(report, field, not any(x['rule'] == 'MIN-' + criterion for x in validation['findings']))
    request.validity_status = "valid_icsr" if validation["minimum_icsr"] else "requires_review"
    for field, criterion in (("has_identifiable_patient","patient"),("has_identifiable_reporter","reporter"),("has_suspect_product","suspect_product"),("has_adverse_event","reaction")):
        setattr(request, field, not any(x["rule"] == "MIN-"+criterion for x in validation["findings"]))
    revisions.create(db, case.id, preview["reason"], source_message_id=row.id)
    remaining = db.query(m.IcsrMessage).filter_by(batch_id=batch.id).filter(m.IcsrMessage.status.notin_(['applied','duplicate'])).count()
    failed_files = db.query(m.E2BImportItem).filter_by(batch_id=batch.id, file_role="xml").filter(m.E2BImportItem.status.in_(["error","detected"])).count()
    batch.status = "partially_processed" if remaining or failed_files else "imported"
    db.commit()
    return case.id
