"""Keep explicit short-card edits in the canonical clinical records.

Full names and contact details stay local; they are never guessed to be D.1
initials or an E2B primary reporter. No revision or source XML is overwritten.
"""
import json

from app import models as m, clinical_data
from app.clinical_values import ValueInput, manual_fields, dump
from app.crud import audit_user_id
from app.audit import log_audit

AGE_UNITS = {"years": "a", "months": "mo", "weeks": "wk", "days": "d"}


def sync_seriousness(db, case, old_case):
    if not case.e2b_identity_profile or (case.seriousness == old_case['seriousness'] and
            (case.seriousness_criteria or []) == (old_case['seriousness_criteria'] or [])):
        return
    reactions = [r for r in case.reactions if not r.is_deleted]
    if len(reactions) > 1:
        raise ValueError('В случае несколько реакций. Укажите критерии серьёзности у каждой реакции в подробной карточке.')
    if not reactions:
        return
    from app.ae_card import CRITERIA, REACTION_FIELDS
    from app.reaction_semantics import aggregate
    from app.crud import log_field_changes
    row = reactions[0]
    changes = {field: True if criterion in (case.seriousness_criteria or []) else None
               for criterion, field in zip(CRITERIA, REACTION_FIELDS)}
    changes['reported_seriousness'] = case.seriousness == 'serious'
    changes['is_serious'] = aggregate([changes[k] for k in REACTION_FIELDS], changes['reported_seriousness'])
    before = {key: getattr(row, key) for key in changes}
    for key, value in changes.items():
        setattr(row, key, value)
    row.reviewed_fields_json = dump(sorted(set(json.loads(row.reviewed_fields_json or '[]')) | set(changes)))
    origins = json.loads(row.current_sources_json or '{}')
    origins.update({key: dict(origin='manual', author_id=audit_user_id(db),
        confirmed_at=m.utcnow().isoformat(), reason='Изменение через краткую карточку НЯ') for key in changes})
    row.current_sources_json = dump(origins)
    log_field_changes(db, entity_type='Reaction', entity_id=row.id, case_id=case.id,
        old_values=before, new_values=changes)


def sync_block(db, case, kind, fields, *, patient_id=None):
    if not fields:
        return
    query = db.query(m.CaseClinicalRecord).filter_by(case_id=case.id, kind=kind, is_deleted=False)
    if patient_id:
        query = query.filter_by(patient_id=patient_id)
    rows = query.all()
    if len(rows) > 1:
        raise ValueError("Есть несколько клинических блоков этого вида. Уточните их в подробной карточке.")
    row = rows[0] if rows else None
    before = clinical_data.serialize(row) if row else None
    if row is None:
        row = m.CaseClinicalRecord(id=m.new_uuid(), case_id=case.id, kind=kind,
            patient_id=patient_id, interpretation_profile="manual-clinical-v1", fields_json="{}")
        db.add(row)
    values = manual_fields(kind, {key: ValueInput(raw=value) for key, value in fields.items()}, audit_user_id(db))
    for value in values.values():
        value["context"]["change_reason"] = "Изменение через краткую карточку НЯ"
    current = json.loads(row.fields_json)
    current.update(values)
    row.fields_json = dump(current)
    row.reviewed_fields_json = dump(sorted(set(json.loads(row.reviewed_fields_json or "[]")) | set(values)))
    clinical_data.check_links(db, row)
    clinical_data.project(db, row, set(values))
    db.flush()
    log_audit(db, entity_type="CaseClinicalRecord", entity_id=row.id, case_id=case.id,
        action="update" if before else "create", user_id=audit_user_id(db),
        old_value=dump(before) if before else None, new_value=dump(clinical_data.serialize(row)),
        source_module="short_card_clinical", change_reason="Изменение через краткую карточку НЯ")


def sync_short_card(db, case, patient, old_patient, old_case):
    sync_seriousness(db, case, old_case)
    if patient:
        fields = {}
        for attr, code in (("sex", "D.5"), ("age_value", "D.2.2a"),
                           ("age_unit", "D.2.2b"), ("medical_history_text", "D.7.2")):
            value = getattr(patient, attr)
            if value == old_patient.get(attr):
                continue
            if attr == 'age_unit' and AGE_UNITS.get(value, value) == AGE_UNITS.get(old_patient.get(attr), old_patient.get(attr)):
                continue
            if attr == "sex":
                value = {"female": "2", "male": "1"}.get(value, value)
            elif attr == "age_unit":
                value = AGE_UNITS.get(value, value)
            fields[code] = str(value) if value is not None else None
        age_text = patient.age_text
        sync_block(db, case, "patient", fields, patient_id=patient.id)
        patient.age_text = age_text
    if case.narrative != old_case.get("narrative"):
        sync_block(db, case, "narrative", {"H.1": case.narrative})
