"""Operational facts; never infer regulatory acceptance or publication from transport."""
from datetime import date,datetime,timedelta,timezone
import json
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field
from app import models as m,as2_queue,r3_artifacts
from app.as2_settings import configuration,config_hash,dumps
from app.concurrency import lock_snapshot_inputs
from app.crud import audit_user_id
from app.audit import log_audit

MSK=timezone(timedelta(hours=3),'Europe/Moscow')
def utc(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
def stamp(value):return utc(value).isoformat() if value else None
def display(value):return utc(datetime.fromisoformat(value) if isinstance(value,str) else value).astimezone(MSK).isoformat(timespec='seconds') if value else ''


def instant(raw):
    value=datetime.fromisoformat(raw)
    if value.tzinfo is None or value.utcoffset() is None:raise ValueError('Укажите дату, время и часовой пояс, например +03:00.')
    return value.astimezone(timezone.utc)


class Write(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason:str=Field(min_length=10,max_length=2000)
    expected_version:int=Field(ge=1)

class Open(BaseModel):
    model_config=ConfigDict(extra='forbid')
    config_version:str
    expected_xml_sha256:str
    reason:str=Field(min_length=10,max_length=2000)

class Manual(Write):
    operation:Literal['decide','submitted','result','deadline','assign']
    submitted_at:str|None=None
    submission_action_id:str|None=None
    result:Literal['pending','accepted','rejected','review']|None=None
    reference_number:str|None=Field(default=None,max_length=255)
    attachment_id:str|None=None
    evidence_reference:str|None=Field(default=None,max_length=2000)
    deadline:str|None=None
    deadline_basis:str|None=Field(default=None,max_length=2000)
    owner_id:str|None=None

class Publication(Write):
    source_key:str
    previous_id:str|None=None
    status:Literal['not_confirmed','confirmed_manual','problem']
    source:str=Field(min_length=3,max_length=1000)
    published_at:str|None=None
    registration_number:str|None=Field(default=None,max_length=255)
    attachment_id:str|None=None
    evidence_reference:str|None=Field(default=None,max_length=2000)

class PolicyWrite(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_number:int=Field(ge=0)
    autopublish:Literal['enabled','disabled','unknown']
    mdn_seconds:int=Field(ge=1,le=31536000)
    ack_seconds:int=Field(ge=1,le=31536000)
    publication_seconds:int=Field(ge=1,le=31536000)
    approaching_seconds:int=Field(ge=0,le=31536000)
    certificate_days:int=Field(ge=0,le=365)
    health_seconds:int=Field(ge=10,le=86400)
    reminder_seconds:int=Field(ge=60,le=31536000)
    reminder_limit:int=Field(ge=0,le=10)
    owner_id:str
    reason:str=Field(min_length=10,max_length=2000)

class IncidentWrite(Write):
    operation:Literal['acknowledge','assign','close']
    owner_id:str|None=None


def owner(db,key):
    user=db.get(m.User,key)
    if not user or user.is_deleted or not user.is_active:raise ValueError('Выберите действующего ответственного сотрудника.')
    return key

def owned(db,case_id,key):
    row=db.get(m.OpsSubmission,key)
    if not row or row.case_id!=case_id:raise HTTPException(404,'Эксплуатационная запись не принадлежит случаю.')
    r3_artifacts.artifact(db,case_id,row.artifact_id)
    return row

def version(row,expected):
    if row.version!=expected:raise HTTPException(409,'Состояние изменилось. Обновите экран.')
    row.version+=1

def record(db,kind,reason,*,subject=None,incident=None,facts=None,actor=None,now=None):
    row=m.OpsAction(submission_id=subject.id if subject else None,incident_id=incident.id if incident else None,kind=kind,
        actor_id=actor,occurred_at=now or m.utcnow(),facts_json=dumps(facts or {}),reason=reason)
    db.add(row);db.flush()
    log_audit(db,entity_type='OpsSubmission' if subject else 'OpsIncident',entity_id=subject.id if subject else incident.id if incident else row.id,
        case_id=subject.case_id if subject else None,action=kind,user_id=actor,change_reason=reason,source_module='operations')
    return row

def actions(db,subject,kind=None):
    query=db.query(m.OpsAction).filter_by(submission_id=subject.id)
    if kind:query=query.filter_by(kind=kind)
    return query.order_by(m.OpsAction.occurred_at,m.OpsAction.id).all()

def attachment(db,subject,key):
    if not key:return None
    row=db.get(m.Attachment,key)
    if not row or row.case_id!=subject.case_id or row.is_deleted:raise HTTPException(404,'Доказательство не принадлежит случаю.')
    from app.recovery_paths import resolve_storage
    from app.as2_settings import digest
    from app.config import get_settings
    path=resolve_storage(row.storage_path)
    if not path.is_relative_to(get_settings().storage_root.resolve()) or not path.is_file() or (row.checksum_sha256 and digest(path.read_bytes())!=row.checksum_sha256):raise ValueError('Файл доказательства отсутствует или изменён.')
    return dict(attachment_id=row.id,sha256=digest(path.read_bytes()))

def open_submission(db,case_id,artifact_id,payload):
    lock_snapshot_inputs(db,('OpsSubmission',))
    artifact=r3_artifacts.artifact(db,case_id,artifact_id);r3_artifacts.verified_bytes(artifact)
    if artifact.xml_sha256!=payload.expected_xml_sha256:raise HTTPException(409,'Артефакт изменился.')
    cfg=configuration(payload.config_version);envelope=json.loads(artifact.envelope_json)
    if envelope['sender_id']!=cfg['xml_sender_id'] or envelope['receiver_id']!=cfg['xml_receiver_id'] or envelope['purpose']!='local_test':raise ValueError('Артефакт не соответствует локальному адресату.')
    row=db.query(m.OpsSubmission).filter_by(artifact_id=artifact_id,contour=cfg['contour'],recipient=cfg['b']['as2_id']).one_or_none()
    if row:
        if row.config_version!=cfg['version']:raise HTTPException(409,'Для артефакта уже закреплена другая версия адресата.')
        return row
    dispatch=db.query(m.AS2Dispatch).filter_by(artifact_id=artifact_id,contour=cfg['contour'],recipient=cfg['b']['as2_id']).one_or_none()
    if dispatch and dispatch.config_hash!=config_hash(cfg):raise ValueError('Версия автоматического намерения отличается.')
    actor=audit_user_id(db)
    row=m.OpsSubmission(case_id=case_id,artifact_id=artifact_id,dispatch_id=dispatch.id if dispatch else None,contour=cfg['contour'],recipient=cfg['b']['as2_id'],config_version=cfg['version'],config_hash=config_hash(cfg),owner_id=actor,author_id=actor)
    db.add(row);db.flush();record(db,'opened',payload.reason,subject=row,actor=actor);db.commit();return row

def manual(db,case_id,key,payload):
    lock_snapshot_inputs(db,('OpsSubmission','AS2Dispatch','AS2Attempt'))
    row=owned(db,case_id,key);version(row,payload.expected_version);actor=audit_user_id(db)
    facts={}
    if payload.operation=='decide':
        row.manual_hold=True
        dispatch=db.get(m.AS2Dispatch,row.dispatch_id) if row.dispatch_id else None
        if dispatch:
            last=db.query(m.AS2Attempt).filter_by(dispatch_id=dispatch.id).order_by(m.AS2Attempt.number.desc()).first()
            dispatch.send_hold=True;dispatch.version+=1;dispatch.next_at=0
            dispatch.hold_state='pv1_stopped' if not last or last.state=='prepared' else 'engine_pending'
            facts=dict(dispatch_id=dispatch.id,hold_state=dispatch.hold_state,possible_delivery=dispatch.hold_state=='engine_pending')
        else:facts=dict(hold_state='pv1_stopped',possible_delivery=False)
    elif payload.operation=='submitted':
        if not row.manual_hold:raise ValueError('Сначала зарегистрируйте решение о резервной подаче.')
        if not payload.submitted_at:raise ValueError('Укажите фактическое время подачи с часовым поясом; скачивание не является подачей.')
        occurred=instant(payload.submitted_at)
        if occurred>utc(m.utcnow())+timedelta(minutes=5):raise ValueError('Фактическая подача не может быть в будущем.')
        proof=attachment(db,row,payload.attachment_id)
        facts=dict(submitted_at=stamp(occurred),submitted_raw=payload.submitted_at,reference_number=payload.reference_number,
            evidence=proof,evidence_reference=payload.evidence_reference,confirmation_complete=bool(payload.reference_number or proof or payload.evidence_reference))
    elif payload.operation=='result':
        source=db.get(m.OpsAction,payload.submission_action_id) if payload.submission_action_id else None
        if not source or source.submission_id!=row.id or source.kind!='manual_submitted' or not payload.result:raise ValueError('Выберите конкретную ручную подачу и её результат.')
        facts=dict(submission_action_id=source.id,result=payload.result,reference_number=payload.reference_number,evidence=attachment(db,row,payload.attachment_id),evidence_reference=payload.evidence_reference)
    elif payload.operation=='deadline':
        if not payload.deadline or not payload.deadline_basis or len(payload.deadline_basis.strip())<10:raise ValueError('Нужны явно заданный срок и его основание.')
        if len(payload.deadline)==10:
            date.fromisoformat(payload.deadline);row.deadline_utc=None;row.deadline_precision='date'
        else:row.deadline_utc=instant(payload.deadline);row.deadline_precision='instant'
        row.deadline_raw=payload.deadline;row.deadline_basis=payload.deadline_basis
        facts=dict(deadline_raw=row.deadline_raw,deadline_utc=stamp(row.deadline_utc),precision=row.deadline_precision,basis=row.deadline_basis)
    elif payload.operation=='assign':row.owner_id=owner(db,payload.owner_id);facts=dict(owner_id=row.owner_id)
    record(db,'manual_'+payload.operation,payload.reason,subject=row,actor=actor,facts=facts)
    db.commit();return row

def sources(db,row):
    result={f'manual:{a.id}':dict(kind='manual',id=a.id,time=json.loads(a.facts_json)['submitted_at']) for a in actions(db,row,'manual_submitted')}
    if row.dispatch_id:
        for a in db.query(m.AS2Attempt).filter_by(dispatch_id=row.dispatch_id):
            if a.state not in ('prepared','cancelled','not_delivered'):result['automatic:'+a.id]=dict(kind='automatic',id=a.id,time=stamp(a.created_at))
    return result

def publication(db,case_id,key,payload):
    lock_snapshot_inputs(db,('OpsSubmission','OpsPublication'))
    row=owned(db,case_id,key);version(row,payload.expected_version)
    if payload.source_key not in sources(db,row):raise HTTPException(404,'Проверка должна относиться к конкретной подаче этого артефакта.')
    previous=db.query(m.OpsPublication).filter_by(submission_id=row.id,source_key=payload.source_key).order_by(m.OpsPublication.checked_at.desc()).first()
    if (previous.id if previous else None)!=payload.previous_id:raise HTTPException(409,'История проверки публикации изменилась.')
    proof=attachment(db,row,payload.attachment_id)
    published=instant(payload.published_at) if payload.published_at else None
    if published and payload.status!='confirmed_manual':raise ValueError('Время публикации допускается только при ручном подтверждении.')
    if published and published>utc(m.utcnow())+timedelta(minutes=5):raise ValueError('Публикация не может находиться в будущем.')
    entry=m.OpsPublication(submission_id=row.id,source_key=payload.source_key,previous_id=payload.previous_id,status=payload.status,
        actor_id=audit_user_id(db),published_at=published,published_raw=payload.published_at,source=payload.source,registration_number=payload.registration_number,
        attachment_id=payload.attachment_id,evidence_reference=payload.evidence_reference,reason=payload.reason)
    db.add(entry);db.flush();record(db,'publication_check',payload.reason,subject=row,actor=audit_user_id(db),facts=dict(publication_id=entry.id,status=entry.status,source_key=entry.source_key,evidence=proof));db.commit();return row

def policy(db,config_version):return db.query(m.OpsPolicy).filter_by(config_version=config_version).order_by(m.OpsPolicy.number.desc()).first()

def save_policy(db,config_version,payload):
    lock_snapshot_inputs(db,('OpsPolicy',));cfg=configuration(config_version);previous=policy(db,config_version)
    if (previous.number if previous else 0)!=payload.expected_number:raise HTTPException(409,'Политика изменена другим пользователем.')
    values=payload.model_dump();values.pop('expected_number');owner(db,payload.owner_id)
    row=m.OpsPolicy(config_version=config_version,config_hash=config_hash(cfg),number=payload.expected_number+1,author_id=audit_user_id(db),**values)
    db.add(row);db.flush()
    log_audit(db,entity_type='OpsPolicy',entity_id=row.id,action='policy_version',user_id=audit_user_id(db),change_reason=payload.reason,source_module='operations')
    db.commit();return row

def incident_action(db,key,payload):
    lock_snapshot_inputs(db,('OpsIncident',));row=db.get(m.OpsIncident,key)
    if not row:raise HTTPException(404,'Инцидент не найден.')
    version(row,payload.expected_version)
    if payload.operation=='assign':row.owner_id=owner(db,payload.owner_id)
    elif payload.operation=='acknowledge':
        if row.status not in ('open','acknowledged'):raise ValueError('Инцидент уже закрыт/разрешён.')
        row.status='acknowledged'
    else:row.status='closed'
    record(db,'incident_'+payload.operation,payload.reason,incident=row,actor=audit_user_id(db),facts=dict(owner_id=row.owner_id,condition_still_active=row.active_condition))
    db.commit();return row

def columns(row):
    return {c.name:(stamp(value) if isinstance(value:=getattr(row,c.name),datetime) else value) for c in row.__table__.columns}

def read(db,row):
    result=columns(row);artifact=db.get(m.IcsrExportArtifact,row.artifact_id)
    result.update(revision_id=artifact.revision_id,xml_sha256=artifact.xml_sha256,actions=[columns(a) for a in actions(db,row)],
        publications=[columns(p) for p in db.query(m.OpsPublication).filter_by(submission_id=row.id).order_by(m.OpsPublication.checked_at)],
        sources=sources(db,row),incidents=[columns(i) for i in db.query(m.OpsIncident).filter_by(submission_id=row.id).order_by(m.OpsIncident.since.desc())])
    pol=policy(db,row.config_version);result['policy']=columns(pol) if pol else None
    result['automatic']=as2_queue.serialize(db,db.get(m.AS2Dispatch,row.dispatch_id)) if row.dispatch_id else None
    return result
