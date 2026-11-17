"""Independent PV1 worker. Network calls run outside PV1 transactions."""
import argparse
import json
import os
from pathlib import Path
import time
from uuid import uuid4
if __name__=='__main__' and any(not os.environ.get(key) for key in ('DATABASE_URL','PV_STORAGE_ROOT','PV_AS2_CONFIG')):
    raise SystemExit('Explicit DATABASE_URL, PV_STORAGE_ROOT and PV_AS2_CONFIG are required.')
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from app import models as m
from app.database import SessionLocal
from app import as2_adapter as adapter,as2_queue as queue
from app.as2_settings import configuration,config_hash
from app.r3_artifacts import verified_bytes
from app.concurrency import lock_snapshot_inputs


def sync_hold(dispatch_id):
    with SessionLocal() as db:
        row=db.get(m.AS2Dispatch,dispatch_id)
        if not row or not row.send_hold:return
        attempt=db.query(m.AS2Attempt).filter_by(dispatch_id=row.id).order_by(m.AS2Attempt.number.desc()).first()
        if not attempt or attempt.state=='prepared':
            row.hold_state='pv1_stopped';queue.refresh(db,row);db.commit();return
        cfg=configuration(row.config_version);key=attempt.id;engine_id=attempt.engine_id
    try:
        result=adapter.call('hold',dict(request_id=key),cfg=cfg)
        state=result['hold_state'] if result['engine_id']==engine_id else 'delivery_possible'
    except Exception:state='engine_pending'
    with SessionLocal() as db:
        lock_snapshot_inputs(db,('AS2Dispatch','AS2Attempt'));row=db.get(m.AS2Dispatch,dispatch_id)
        row.hold_state=state
        if state=='engine_stopped':db.get(m.AS2Attempt,key).state='cancelled'
        queue.refresh(db,row);row.version+=1;row.next_at=time.time()+2;db.commit()


def poll_events():
    status=adapter.call('status');engine_id=status['engine_id']
    with SessionLocal() as db:after=db.query(func.max(m.AS2Event.sequence)).filter_by(engine_id=engine_id).scalar() or 0
    result=adapter.call('events?after='+str(after))
    if result['engine_id']!=engine_id:raise ValueError('engine_changed_during_poll')
    ids=set()
    for item in result['events']:
        with SessionLocal() as db:
            try:
                row=queue.ingest(db,engine_id,item)
                if row.dispatch_id:ids.add(row.dispatch_id)
            except IntegrityError:db.rollback()  # another worker archived the same durable event
    with SessionLocal() as db:
        for event in db.query(m.AS2Event).filter_by(processing_state='unmatched').limit(50):
            queue.apply_event(db,event)
            if event.dispatch_id:ids.add(event.dispatch_id)
        db.flush()
        for key in ids:
            row=db.get(m.AS2Dispatch,key);queue.refresh(db,row)
        db.commit()


def process(dispatch_id,owner):
    attempt_id=None
    try:
        with SessionLocal() as db:
            lock_snapshot_inputs(db,('AS2Dispatch','AS2Attempt'))
            row=db.get(m.AS2Dispatch,dispatch_id)
            if row.lease_owner!=owner:return
            held=row.send_hold
            if held:
                db.commit()
            else:
                db.commit()
        if held:
            sync_hold(dispatch_id)
            with SessionLocal() as db:
                row=db.get(m.AS2Dispatch,dispatch_id);row.lease_owner=None;row.lease_until=None;row.next_at=time.time()+2;db.commit()
            return
        with SessionLocal() as db:
            lock_snapshot_inputs(db,('AS2Dispatch','AS2Attempt'))
            row=db.get(m.AS2Dispatch,dispatch_id)
            if row.send_hold or row.lease_owner!=owner:return
            cfg=configuration(row.config_version)
            if cfg.get('_runtime',{}).get('outgoing_disabled'):return
            if config_hash(cfg)!=row.config_hash:raise ValueError('configuration_changed')
            artifact=db.get(m.IcsrExportArtifact,row.artifact_id)
            raw=verified_bytes(artifact);expected_hash=artifact.xml_sha256
            envelope=json.loads(artifact.envelope_json)
            if envelope['sender_id']!=cfg['xml_sender_id'] or envelope['receiver_id']!=cfg['xml_receiver_id'] or envelope['purpose']!='local_test':raise ValueError('envelope_mismatch')
            attempt=db.query(m.AS2Attempt).filter_by(dispatch_id=row.id).order_by(m.AS2Attempt.number.desc()).first()
            if not attempt:
                attempt=m.AS2Attempt(dispatch_id=row.id,number=1);db.add(attempt);db.flush()
            attempt_id=attempt.id;attempt_state=attempt.state;prior_engine=attempt.engine_id
            db.commit()
        status=adapter.call('status',cfg=cfg);engine_id=status['engine_id']
        if prior_engine and prior_engine!=engine_id:raise ValueError('engine_history_replaced')
        with SessionLocal() as db:
            attempt=db.get(m.AS2Attempt,attempt_id);attempt.engine_id=engine_id;db.commit()
        known=adapter.call('task/'+attempt_id,cfg=cfg)
        if known is None:
            if attempt_state!='prepared':raise ValueError('handoff_result_unknown_no_blind_repeat')
            with SessionLocal() as db:
                lock_snapshot_inputs(db,('AS2Dispatch','AS2Attempt'))
                row=db.get(m.AS2Dispatch,dispatch_id)
                if row.send_hold or row.lease_owner!=owner:return
                attempt=db.get(m.AS2Attempt,attempt_id);attempt.state='requesting';attempt.updated_at=m.utcnow();db.commit()
            known=adapter.submit(attempt_id,raw,cfg)
        if known['payload_sha']!=expected_hash or known['config_hash']!=config_hash(cfg):raise ValueError('engine_task_conflict')
        with SessionLocal() as db:
            row=db.get(m.AS2Dispatch,dispatch_id)
            if row.lease_owner!=owner:return
            attempt=db.get(m.AS2Attempt,attempt_id)
            attempt.message_id=known['message_id'];attempt.state=known['state'];attempt.send_count=known['tries']
            attempt.mic=known['mic'];attempt.mic_algorithm=known['digest_alg'];attempt.error=known['error'];attempt.updated_at=m.utcnow()
            row.last_error=None;db.flush();queue.refresh(db,row);row.lease_owner=None;row.lease_until=None;row.next_at=time.time()+1;db.commit()
    except Exception as exc:
        # Do not log exception text: protocol/library exceptions may embed content.
        error=str(exc) if isinstance(exc,ValueError) and str(exc) in ('configuration_changed','envelope_mismatch','engine_history_replaced','handoff_result_unknown_no_blind_repeat','engine_task_conflict') else 'local_engine_or_artifact_unavailable'
        with SessionLocal() as db:
            row=db.get(m.AS2Dispatch,dispatch_id)
            if row and row.lease_owner==owner:
                row.last_error=error;row.state='uncertain' if error in ('engine_history_replaced','handoff_result_unknown_no_blind_repeat') else 'requires_review' if error in ('configuration_changed','envelope_mismatch','engine_task_conflict') else 'reconciling'
                row.lease_owner=None;row.lease_until=None;row.next_at=time.time()+2;row.updated_at=m.utcnow();row.version+=1;db.commit()


def step(owner=None):
    owner=owner or str(uuid4())
    try:poll_events()
    except Exception:pass  # durable engine spool will be polled again; no acknowledgement/deletion
    if configuration().get('_runtime',{}).get('outgoing_disabled'):return None
    with SessionLocal() as db:key=queue.claim(db,owner)
    if key:process(key,owner)
    return key


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');parser.add_argument('--stop-file',required=False)
    args=parser.parse_args()
    if not os.environ.get('DATABASE_URL') or not os.environ.get('PV_STORAGE_ROOT') or not os.environ.get('PV_AS2_CONFIG'):
        raise SystemExit('Explicit DATABASE_URL, PV_STORAGE_ROOT and PV_AS2_CONFIG are required.')
    from app.local_runtime import runtime_guard
    guard=runtime_guard()
    owner=str(uuid4());stop=Path(args.stop_file) if args.stop_file else None
    while not stop or not stop.exists():
        try:step(owner)
        except Exception:print('worker_database_or_queue_unavailable',flush=True)
        try:
            from app.operations_monitor import save_signal
            with SessionLocal() as db:save_signal(db,'worker',dict(owner=owner,alive=True));db.commit()
        except Exception:pass
        if stop:
            heartbeat=stop.parent/('worker-'+owner+'.heartbeat')
            heartbeat.write_text(str(time.time()),'ascii')
        if args.once:break
        time.sleep(.25)
    if stop and 'heartbeat' in locals():heartbeat.unlink(missing_ok=True)
    guard.close()


if __name__=='__main__':main()
