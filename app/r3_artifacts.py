"""Transactional local preparation and verified downloads, without transport."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import event
from app import models as m
from app.r3_export import export_revision, dump
from app.r3_profile import profile, sha, MAX_XML_BYTES
from app.config import get_settings
from app.concurrency import lock_snapshot_inputs
from app.crud import audit_user_id
from app.audit import log_audit
from app.unit_of_work import register_new_file

class PrepareRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    idempotency_key: UUID
    expected_revision_checksum: str = Field(pattern=r'^[0-9a-f]{64}$')
    sender_id: str = Field(min_length=1,max_length=100)
    receiver_id: str = Field(min_length=1,max_length=100)
    batch_id: str | None = Field(default=None,min_length=1,max_length=100)
    message_id: str | None = Field(default=None,min_length=1,max_length=100)
    purpose: Literal['local_test','manual_preparation']
    scenario: Literal['initial','followup']
    previous_artifact_id: str | None = None
    reason: str = Field(min_length=10,max_length=2000)

    @field_validator('sender_id','receiver_id','batch_id','message_id','reason')
    @classmethod
    def meaningful(cls,value):
        if value is not None and (not value.strip() or any(ord(c)<32 for c in value)):
            raise ValueError('Пустые и управляющие символы недопустимы.')
        return value  # do not casefold/trim significant identifiers

def revision(db,case_id,revision_id):
    case=db.get(m.Case,case_id)
    row=db.get(m.IcsrRevision,revision_id)
    if not case or case.is_deleted or not row or row.case_id!=case_id:
        raise HTTPException(404,'Ревизия не принадлежит доступному случаю.')
    return row

def artifact(db,case_id,artifact_id):
    row=db.get(m.IcsrExportArtifact,artifact_id)
    if not row or row.case_id!=case_id:
        raise HTTPException(404,'Артефакт не принадлежит этому случаю.')
    revision(db,case_id,row.revision_id)
    return row

def read(row):
    return dict(id=row.id,case_id=row.case_id,revision_id=row.revision_id,revision_checksum=row.revision_checksum,
        status='Локальная проверка пройдена' if row.locally_valid else 'Сформирован — обнаружены ошибки',
        formed=True,locally_valid=row.locally_valid,xsd_valid=row.xsd_valid,xml_sha256=row.xml_sha256,byte_length=row.byte_length,
        profile=json.loads(row.profile_json),envelope=json.loads(row.envelope_json),validation=json.loads(row.validation_json),
        created_at=row.created_at,author_id=row.author_id,reason=row.reason,rzn_compatibility='not_confirmed',
        download_url=f'/api/cases/{row.case_id}/r3-artifacts/{row.id}/download' if row.locally_valid else None)

def verified_bytes(row):
    if not row.locally_valid:
        raise HTTPException(409,'Артефакт с блокирующими ошибками недоступен как готовая ручная выгрузка.')
    root=(get_settings().storage_root/'r3').resolve()
    from app.recovery_paths import resolve_storage
    path=resolve_storage(row.storage_path)
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size!=row.byte_length or row.byte_length>MAX_XML_BYTES:
        raise HTTPException(409,'Сохранённый файл отсутствует или нарушена его целостность.')
    raw=path.read_bytes()
    if sha(raw)!=row.xml_sha256:
        raise HTTPException(409,'Контрольная сумма сохранённого XML не совпадает. Повторная сериализация вместо скачивания запрещена.')
    return raw

def prepare(db,case_id,revision_id,payload):
    lock_snapshot_inputs(db,('IcsrRevision','IcsrExportArtifact'))
    source=revision(db,case_id,revision_id)
    requested=payload.model_dump(mode='json')
    request_hash=sha(dump(dict(case_id=case_id,revision_id=revision_id,parameters=requested)).encode())
    prior=db.query(m.IcsrExportArtifact).filter_by(idempotency_key=str(payload.idempotency_key)).one_or_none()
    if prior:
        if prior.request_checksum!=request_hash or prior.case_id!=case_id or prior.revision_id!=revision_id:
            raise HTTPException(409,'Ключ повторной подготовки уже относится к другим параметрам. Откройте новую форму.')
        return prior
    if payload.expected_revision_checksum!=source.checksum_sha256:
        raise HTTPException(409,'Контрольная сумма выбранной ревизии изменилась; подготовка остановлена.')
    previous=None
    if payload.scenario=='followup':
        if not payload.previous_artifact_id:raise ValueError('Для follow-up выберите предшествующий локальный артефакт; это не доказательство его подачи.')
        previous=artifact(db,case_id,payload.previous_artifact_id)
        if not previous.locally_valid or previous.revision_id==revision_id:
            raise ValueError('Follow-up требует другой содержательной ревизии и предшествующего локально проверенного артефакта.')
        old=json.loads(db.get(m.IcsrRevision,previous.revision_id).snapshot_json)
        current=json.loads(source.snapshot_json)
        if any(old['case'].get(k)!=current['case'].get(k) for k in ('worldwide_case_id','local_sender_case_id','e2b_sender_scope')):
            raise ValueError('Идентичность случая изменилась; обычный follow-up не применяется.')
        if json.loads(previous.envelope_json)['sender_id']!=payload.sender_id:
            raise ValueError('Отправитель follow-up отличается от предыдущего артефакта.')
    elif payload.previous_artifact_id:
        raise ValueError('Предшествующий артефакт указывается только для выбранного follow-up.')
    now=datetime.now(timezone.utc);ident=str(uuid4())
    env=dict(sender_id=payload.sender_id,receiver_id=payload.receiver_id,purpose=payload.purpose,scenario=payload.scenario,
        batch_id=payload.batch_id or 'PV1-B-'+str(uuid4()),message_id=payload.message_id or 'PV1-M-'+str(uuid4()),
        created_ts=now.strftime('%Y%m%d%H%M%S.%f+0000'),previous_artifact_id=payload.previous_artifact_id)
    result=export_revision(dict(snapshot_json=source.snapshot_json,schema_version=source.schema_version,checksum_sha256=source.checksum_sha256,case_id=case_id),env,get_settings().storage_root)
    if previous:
        def received(data,key):
            rs=[r for r in data['records']['CaseClinicalRecord'] if r['kind']=='report' and not r.get('is_deleted')]
            return json.loads(rs[0]['fields_json']).get(key,{}).get('raw') if len(rs)==1 else None
        if received(old,'C.1.4')!=received(current,'C.1.4') or (received(current,'C.1.5') or '')<(received(old,'C.1.5') or ''):
            raise ValueError('Для обычного follow-up первоначальная дата сохраняется, последняя не должна уменьшаться; требуется разбор сценария.')
    manifest=result['profile'];raw=result.pop('xml')
    folder=get_settings().storage_root/'r3';folder.mkdir(parents=True,exist_ok=True)
    suffix='.xml' if result['locally_valid'] else '.diagnostic.xml'
    final=folder/(ident+suffix);temporary=folder/(ident+'.part')
    register_new_file(temporary);register_new_file(final)
    try:
        with temporary.open('xb') as output:
            output.write(raw);output.flush();os.fsync(output.fileno())
        os.replace(temporary,final)
        row=m.IcsrExportArtifact(id=ident,case_id=case_id,revision_id=revision_id,previous_artifact_id=payload.previous_artifact_id,
            idempotency_key=str(payload.idempotency_key),request_checksum=request_hash,revision_checksum=source.checksum_sha256,snapshot_schema=source.schema_version,
            profile_id=manifest['profile_id'],profile_manifest_sha256=manifest['manifest_sha256'],profile_json=dump(manifest),
            exporter_version=manifest['exporter_version'],rules_version=manifest['rules_version'],envelope_json=dump(env),
            validation_json=dump({k:v for k,v in result.items() if k!='profile'}),xml_sha256=sha(raw),storage_path=str(final.resolve()),byte_length=len(raw),
            locally_valid=result['locally_valid'],xsd_valid=result['xsd_valid'],author_id=audit_user_id(db),created_at=now,reason=payload.reason)
        db.add(row);db.flush()
        log_audit(db,entity_type='IcsrExportArtifact',entity_id=row.id,case_id=case_id,action='create',user_id=audit_user_id(db),
            new_value=dump(dict(revision_id=revision_id,xml_sha256=row.xml_sha256,locally_valid=row.locally_valid)),
            change_reason=payload.reason,source_module='r3_export')
        db.commit()
        return row
    except BaseException:
        # Both paths are unique to this preparation; never delete a pre-existing attachment.
        temporary.unlink(missing_ok=True);final.unlink(missing_ok=True)
        raise

def immutable(mapper,connection,target):
    raise ValueError('XML-артефакт неизменяем; подготовьте новый с другим ключом.')

event.listen(m.IcsrExportArtifact,'before_update',immutable)
event.listen(m.IcsrExportArtifact,'before_delete',immutable)
