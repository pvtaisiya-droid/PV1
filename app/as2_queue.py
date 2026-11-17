"""PV1 intent, immutable engine evidence and independent transport/application states."""
import base64
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import time
from uuid import UUID,uuid4
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import and_,or_,func,update
from sqlalchemy.exc import IntegrityError
from app import models as m
from app.as2_settings import configuration,public,config_hash,digest,dumps
from app import as2_ack,r3_artifacts
from app.config import get_settings
from app.concurrency import lock_snapshot_inputs
from app.crud import audit_user_id
from app.audit import log_audit


class Enqueue(BaseModel):
    model_config=ConfigDict(extra='forbid')
    command_key: UUID
    config_version: str
    expected_xml_sha256: str=Field(pattern='^[0-9a-f]{64}$')
    reason: str=Field(min_length=10,max_length=2000)


class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version: int
    reason: str=Field(min_length=10,max_length=2000)


def owned(db,case_id,dispatch_id):
    row=db.get(m.AS2Dispatch,dispatch_id)
    if not row or row.case_id!=case_id:raise HTTPException(404,'Задание не принадлежит этому случаю.')
    r3_artifacts.artifact(db,case_id,row.artifact_id)
    return row


def enqueue(db,case_id,artifact_id,payload):
    lock_snapshot_inputs(db,('AS2Dispatch',))
    source=r3_artifacts.artifact(db,case_id,artifact_id)
    request_hash=digest(dumps(dict(case_id=case_id,artifact_id=artifact_id,parameters=payload.model_dump(mode='json'))).encode())
    prior=db.query(m.AS2Dispatch).filter_by(command_key=str(payload.command_key)).one_or_none()
    if prior:
        if prior.command_hash!=request_hash:raise HTTPException(409,'Ключ команды уже использован с другими параметрами.')
        return prior
    cfg=configuration(payload.config_version);env=json.loads(source.envelope_json)
    operational=db.query(m.OpsSubmission).filter_by(artifact_id=artifact_id,contour=cfg['contour'],recipient=cfg['b']['as2_id']).one_or_none()
    if operational and operational.manual_hold:raise HTTPException(409,'Зарегистрировано решение о ручной подаче. Новая автоматическая отправка заблокирована.')
    if operational and operational.config_hash!=config_hash(cfg):raise HTTPException(409,'Версия адресата отличается от эксплуатационной записи.')
    r3_artifacts.verified_bytes(source)
    if payload.expected_xml_sha256!=source.xml_sha256:raise HTTPException(409,'Контрольная сумма артефакта не совпала.')
    if env['sender_id']!=cfg['xml_sender_id'] or env['receiver_id']!=cfg['xml_receiver_id'] or env['purpose']!='local_test':
        raise ValueError('Артефакт не согласуется с XML-сторонами и назначением local_test; параметры не заменяются автоматически.')
    existing=db.query(m.AS2Dispatch).filter_by(artifact_id=artifact_id,contour=cfg['contour'],recipient=cfg['b']['as2_id']).one_or_none()
    if existing:raise HTTPException(409,'Намерение отправить этот артефакт адресату уже существует: '+existing.id)
    row=m.AS2Dispatch(case_id=case_id,artifact_id=artifact_id,command_key=str(payload.command_key),command_hash=request_hash,
        contour=cfg['contour'],recipient=cfg['b']['as2_id'],config_version=cfg['version'],config_hash=config_hash(cfg),config_json=dumps(public(cfg)),
        author_id=audit_user_id(db),reason=payload.reason)
    db.add(row);db.flush()
    if operational:operational.dispatch_id=row.id;operational.version+=1
    else:
        # New intents must be monitored even if the separate operations page was
        # never opened. This records preparation only, not delivery/submission.
        from app.operations import record
        actor=audit_user_id(db)
        operational=m.OpsSubmission(case_id=case_id,artifact_id=artifact_id,dispatch_id=row.id,
            contour=cfg['contour'],recipient=cfg['b']['as2_id'],config_version=cfg['version'],config_hash=config_hash(cfg),owner_id=actor,author_id=actor)
        db.add(operational);db.flush()
        record(db,'opened',payload.reason,subject=operational,actor=actor,facts=dict(origin='automatic_intent',dispatch_id=row.id))
    log_audit(db,entity_type='AS2Dispatch',entity_id=row.id,case_id=case_id,action='enqueue',user_id=audit_user_id(db),
        new_value=dumps(dict(artifact_id=artifact_id,config_version=cfg['version'],contour=cfg['contour'])),change_reason=payload.reason,source_module='as2')
    db.commit();return row


def action(db,case_id,dispatch_id,kind,payload):
    lock_snapshot_inputs(db,('AS2Dispatch','AS2Attempt'))
    row=owned(db,case_id,dispatch_id)
    if row.version!=payload.expected_version or (row.lease_until or 0)>time.time():raise HTTPException(409,'Задание изменилось или обрабатывается исполнителем.')
    if kind=='retry':
        if row.send_hold:raise HTTPException(409,'Ручная подача блокирует повтор; сведения о доставке не стираются.')
        last=db.query(m.AS2Attempt).filter_by(dispatch_id=row.id).order_by(m.AS2Attempt.number.desc()).first()
        if row.state!='not_delivered' or not last or last.state!='not_delivered':
            raise HTTPException(409,'Повтор разрешён только при подтверждённом сбое до доставки; неопределённость/MDN/ACK требуют разбора.')
        row.state='queued';row.last_error=None
        db.add(m.AS2Attempt(dispatch_id=row.id,number=last.number+1))
    elif kind=='reconcile':
        # Does not invent success or authorize a fresh transmission.
        row.state='reconciling'
    else:raise HTTPException(404,'Неизвестное действие.')
    row.version+=1;row.next_at=0;row.updated_at=m.utcnow()
    log_audit(db,entity_type='AS2Dispatch',entity_id=row.id,case_id=case_id,action=kind,user_id=audit_user_id(db),change_reason=payload.reason,source_module='as2')
    db.commit();return row


def claim(db,owner,now=None):
    now=now or time.time()
    available=and_(m.AS2Dispatch.next_at<=now,or_(m.AS2Dispatch.lease_until.is_(None),m.AS2Dispatch.lease_until<=now),
        or_(m.AS2Dispatch.state.in_(('queued','dispatching','reconciling','waiting_mdn','waiting_ack','mdn_overdue','ack_overdue','uncertain')),
            and_(m.AS2Dispatch.send_hold.is_(True),m.AS2Dispatch.hold_state=='engine_pending')))
    row=db.query(m.AS2Dispatch.id).filter(available).order_by(m.AS2Dispatch.next_at,m.AS2Dispatch.created_at).first()
    if not row:return None
    count=db.execute(update(m.AS2Dispatch).where(m.AS2Dispatch.id==row.id,available).values(lease_owner=owner,lease_until=now+30,version=m.AS2Dispatch.version+1)).rowcount
    db.commit();return row.id if count else None


def archive_bytes(event_id,suffix,raw):
    root=get_settings().storage_root/'as2-events';root.mkdir(parents=True,exist_ok=True)
    final=root/(event_id+suffix);temp=root/(event_id+suffix+'.part')
    try:
        with temp.open('xb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
        os.replace(temp,final)
    except BaseException:temp.unlink(missing_ok=True);raise
    return str(final.resolve()),digest(raw)


def ingest(db,engine_id,item):
    existing=db.query(m.AS2Event).filter_by(engine_id=engine_id,sequence=item['seq']).one_or_none()
    if existing:return existing
    meta=dict(item['metadata'])
    if item.get('created_at') is not None:meta['engine_created_at']=item['created_at']
    if meta.get('engine_id')!=engine_id or meta.get('side')!='a' or meta.get('contour')!='local_stand':raise ValueError('Нарушена граница источника AS2-событий.')
    row=m.AS2Event(id=str(uuid4()),engine_id=engine_id,sequence=item['seq'],event_key=item['event_key'],kind=meta['kind'],metadata_json=dumps(meta))
    paths=[]
    try:
        for key,suffix in (('raw','.mime'),('payload','.payload')):
            if item.get(key) is not None:
                raw=base64.b64decode(item[key],validate=True)
                if len(raw)>40*1024*1024 or digest(raw)!=meta.get(key+'_sha256'):raise ValueError('Контрольная сумма архивного AS2-ответа не совпала.')
                path,checksum=archive_bytes(row.id,suffix,raw);paths.append(path)
                setattr(row,key+'_path',path);setattr(row,key+'_sha256',checksum)
        db.add(row);db.flush();apply_event(db,row);db.commit();return row
    except BaseException:
        db.rollback()
        for path in paths:Path(path).unlink(missing_ok=True)
        raise


def evidence(row,kind):
    location=getattr(row,kind+'_path');expected=getattr(row,kind+'_sha256')
    if not location:raise ValueError('У события нет этого архивного файла.')
    from app.recovery_paths import resolve_storage
    root=(get_settings().storage_root/'as2-events').resolve();path=resolve_storage(location)
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('Архив события отсутствует.')
    raw=path.read_bytes()
    if digest(raw)!=expected:raise ValueError('Архив события изменён.')
    return raw


def apply_event(db,row):
    if row.processing_state!='unmatched':return
    meta=json.loads(row.metadata_json);ack=None
    if row.kind=='duplicate':
        prior=db.query(m.AS2Event).filter(m.AS2Event.engine_id==row.engine_id,m.AS2Event.metadata_json.contains('"inbox_id":"'+meta.get('original_inbox_id','')+'"')).first()
        if prior:row.dispatch_id=prior.dispatch_id;row.attempt_id=prior.attempt_id
        row.processing_state='recorded';return
    attempt=db.get(m.AS2Attempt,meta.get('request_id')) if meta.get('request_id') else None
    if row.kind=='ack' and meta.get('trusted'):
        try:
            ack=as2_ack.parse(evidence(row,'payload'))
            candidates=db.query(m.AS2Attempt).filter_by(message_id=ack['original_as2_message_id']).all()
            if len(candidates)==1:attempt=candidates[0]
            # Retain per-message results even when correlation is unknown.
            if not db.query(m.AS2AckResult).filter_by(event_id=row.id).first():
                for i,result in enumerate(ack['results']):
                    db.add(m.AS2AckResult(event_id=row.id,ordinal=i,message_id=result['message_id'],code=str(result['code'])[:50],interpretation=result['interpretation'],detail=result['detail']))
        except (ValueError,TypeError,KeyError):row.processing_state='review';row.review_reason='unknown_ack_profile_or_structure';return
    if not attempt:
        row.review_reason='unmatched_correlation';return
    dispatch=db.get(m.AS2Dispatch,attempt.dispatch_id)
    if attempt.engine_id and row.engine_id!=attempt.engine_id:
        row.review_reason='different_engine_history';return
    row.dispatch_id=dispatch.id;row.attempt_id=attempt.id
    if row.kind=='hold':row.processing_state='recorded';return
    if meta.get('config_version')!=dispatch.config_version or (meta.get('config_hash') and meta['config_hash']!=dispatch.config_hash):
        row.processing_state='review';row.review_reason='configuration_mismatch';return
    if row.kind in ('mdn','ack') and (not meta.get('trusted') or meta.get('fingerprint')!=json.loads(dispatch.config_json)['b']['fingerprint']):
        row.processing_state='review';row.review_reason='untrusted_transport_response';return
    if row.kind=='transport' and meta.get('try_number',0)>=attempt.send_count:
        attempt.state=meta['state'];attempt.send_count=meta.get('try_number',attempt.send_count)
        attempt.message_id=meta.get('message_id',attempt.message_id);attempt.error=meta.get('reason');attempt.updated_at=m.utcnow()
    if ack:
        artifact=db.get(m.IcsrExportArtifact,dispatch.artifact_id);envelope=json.loads(artifact.envelope_json)
        if ack['batch_id']!=envelope['batch_id'] or ack['artifact_sha256']!=artifact.xml_sha256 or {r['message_id'] for r in ack['results']}!={envelope['message_id']}:
            row.processing_state='review';row.review_reason='ack_identifiers_or_message_set_mismatch';return
        if any(r['interpretation'] in ('unknown','review') for r in ack['results']):
            row.processing_state='review';row.review_reason='ack_result_requires_review';return
    row.processing_state='applied';row.review_reason=None


def refresh(db,dispatch,now=None):
    previous=(dispatch.state,dispatch.transport_state,dispatch.ack_state)
    now=now or time.time();events=db.query(m.AS2Event).filter_by(dispatch_id=dispatch.id).all()
    mdns=[json.loads(e.metadata_json) for e in events if e.kind=='mdn' and e.processing_state=='applied']
    ack_events=[e for e in events if e.kind=='ack' and e.processing_state=='applied']
    codes={r.interpretation for e in ack_events for r in db.query(m.AS2AckResult).filter_by(event_id=e.id)}
    transport={r.get('state') for r in mdns};review=any(e.processing_state=='review' for e in events)
    latest=db.query(m.AS2Attempt).filter_by(dispatch_id=dispatch.id).order_by(m.AS2Attempt.number.desc()).first()
    dispatch.transport_state='positive' if transport=={'positive'} else 'negative' if transport=={'negative'} else 'conflict' if len(transport)>1 else 'pending'
    dispatch.ack_state='accepted' if codes=={'accepted'} else 'rejected' if codes=={'rejected'} else 'conflict' if len(codes)>1 else 'pending'
    if review or len(codes)>1 or len(transport)>1:state='requires_review'
    elif codes=={'rejected'}:state='ack_rejected'
    elif transport=={'negative'}:state='mdn_negative'
    elif transport=={'positive'} and codes=={'accepted'}:state='locally_acknowledged'
    elif transport=={'positive'}:state='waiting_ack'
    elif latest and latest.state=='not_delivered':state='not_delivered'
    elif latest and latest.state=='unknown':state='uncertain'
    elif latest and latest.state=='waiting_mdn':state='waiting_mdn'
    elif latest and latest.state=='requesting':state='uncertain'
    else:state='dispatching' if latest else 'queued'
    if dispatch.send_hold and dispatch.hold_state in ('pv1_stopped','engine_stopped') and state in ('queued','dispatching','not_delivered'):
        state='held_for_manual'
    cfg=json.loads(dispatch.config_json)
    if latest:
        created=latest.created_at.replace(tzinfo=timezone.utc) if latest.created_at.tzinfo is None else latest.created_at
        elapsed=now-created.timestamp()
        if state=='waiting_mdn' and elapsed>cfg['mdn_wait_seconds']:state='mdn_overdue'
        if state=='waiting_ack' and elapsed>cfg['ack_wait_seconds']:state='ack_overdue'
    dispatch.state=state
    if previous!=(dispatch.state,dispatch.transport_state,dispatch.ack_state):dispatch.updated_at=m.utcnow();dispatch.version+=1


def serialize(db,row):
    attempts=db.query(m.AS2Attempt).filter_by(dispatch_id=row.id).order_by(m.AS2Attempt.number).all()
    events=db.query(m.AS2Event).filter_by(dispatch_id=row.id).order_by(m.AS2Event.created_at).all()
    def columns(obj,fields):return {k:getattr(obj,k) for k in fields}
    data=columns(row,('id','case_id','artifact_id','contour','recipient','config_version','state','transport_state','ack_state','version','last_error','created_at','updated_at','reason'))
    data['publication']='not_confirmed';data['config']=json.loads(row.config_json)
    data['revision_id']=db.get(m.IcsrExportArtifact,row.artifact_id).revision_id
    data['attempts']=[columns(a,('id','number','state','message_id','send_count','mic','mic_algorithm','error','created_at','updated_at')) for a in attempts]
    data['events']=[dict(**columns(e,('id','kind','processing_state','review_reason','created_at','raw_sha256','payload_sha256')),metadata=json.loads(e.metadata_json),
        results=[columns(r,('ordinal','message_id','code','interpretation','detail')) for r in db.query(m.AS2AckResult).filter_by(event_id=e.id).order_by(m.AS2AckResult.ordinal)]) for e in events]
    data['elapsed_seconds']=max(0,int(time.time()-(row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at).timestamp()))
    data['send_hold']=row.send_hold;data['hold_state']=row.hold_state
    data['can_retry']=not row.send_hold and row.state=='not_delivered' and (row.lease_until or 0)<=time.time()
    return data
