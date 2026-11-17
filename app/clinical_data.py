"""Case-owned clinical records and compatibility projections.

Only this writer updates the new clinical fields. Older patient/narrative rows
remain readable; no historic source is guessed when adopting a row for editing.
"""
import json
from datetime import date
from fastapi import HTTPException
from sqlalchemy import inspect
from app import models as m
from app.clinical_values import CONTRACT, ClinicalWrite, dump, manual_fields
from app.clinical_import import PARENT_KINDS
from app.crud import assert_case_editable, audit_user_id
from app.concurrency import protect_record, require_version
from app.audit import log_audit

DRUG_KINDS = {"drug_extra", "drug_additional", "pair_interval", "pair_last_interval"}
REACTION_KINDS = {"reaction_extra", "pair_interval", "pair_last_interval"}
SINGLETON_KINDS = {'patient','report','study','reaction_extra','drug_extra'}
PATIENT_FIELDS = {
    "patient": {"D.1": "patient_initials", "D.2.1": "birth_date_raw", "D.2.2a": "age_value",
                "D.2.2b": "age_unit", "D.2.3": "age_group", "D.3": "weight_kg", "D.4": "height_cm",
                "D.5": "sex", "D.7.2": "medical_history_text", "D.9.1": "death_date_raw",
                "local.patient_id": "patient_identifier", "local.pregnancy_status": "pregnancy_status"},
    "parent": {"D.10.1": "patient_initials", "D.10.2.1": "birth_date_raw", "D.10.2.2a": "age_value",
               "D.10.2.2b": "age_unit", "D.10.4": "weight_kg", "D.10.5": "height_cm", "D.10.6": "sex",
               "D.10.7.2": "medical_history_text"},
}
NARRATIVE_FIELDS = {"narrative": "H.1", "reporter_comment": "H.2", "sender_comment": "H.4", "summary": "H.5.r.1a"}
REACTION_FIELDS = {"E.i.1.1b": "term_language", "E.i.1.2": "verbatim_term", "E.i.4": "onset_date_raw",
                   "E.i.5": "end_date_raw", "E.i.8": "medically_confirmed", "E.i.9": "term_country_code"}


def columns(row):
    return {c.key: getattr(row, c.key) for c in inspect(type(row)).columns}


def serialize(row):
    result = columns(row)
    result["fields"] = json.loads(result.pop("fields_json"))
    result["source_facts"] = json.loads(result.pop("source_facts_json") or "null")
    result["reviewed_fields"] = json.loads(result.pop("reviewed_fields_json") or "[]")
    result["label"] = CONTRACT[row.kind]["label"]
    return json.loads(dump(result))


def check_owner(db, cls, row_id, case_id):
    row = db.get(cls, row_id) if row_id else None
    if not row or row.is_deleted or row.case_id != case_id:
        raise HTTPException(404, "Объект не принадлежит выбранному случаю.")
    return row


def check_links(db, row):
    kind = row.kind
    parent_kind = PARENT_KINDS.get(kind)
    if parent_kind:
        parent = check_owner(db, m.CaseClinicalRecord, row.parent_id, row.case_id)
        if parent.kind != parent_kind:
            raise ValueError("Неверный вид родительского клинического блока.")
    elif row.parent_id:
        raise ValueError("Для этого блока родительская запись не предусмотрена.")
    if kind in DRUG_KINDS:
        check_owner(db, m.CaseProduct, row.case_product_id, row.case_id)
    elif row.case_product_id:
        raise ValueError("Этот блок не относится к препарату.")
    if kind in REACTION_KINDS:
        check_owner(db, m.Reaction, row.reaction_id, row.case_id)
    elif row.reaction_id:
        raise ValueError("Этот блок не относится к реакции.")
    if row.attachment_id:
        check_owner(db, m.Attachment, row.attachment_id, row.case_id)


def project(db, row, changed):
    """Compatibility columns are derived from the current values, never vice versa."""
    values = json.loads(row.fields_json)
    raw = lambda code: values.get(code, {}).get("raw") if values.get(code, {}).get("state") not in ("invalid", "unsupported", "null_flavor", "ambiguous") else None
    if row.kind == 'report':
        case = db.get(m.Case, row.case_id)
        for code, field in (('C.1.4','initial_received_date'), ('C.1.5','latest_received_date')):
            if code in changed:
                parsed = values[code].get('value')
                try:
                    value = date.fromisoformat(parsed[:10]) if parsed and len(parsed) >= 10 else None
                except ValueError:
                    value = None
                setattr(case, field, value)
    if row.kind in PATIENT_FIELDS:
        patient = db.get(m.Patient, row.patient_id) if row.patient_id else None
        if patient is None:
            patient = m.Patient(case_id=row.case_id, patient_role=row.kind)
            db.add(patient)
            db.flush()
            row.patient_id = patient.id
        for code, field in PATIENT_FIELDS[row.kind].items():
            if code not in changed:
                continue
            value = raw(code)
            if field in ("age_value", "weight_kg", "height_cm"):
                value = float(value) if value is not None else None
            if field == "sex":
                value = {"1": "male", "2": "female"}.get(value, value)
            setattr(patient, field, value)
            if field in ("birth_date_raw", "death_date_raw"):
                normalized = values[code].get("value")
                try:
                    typed = date.fromisoformat(normalized[:10]) if normalized and len(normalized) >= 10 else None
                except ValueError:
                    typed = None
                setattr(patient, "date_of_birth" if field == "birth_date_raw" else "death_date", typed)
                setattr(patient, "date_of_birth_precision" if field == "birth_date_raw" else "death_date_precision", values[code].get("precision"))
    if row.kind == "reaction_extra":
        reaction = db.get(m.Reaction, row.reaction_id)
        origins = json.loads(reaction.current_sources_json or '{}')
        for code, field in REACTION_FIELDS.items():
            if code not in changed:
                continue
            value = values.get(code, {}).get("value") if field == "medically_confirmed" else raw(code)
            setattr(reaction, field, value)
            origins[field] = dict(origin='clinical_record', record_id=row.id, field=code, context=values[code].get('context'))
            if field in ("onset_date_raw", "end_date_raw"):
                normalized = values[code].get("value")
                try:
                    typed = date.fromisoformat(normalized[:10]) if normalized and len(normalized) >= 10 else None
                except ValueError:
                    typed = None
                setattr(reaction, field.removesuffix("_raw"), typed)
                setattr(reaction, field.removesuffix("_raw") + "_precision", values[code].get("precision"))
        if {"E.i.6a", "E.i.6b"} & set(changed):
            reaction.duration_text = " ".join(str(raw(c)) for c in ("E.i.6a", "E.i.6b") if raw(c) is not None) or None
        reaction.current_sources_json = dump(origins)
    if row.kind == 'drug_extra' and 'G.k.8' in changed:
        product = db.get(m.CaseProduct, row.case_product_id)
        product.action_taken = raw('G.k.8')
        origins = json.loads(product.current_sources_json or '{}')
        origins['action_taken'] = dict(origin='clinical_record', record_id=row.id, field='G.k.8', context=values['G.k.8'].get('context'))
        product.current_sources_json = dump(origins)
    if row.kind == "literature":
        ref = db.get(m.CaseLiteratureReference, row.literature_id) if row.literature_id else None
        if ref is None:
            ref = m.CaseLiteratureReference(case_id=row.case_id)
            db.add(ref)
            db.flush()
            row.literature_id = ref.id
        if "C.4.r.1" in changed:
            ref.citation = raw("C.4.r.1")
        ref.linked_attachment_id = row.attachment_id
    if row.kind in NARRATIVE_FIELDS:
        narrative = db.get(m.CaseNarrative, row.narrative_id) if row.narrative_id else None
        if narrative is None:
            narrative = m.CaseNarrative(case_id=row.case_id, narrative_type=row.kind)
            db.add(narrative)
            db.flush()
            row.narrative_id = narrative.id
        field = NARRATIVE_FIELDS[row.kind]
        if row.kind == 'narrative' and 'H.1' not in values and 'local.legacy_text' in values:
            field = 'local.legacy_text'
        if field in changed:
            narrative.narrative_text = raw(field)
        if "H.5.r.1b" in changed:
            narrative.language = raw("H.5.r.1b")
        if row.kind in ('narrative', 'sender_comment') and field in changed:
            db.flush()
            active = db.query(m.CaseClinicalRecord).filter_by(case_id=row.case_id, kind=row.kind, is_deleted=False).all()
            case = db.get(m.Case, row.case_id)
            setattr(case, 'narrative' if row.kind == 'narrative' else 'company_comment', raw(field) if len(active) == 1 else None)


def sync_reaction_edit(db, reaction, changes, author):
    """The existing reaction editor writes the same canonical facts, not a fork."""
    from app.clinical_values import ValueInput
    reverse = {v: k for k, v in REACTION_FIELDS.items()}
    reverse.update(onset_date="E.i.4", end_date="E.i.5")
    touched = {key: value for key, value in changes.items() if key in reverse}
    if not touched:
        return
    row = db.query(m.CaseClinicalRecord).filter_by(case_id=reaction.case_id, kind="reaction_extra", reaction_id=reaction.id, is_deleted=False).one_or_none()
    if row is None:
        row = m.CaseClinicalRecord(id=m.new_uuid(), case_id=reaction.case_id, kind="reaction_extra", reaction_id=reaction.id,
                                   fields_json="{}", interpretation_profile="manual-clinical-v1")
        db.add(row)
    inputs = {}
    for field, value in touched.items():
        if field in ("onset_date", "end_date") and field + "_raw" in touched:
            continue
        raw = value.isoformat().replace("-", "") if isinstance(value, date) else str(value).lower() if isinstance(value, bool) else value
        inputs[reverse[field]] = ValueInput(raw=raw)
    values = manual_fields(row.kind, inputs, author)
    merged = json.loads(row.fields_json)
    merged.update(values)
    row.fields_json = dump(merged)
    row.reviewed_fields_json = dump(sorted(set(json.loads(row.reviewed_fields_json or "[]")) | set(values)))
    project(db, row, set(values))


def adopt_patient(db, patient, author):
    """Compatibility create form: preserve its entered values in the same owner."""
    from app.clinical_values import ValueInput
    if db.query(m.CaseClinicalRecord).filter_by(case_id=patient.case_id, kind='patient', is_deleted=False).first():
        raise ValueError('Пациент уже есть. Используйте исправление клинического блока с контролем версии.')
    if patient.patient_role == 'parent':
        raise ValueError('Родителя добавляют в клинических блоках с явной ссылкой на пациента.')
    inputs = {}
    for code, field in PATIENT_FIELDS['patient'].items():
        value = getattr(patient, field)
        if value is not None:
            inputs[code] = ValueInput(raw=str(value))
    if patient.date_of_birth and 'D.2.1' not in inputs:
        inputs['D.2.1'] = ValueInput(raw=patient.date_of_birth.strftime('%Y%m%d'))
    if patient.patient_identifier:
        inputs['local.patient_id'] = ValueInput(raw=patient.patient_identifier)
    if patient.pregnancy_status:
        inputs['local.pregnancy_status'] = ValueInput(raw=patient.pregnancy_status)
    row=m.CaseClinicalRecord(case_id=patient.case_id, kind='patient', patient_id=patient.id,
        fields_json=dump(manual_fields('patient', inputs, author)), interpretation_profile='manual-clinical-v1',
        source_facts_json=dump(dict(origin='manual_patient_form', author_id=author, values=columns(patient))))
    db.add(row)


def sync_product_action(db, product, author):
    from app.clinical_values import ValueInput
    row = db.query(m.CaseClinicalRecord).filter_by(case_id=product.case_id, kind='drug_extra', case_product_id=product.id, is_deleted=False).one_or_none()
    if row is None:
        row = m.CaseClinicalRecord(id=m.new_uuid(), case_id=product.case_id, kind='drug_extra', case_product_id=product.id,
            fields_json='{}', interpretation_profile='manual-clinical-v1')
        db.add(row)
    fields=json.loads(row.fields_json)
    fields.update(manual_fields('drug_extra', {'G.k.8':ValueInput(raw=product.action_taken)}, author))
    row.fields_json=dump(fields)
    project(db,row,{'G.k.8'})


def save(db, case_id, kind, payload: ClinicalWrite, expected_case_version, *, row_id=None, expected_version=None, archive=False):
    if kind not in CONTRACT:
        raise HTTPException(404, "Неизвестный клинический блок.")
    if len(payload.change_reason.strip()) < 10:
        raise ValueError("Требуется содержательное основание не менее 10 символов.")
    case = protect_record(db, m.Case, case_id, expected_case_version)
    assert_case_editable(case)
    author = audit_user_id(db)
    values = manual_fields(kind, payload.fields, author)
    for value in values.values():
        value.setdefault("context", {})["change_reason"] = payload.change_reason
    row = check_owner(db, m.CaseClinicalRecord, row_id, case_id) if row_id else None
    if row and row.kind != kind:
        raise HTTPException(404, "Неверный тип записи.")
    if row:
        require_version(row, expected_version)
    elif archive:
        raise ValueError("Архивировать можно только сохранённую запись.")
    before = serialize(row) if row else None
    if row is None:
        row = m.CaseClinicalRecord(id=m.new_uuid(), case_id=case_id, kind=kind, fields_json="{}", interpretation_profile="manual-clinical-v1")
        db.add(row)
    for field in ("parent_id", "case_product_id", "reaction_id", "attachment_id"):
        if field in payload.model_fields_set:
            setattr(row, field, getattr(payload, field))
    check_links(db, row)
    if row.kind in SINGLETON_KINDS and not archive:
        query = db.query(m.CaseClinicalRecord).filter_by(case_id=case_id, kind=row.kind, is_deleted=False,
                    reaction_id=row.reaction_id, case_product_id=row.case_product_id)
        if row.id:
            query = query.filter(m.CaseClinicalRecord.id != row.id)
        with db.no_autoflush:
            if query.first():
                raise ValueError('Для этого повтора уже есть блок характеристик. Исправьте существующий блок.')
    if archive:
        children = db.query(m.CaseClinicalRecord).filter_by(parent_id=row.id, is_deleted=False).count()
        if children:
            raise ValueError("Сначала разберите активные дочерние записи; они не архивируются скрыто.")
        row.is_deleted = True
        row.deleted_at = m.utcnow()
        row.deleted_by = author
        row.delete_reason = payload.change_reason
        for cls, key in ((m.Patient, row.patient_id), (m.CaseLiteratureReference, row.literature_id), (m.CaseNarrative, row.narrative_id)):
            if key:
                target = db.get(cls, key)
                target.is_deleted = True
                target.deleted_at, target.deleted_by, target.delete_reason = row.deleted_at, author, payload.change_reason
        if row.kind in ('narrative', 'sender_comment'):
            setattr(case, 'narrative' if row.kind == 'narrative' else 'company_comment', None)
        if row.kind == 'reaction_extra':
            reaction = db.get(m.Reaction, row.reaction_id)
            for field in (*REACTION_FIELDS.values(), 'onset_date','end_date','onset_date_precision','end_date_precision','duration_text'):
                setattr(reaction, field, None)
        if row.kind == 'drug_extra':
            db.get(m.CaseProduct, row.case_product_id).action_taken = None
        if row.kind == 'report':
            case.initial_received_date = case.latest_received_date = None
    else:
        current = json.loads(row.fields_json)
        current.update(values)
        row.fields_json = dump(current)
        row.reviewed_fields_json = dump(sorted(set(json.loads(row.reviewed_fields_json or "[]")) | set(values)))
        project(db, row, set(values))
    db.flush()
    log_audit(db, entity_type="CaseClinicalRecord", entity_id=row.id, case_id=case_id,
              action="soft_delete" if archive else "update" if before else "create", user_id=author,
              old_value=dump(before) if before else None, new_value=dump(serialize(row)),
              change_reason=payload.change_reason, source_module="clinical")
    db.commit()
    db.refresh(row)
    return row
