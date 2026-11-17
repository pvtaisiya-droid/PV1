"""Persistence and readback for case drug blocks; source facts are never edited."""
import json
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
from sqlalchemy import inspect
from fastapi import HTTPException
from app.e2b_r3_identity import source_date


def dump(value):
    return json.dumps(value,ensure_ascii=False,default=str,sort_keys=True)


def provenance(row):
    result=dict(profile=row.interpretation_profile or "legacy_unverified",
        source=json.loads(row.source_facts_json or "null"),reviewed_fields=json.loads(row.reviewed_fields_json or "[]"),
        dictionary_validation="not_performed")
    result['current_field_sources'] = json.loads(row.current_sources_json or '{}')
    if hasattr(row,"start_raw"):
        result["current_dates"]={}
        for side in ("start","end"):
            raw=getattr(row,side+"_raw"); nf=getattr(row,side+"_null_flavor")
            attrs={}
            if raw is not None:attrs["value"]=raw
            if nf is not None:attrs["nullFlavor"]=nf
            result["current_dates"][side]=source_date([ET.Element(side,attrs)] if attrs else [],side,"G.k.4.r")
    result["numeric_states"]={}
    for f in ("dose_value","dose_numerator_value","dose_denominator_value","interval_value","duration_value","strength_value","denominator_value"):
        if not hasattr(row,f):continue
        v=getattr(row,f)
        try: state="missing" if v is None else "numeric_unvalidated_unit" if Decimal(v).is_finite() else "invalid"
        except InvalidOperation:state="invalid"
        result["numeric_states"][f]=state
    if hasattr(row,"rechallenge_code"):
        from app.drug_import import RECHALLENGE, RECHALLENGES
        supported=row.code_system==RECHALLENGE and row.code_version in (None,"1.0") and not row.null_flavor
        result["rechallenge_meaning"]=RECHALLENGES.get(row.rechallenge_code) if supported else None
    return result


def model_for(kind):
    from app import models as m
    return {"product":m.CaseProduct,"dosage":m.CaseDrugDosage,"indication":m.CaseDrugIndication,
        "substance":m.CaseDrugSubstance,"assessment":m.CaseProductReactionAssessment,"rechallenge":m.CaseDrugRechallenge}[kind]


def serialize(row):
    values={c.key:getattr(row,c.key) for c in inspect(type(row)).columns if c.key not in
        ("source_facts_json","reviewed_fields_json","coded_identifiers_json")}
    values["drug_provenance"]=provenance(row)
    if hasattr(row,"coded_identifiers"):values["coded_identifiers"]=row.coded_identifiers
    return values


def read_product(db, product):
    result=serialize(product)
    for kind,label in (("dosage","dosages"),("indication","indications"),("substance","substances"),
                       ("assessment","assessments"),("rechallenge","rechallenges")):
        rows=db.query(model_for(kind)).filter_by(case_product_id=product.id,is_deleted=False).order_by(model_for(kind).created_at).all()
        result[label]=[serialize(r) for r in rows]
    result["legacy_scalar_policy"]="read-only historical snapshot; current dosing/indications are child rows"
    return result


def get_parent(db, case_id, product_id):
    from app import models as m
    row=db.get(m.CaseProduct,product_id)
    if not row or row.is_deleted or row.case_id!=case_id:
        raise HTTPException(404,"Product does not belong to this case")
    return row


def save(db, *, case_id, kind, payload, expected_case_version, product_id=None, row_id=None,
         expected_parent_version=None, expected_version=None, archive=False):
    from app import models as m
    from app.crud import assert_case_editable, audit_user_id
    from app.concurrency import protect_record, require_version
    from app.audit import log_audit
    case=protect_record(db,m.Case,case_id,expected_case_version)
    assert_case_editable(case)
    parent=get_parent(db,case_id,product_id) if product_id else None
    if kind!="product" and parent is None:raise HTTPException(404,"Parent product required")
    if parent is not None:
        require_version(parent,expected_parent_version)
        parent.version+=1
    model=model_for(kind)
    row=db.get(model,row_id) if row_id else None
    if row_id:
        if (not row or row.is_deleted or (kind=="product" and row.case_id!=case_id)
                or (kind!="product" and row.case_product_id!=product_id)):
            raise HTTPException(404,"Record does not belong to the selected case/product")
        require_version(row,expected_version)
    data=payload.model_dump(exclude_unset=True)
    reason=data.pop("change_reason",None)
    if row and (not reason or len(reason.strip())<10):raise ValueError("Для правки требуется содержательное основание (не менее 10 символов).")
    if archive and (row is None or kind=="product"):
        raise ValueError("Архивирование здесь доступно только для существующей дочерней записи.")
    if kind in ("assessment","rechallenge"):
        reaction_id=data.get("reaction_id",row.reaction_id if row else None)
        reaction=db.get(m.Reaction,reaction_id) if reaction_id else None
        if not reaction or reaction.is_deleted or reaction.case_id!=case_id:
            raise ValueError("Реакция должна принадлежать тому же случаю.")
        if row and reaction_id!=row.reaction_id:
            raise ValueError("Переназначение существующей пары запрещено; создайте отдельную запись с основанием.")
        if kind=="assessment" and not archive:
            if row and row.causality_method in ("who_umc","naranjo"):
                raise ValueError("Расчётную оценку не изменяют как свободный текст. Создайте новую оценку; прежняя сохранится.")
            data["causality_method"]="manual_text"
    if kind=="product":
        if "drug_role" in data and data["drug_role"] is None:data["drug_role"]="unknown"
        if not row:data.setdefault("drug_role","unknown")
        if "coded_identifiers" in data:data["coded_identifiers_json"]=dump(data.pop("coded_identifiers") or [])
    if kind in ("product","substance"):
        field="product_id" if kind=="product" else "substance_id"
        if data.get(field):
            catalog=db.get(m.Product if kind=="product" else m.Substance,data[field])
            if not catalog or catalog.is_deleted:raise ValueError("Запись каталога недоступна.")
        if field in data:
            data["product_match_status" if kind=="product" else "match_status"]="manual_link_unvalidated" if data[field] else "not_linked"
    before=serialize(row) if row else None
    if row is None:
        row=model(**({"case_id":case_id} if kind=="product" else {"case_product_id":product_id}))
        if kind=="assessment":row.created_by_user_id=audit_user_id(db)
        db.add(row)
    for k,v in data.items():setattr(row,k,v)
    if archive:
        row.is_deleted=True;row.deleted_by=audit_user_id(db);row.deleted_at=m.utcnow();row.delete_reason=reason
    reviewed=set(json.loads(row.reviewed_fields_json or "[]"))|set(data)
    row.reviewed_fields_json=dump(sorted(reviewed))
    origins=json.loads(row.current_sources_json or '{}')
    origins.update({k:dict(origin='manual',author_id=audit_user_id(db),confirmed_at=m.utcnow().isoformat(),reason=reason) for k in data})
    row.current_sources_json=dump(origins)
    if before is None:row.interpretation_profile="manual-drug-v1"
    db.flush()
    if kind=='product' and 'action_taken' in data:
        from app.clinical_data import sync_product_action
        sync_product_action(db,row,audit_user_id(db))
    log_audit(db,entity_type=type(row).__name__,entity_id=row.id,case_id=case_id,action="soft_delete" if archive else "update" if before else "create",
        old_value=dump(before) if before else None,new_value=dump(serialize(row)),change_reason=reason,
        user_id=audit_user_id(db),source_module="drug_block")
    db.commit();db.refresh(row)
    return row


def promote_single(db, product, origin="legacy_input"):
    """One-time compatibility ingestion, never a second editable current regimen."""
    from app import models as m
    mapping=dict(dose_value="dose_value",dose_unit="dose_unit",route="route_text",frequency="frequency_text",
        duration_text="dosage_text",dosage_form="dosage_form_text",batch_lot_number="batch_lot_number")
    fields=[*mapping,"therapy_start_date","therapy_start_date_raw","therapy_start_date_precision",
        "therapy_end_date","therapy_end_date_raw","therapy_end_date_precision"]
    old={f:getattr(product,f) for f in fields}
    if any(v is not None and v!="" for v in old.values()) and not product.dosages:
        data={new:old[f] for f,new in mapping.items()}
        for side in ("start","end"):
            raw=old[f"therapy_{side}_date_raw"]; value=old[f"therapy_{side}_date"]
            data[side+"_raw"]=raw or (value.strftime("%Y%m%d") if value else None)
        db.add(m.CaseDrugDosage(case_product_id=product.id,**data,interpretation_profile=origin,
            source_facts_json=dump(dict(origin=origin,values=old))))
    if (product.indication_text or product.indication_meddra_pt_code) and not product.indications:
        db.add(m.CaseDrugIndication(case_product_id=product.id,reported_text=product.indication_text,
            legacy_meddra_pt_code=product.indication_meddra_pt_code,interpretation_profile=origin,
            source_facts_json=dump(dict(origin=origin,indication_text=product.indication_text,
                indication_meddra_pt_code=product.indication_meddra_pt_code))))


def import_children(db, product, data, source_ref, reaction_map):
    from app import models as m
    from app.drug_import import PROFILE
    product.interpretation_profile=PROFILE
    product.coded_identifiers_json=dump(data.get("coded_identifiers",[]))
    product.source_facts_json=dump({**data.get("source_facts",{}),"import_source":source_ref})
    for kind,label in (("dosage","dosages"),("indication","indications"),("substance","substances"),
                       ("assessment","assessments"),("rechallenge","rechallenges")):
        for item in data.get(label,[]):
            values=dict(item);facts=values.pop("source_facts",{})
            if kind in ("assessment","rechallenge"):
                ref=values.pop("reaction_ref")
                if ref not in reaction_map:raise ValueError("Не удалось разрешить исходную связь препарат–реакция; импорт остановлен.")
                values["reaction_id"]=reaction_map[ref]
            if kind=="assessment":values["causality_method"]="imported_text"
            if kind=="substance":values.update(match_status="not_checked",import_batch_id=product.import_batch_id)
            row=model_for(kind)(case_product_id=product.id,**values,interpretation_profile=PROFILE,
                source_facts_json=dump({**facts,"import_source":source_ref,"product_repeat_index":data.get("source_facts",{}).get("repeat_index")}))
            db.add(row)
