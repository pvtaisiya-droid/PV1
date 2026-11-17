"""Immutable, self-contained internal clinical snapshots (not E2B versions)."""
import hashlib
import json
import re
from sqlalchemy import event, func
from app import models as m
from app.clinical_data import columns
from app.clinical_values import dump, meaningful
from app.concurrency import protect_record
from app.crud import assert_case_editable, audit_user_id

SCHEMA = "pv1-clinical-1"
DIRECT = ("Patient", "Reaction", "CaseProduct", "CaseIdentifier", "CaseClinicalRecord",
          "CaseNarrative", "CaseLiteratureReference", "Attachment", "CaseIdentityDecision", "E2BTransmission", "AuditTrail", "SafetyReport", "IncomingRequestCaseLink")
CHILDREN = ("CaseDrugDosage", "CaseDrugIndication", "CaseDrugSubstance", "CaseDrugRechallenge", "CaseProductReactionAssessment")


def snapshot(db, case_id):
    db.flush()
    case = db.get(m.Case, case_id)
    data = {"schema": SCHEMA, "case": columns(case), "records": {}}
    for name in DIRECT:
        cls = getattr(m, name)
        data["records"][name] = [columns(r) for r in db.query(cls).filter_by(case_id=case_id).order_by(cls.id)]
    products = [r["id"] for r in data["records"]["CaseProduct"]]
    for name in CHILDREN:
        cls = getattr(m, name)
        data["records"][name] = [columns(r) for r in db.query(cls).filter(cls.case_product_id.in_(products)).order_by(cls.id)]
    request_ids = [r['incoming_request_id'] for r in data['records']['IncomingRequestCaseLink']]
    data['records']['IncomingRequest'] = [columns(r) for r in db.query(m.IncomingRequest).filter(m.IncomingRequest.id.in_(request_ids)).order_by(m.IncomingRequest.id)]
    data['records']['IcsrMessage'] = [columns(r) for r in db.query(m.IcsrMessage).filter_by(case_id=case_id).order_by(m.IcsrMessage.id)]
    return json.loads(dump(data))


def digest(data):
    return hashlib.sha256(dump(data).encode("utf-8")).hexdigest()


def validate(data):
    findings = []
    def add(level, rule, path, message, blocking=False):
        findings.append(dict(level=level, rule=rule, version="1", source="PV1 internal clinical contract v1; ICH E2B(R3) IG 5.03 C/D/E/G",
                             path=path, message=message, blocking=blocking))
    rows = data["records"]
    active = lambda name: [r for r in rows[name] if not r.get("is_deleted")]
    clinical = active("CaseClinicalRecord")
    def has(kind):
        keys = {'patient': {'D.1','D.1.1.1','D.1.1.2','D.1.1.3','D.1.1.4','D.2.1','D.2.2a','local.patient_id'},
                'reporter': {'C.2.r.1.2','C.2.r.1.4','C.2.r.2.1','C.2.r.2.7','local.name_line','local.contacts'}}[kind]
        def identified(key, value):
            if key == 'local.contacts':
                return any(meaningful(dict(state='received_unvalidated', value=c.get('value'))) for c in (value.get('value') or []) if isinstance(c, dict))
            return meaningful(value)
        return any(r['kind'] == kind and any(k in keys and identified(k,v) for k,v in json.loads(r['fields_json']).items()) for r in clinical)
    nonempty = lambda value: bool(value and str(value).strip().casefold() not in ("unknown", "unknown reaction", "неизвестно", "не указано", "[исходный текст реакции отсутствует]"))
    checks = {"patient": has("patient") or any(nonempty(r.get("patient_initials") or r.get("patient_identifier")) for r in active("Patient")),
              "reporter": has("reporter"),
              "reaction": any(nonempty(r.get("reported_term")) or bool(re.fullmatch(r"[0-9]{8}", r.get("meddra_llt_code") or "")) for r in active("Reaction")),
              "suspect_product": any(r.get("drug_role") in ("suspect", "interacting") and nonempty(r.get("reported_product_name")) for r in active("CaseProduct"))}
    for name, ok in checks.items():
        if not ok:
            add("minimum_icsr", "MIN-" + name, name, "Минимальный критерий не подтверждён содержательными данными.")
    ids = {r["id"] for r in clinical}
    product_ids = {r["id"] for r in active("CaseProduct")}
    reaction_ids = {r["id"] for r in active("Reaction")}
    for r in clinical:
        path = f"clinical/{r['kind']}/{r['id']}"
        if r.get("interpretation_profile") == "legacy_unverified":
            add("values", "LEGACY", path, "Историческая интерпретация не подтверждена; источник сохранён, нужна сверка перед представлением.")
        for field, allowed in (("parent_id", ids), ("case_product_id", product_ids), ("reaction_id", reaction_ids)):
            if r.get(field) and r[field] not in allowed:
                add("structure", "LINK", path + "/" + field, "Связь ведёт на отсутствующую или архивную запись.", True)
        for key, v in json.loads(r["fields_json"]).items():
            if v.get("state") in ("invalid", "ambiguous", "unsupported"):
                add("values", "VALUE", path + "/" + key, "Значение требует разбора: " + v["state"])
        from app.clinical_import import PARENT_KINDS
        parent_kind = PARENT_KINDS.get(r['kind'])
        parent = next((p for p in clinical if p['id']==r.get('parent_id')), None)
        if parent_kind and (not parent or parent['kind'] != parent_kind):
            legacy = r['interpretation_profile'] == 'legacy_unverified'
            add('values' if legacy else 'structure', 'PARENT-KIND', path+'/parent_id',
                'Связь с пациентом/родителем/исследованием требует подтверждения.', not legacy)
        values = json.loads(r['fields_json'])
        for start, end in (('C.1.4','C.1.5'),('E.i.4','E.i.5'),('D.7.1.r.2','D.7.1.r.4'),('D.10.7.1.r.2','D.10.7.1.r.4')):
            a,b=values.get(start,{}),values.get(end,{})
            if a.get('precision')==b.get('precision')=='day' and a.get('value') and b.get('value') and a['value']>b['value']:
                add('values','DATE-ORDER',path+'/'+end,'Конец/последующее получение раньше начальной даты; требуется сверка.')
    from app.reaction_semantics import CRITERIA
    for r in active("Reaction"):
        if r.get("reported_seriousness") is False and any(r.get(k) is True for k in CRITERIA):
            add("values", "SERIOUSNESS-CONFLICT", "Reaction/"+r["id"], "Исходная общая оценка противоречит выбранным критериям серьёзности.")
    for name in CHILDREN:
        for r in active(name):
            if r["case_product_id"] not in product_ids or (r.get("reaction_id") and r["reaction_id"] not in reaction_ids):
                add("structure", "DRUG-LINK", name + "/" + r["id"], "Нарушена активная связь препарат–реакция.", True)
    for r in active("Attachment"):
        if not r.get("checksum_sha256"):
            add("revision", "DOCUMENT-HASH", "Attachment/" + r["id"], "Для документа нет устойчивой контрольной суммы.", True)
    return dict(minimum_icsr=all(checks.values()), structurally_valid=not any(x["level"] == "structure" for x in findings),
                revision_ready=not any(x["blocking"] for x in findings), recipient_profile="not_validated",
                dictionary_validation="not_performed", findings=findings)


def create(db, case_id, reason, *, source_message_id=None, expected_case_version=None, author_id=None):
    if len(reason.strip()) < 10:
        raise ValueError("Для ревизии требуется содержательное основание.")
    case = protect_record(db, m.Case, case_id, expected_case_version) if expected_case_version is not None else db.get(m.Case, case_id)
    assert_case_editable(case)
    db.flush()
    data = snapshot(db, case_id)
    from pathlib import Path
    for attachment in data['records']['Attachment']:
        if attachment.get('is_deleted'):
            continue
        from app.recovery_paths import resolve_storage
        path = resolve_storage(attachment['storage_path']) if attachment.get('storage_path') else None
        if path and path.is_file():
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if attachment.get('checksum_sha256') and actual != attachment['checksum_sha256']:
                raise ValueError('Содержимое документа изменилось: ' + attachment['id'])
            attachment['checksum_sha256'] = actual
            attachment['snapshot_checksum_origin'] = 'verified_stored_file'
    validation = validate(data)
    if not validation["revision_ready"]:
        raise ValueError("Сначала исправьте связи и контрольные суммы документов; черновик остаётся доступен.")
    number = (db.query(func.max(m.IcsrRevision.revision_number)).filter_by(case_id=case_id).scalar() or 0) + 1
    row = m.IcsrRevision(case_id=case_id, revision_number=number, schema_version=SCHEMA,
                        snapshot_json=dump(data), checksum_sha256=digest(data), validation_json=dump(validation),
                        source_message_id=source_message_id, author_id=author_id or audit_user_id(db), reason=reason)
    db.add(row)
    db.flush()
    return row


def immutable(mapper, connection, target):
    raise ValueError("Содержательная ревизия неизменяема; создайте новую.")


event.listen(m.IcsrRevision, "before_update", immutable)
event.listen(m.IcsrRevision, "before_delete", immutable)


def immutable_message_source(mapper, connection, target):
    from sqlalchemy import inspect
    for field in ('source_xml','source_checksum','fragment_checksum','normalized_json','item_id','batch_id','ordinal'):
        history = inspect(target).attrs[field].history
        if history.has_changes():
            raise ValueError('Исходник отдельного сообщения неизменяем.')


event.listen(m.IcsrMessage, 'before_update', immutable_message_source)
event.listen(m.IcsrMessage, 'before_delete', immutable)
