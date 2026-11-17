"""Bounded operational risks; synthetic content, controlled time and explicit isolated paths."""
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
import base64
import io
import json
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4
from zipfile import ZipFile
import pytest
from tests.test_as2_local import prepare,seed_artifact,enqueue


def setup(client,tmp_path,monkeypatch,open_record=True,with_policy=True):
    from app.database import SessionLocal
    from app import models as m
    root=tmp_path/'stand';prepare(root,monkeypatch)
    cid,artifact,xml=seed_artifact(client)
    with SessionLocal() as db:uid=db.query(m.User).filter_by(login='test-admin').one().id
    policy=dict(expected_number=0,autopublish='unknown',mdn_seconds=30,ack_seconds=60,publication_seconds=120,approaching_seconds=86400,certificate_days=7,health_seconds=60,reminder_seconds=60,reminder_limit=2,owner_id=uid,reason='Explicit synthetic operational policy')
    if with_policy:
        response=client.post('/api/operations/policies/local-1',json=policy);assert response.status_code==200,response.text
    if not open_record:return root,cid,artifact,xml,None,uid
    response=client.post(f'/api/cases/{cid}/r3-artifacts/{artifact["id"]}/operations',json=dict(config_version='local-1',expected_xml_sha256=artifact['xml_sha256'],reason='Synthetic operational record'))
    assert response.status_code==200,response.text
    row=response.json();url=f'/api/cases/{cid}/operations/{row["id"]}'
    return root,cid,artifact,xml,url,uid


def write(client,url,operation,**fields):
    row=client.get(url).json()
    response=client.post(url+'/manual',json=dict(expected_version=row['version'],reason='Synthetic documented operational action',operation=operation,**fields))
    assert response.status_code==200,response.text
    return response.json()


def test_manual_fence_race_and_late_ack(client,tmp_path,monkeypatch):
    from app.database import SessionLocal
    from app import models as m,as2_worker as worker,as2_queue as queue
    from app.as2_settings import configuration,config_hash,digest
    from app.operations_monitor import evaluate
    root,cid,artifact,xml,url,uid=setup(client,tmp_path,monkeypatch,open_record=False,with_policy=False)
    dispatch,queue_url,payload=enqueue(client,cid,artifact);did=dispatch['id']
    with SessionLocal() as db:
        tracked=db.query(m.OpsSubmission).filter_by(dispatch_id=did).one()
        assert tracked.owner_id==uid and tracked.deadline_raw is None
        assert [a.kind for a in db.query(m.OpsAction).filter_by(submission_id=tracked.id)]==['opened']
        url=f'/api/cases/{cid}/operations/{tracked.id}'
    # Pause the executor after claim, before its final transactional handoff decision.
    entered=threading.Event();release=threading.Event()
    def fake_call(path,*args,**kwargs):
        if path=='status':entered.set();assert release.wait(5);return {'engine_id':'controlled-engine'}
        if path.startswith('task/'):return None
        pytest.fail('Unexpected engine control call')
    monkeypatch.setattr(worker.adapter,'call',fake_call)
    monkeypatch.setattr(worker.adapter,'submit',lambda *args,**kwargs:pytest.fail('Send must be fenced'))
    with SessionLocal() as db:assert queue.claim(db,'worker-race')==did
    with ThreadPoolExecutor(1) as pool:
        running=pool.submit(worker.process,did,'worker-race');assert entered.wait(5)
        row=write(client,url,'decide');assert row['automatic']['hold_state']=='pv1_stopped'
        release.set();running.result(timeout=5)
    assert client.post(queue_url,json={**payload,'command_key':str(uuid4())}).status_code==409
    # A second synthetic artifact is unnecessary: model an already uncertain handoff
    # on the same immutable intent, keeping all source fields unchanged.
    with SessionLocal() as db:
        from app.operations import sources
        attempt=db.query(m.AS2Attempt).filter_by(dispatch_id=did).one();attempt.state='cancelled';db.flush()
        assert not sources(db,db.query(m.OpsSubmission).filter_by(dispatch_id=did).one())
        attempt=db.query(m.AS2Attempt).filter_by(dispatch_id=did).one();attempt.state='unknown';attempt.message_id='synthetic@pv1-local.invalid';attempt.engine_id='controlled-engine';aid=attempt.id
        intent=db.get(m.AS2Dispatch,did);intent.state='uncertain';intent.hold_state='engine_pending';intent.lease_owner=None;intent.lease_until=None;db.commit()
    monkeypatch.setattr(worker.adapter,'call',lambda path,*a,**kw:dict(engine_id='controlled-engine',hold_state='delivery_possible'))
    worker.sync_hold(did)
    row=write(client,url,'submitted',submitted_at=datetime.now(timezone.utc).isoformat())
    manual=[a for a in row['actions'] if a['kind']=='manual_submitted'];assert len(manual)==1
    assert not json.loads(manual[0]['facts_json'])['confirmation_complete']
    cfg=configuration();ack=dict(profile='pv1-local-ack-1',batch_id=artifact['envelope']['batch_id'],artifact_sha256=artifact['xml_sha256'],original_as2_message_id='synthetic@pv1-local.invalid',results=[dict(message_id=artifact['envelope']['message_id'],code='accepted',detail='SYNTHETIC_MEDICAL_TEXT')])
    raw=json.dumps(ack).encode();item=dict(seq=1,event_key='late-ack',raw=None,payload=base64.b64encode(raw).decode(),created_at=100,
        metadata=dict(kind='ack',engine_id='controlled-engine',side='a',contour='local_stand',config_version='local-1',config_hash=config_hash(cfg),fingerprint=cfg['b']['fingerprint'],trusted=True,payload_sha256=digest(raw)))
    with SessionLocal() as db:
        queue.ingest(db,'controlled-engine',item);queue.refresh(db,db.get(m.AS2Dispatch,did));db.commit();evaluate(db)
        assert db.query(m.OpsIncident).filter_by(rule='possible_duplicate',active_condition=True).count()==1
        assert db.query(m.OpsIncident).filter_by(rule='policy_missing',active_condition=True).count()==1
        assert db.query(m.AS2Attempt).count()==1
    row=client.get(url).json();assert len([a for a in row['actions'] if a['kind']=='manual_submitted'])==1 and row['publications']==[]
    assert row['automatic']['transport_state']!='positive' and row['automatic']['ack_state']=='accepted'
    assert row['automatic']['send_hold'] and row['automatic']['hold_state']=='delivery_possible'


def test_publication_incidents_access_and_diagnostics(client,tmp_path,monkeypatch):
    from app.database import SessionLocal
    from app import models as m,operations as op,as2_queue as q
    from app.operations_monitor import evaluate
    from tests.test_e2b_import import Forms
    root,cid,artifact,xml,url,uid=setup(client,tmp_path,monkeypatch)
    dispatch,queue_url,payload=enqueue(client,cid,artifact);did=dispatch['id']
    row=client.get(url).json();assert row['automatic']['id']==did
    # Download is audited but never registered as a submission.
    response=client.post(url+'/download/xml',json=dict(expected_version=row['version'],reason='Download exact synthetic artifact'))
    assert response.status_code==200 and response.content==xml
    row=client.get(url).json();assert not row['sources'] and not row['manual_hold']
    page=client.get(f'/cases/{cid}/operations');assert page.status_code==200,page.text
    forms=Forms(page.text).forms;target=f'/cases/{cid}/operations/{row["id"]}/manual'
    assert target in forms
    assert client.post(target,data=dict(operation='decide',expected_version=row['version'],reason='Synthetic missing CSRF test')).status_code==403
    row=write(client,url,'deadline',deadline='2026-11-12',deadline_basis='Explicit test date selected by responsible person')
    assert row['deadline_precision']=='date' and row['deadline_utc'] is None
    stale=client.post(url+'/manual',json=dict(operation='decide',expected_version=row['version']-1,reason='Synthetic stale operation'))
    assert stale.status_code==409
    now=datetime(2026,11,14,12,tzinfo=timezone.utc)
    with SessionLocal() as db:
        evaluate(db,now);evaluate(db,now+timedelta(seconds=10))
        incidents=db.query(m.OpsIncident).filter_by(rule='submission_deadline').all();assert len(incidents)==1
        incident=incidents[0];iid=incident.id;v=incident.version
    response=client.post('/api/operations/incidents/'+iid,json=dict(expected_version=v,operation='close',reason='Acknowledged locally; evidence remains unresolved'))
    assert response.status_code==200,response.text
    with SessionLocal() as db:
        for minute in (2,4,6):evaluate(db,now+timedelta(minutes=minute))
        incident=db.get(m.OpsIncident,iid);assert incident.status=='closed' and incident.active_condition and incident.reminder_count==2
        assert db.query(m.OpsIncident).filter_by(rule='submission_deadline').count()==1
        assert db.get(m.AS2Dispatch,did).ack_state!='accepted'
    write(client,url,'decide')
    row=write(client,url,'submitted',submitted_at='2026-11-11T17:00:00+03:00',reference_number='SYNTHETIC_PACKAGE')
    source_key=next(k for k in row['sources'] if k.startswith('manual:'))
    response=client.post(url+'/publication',json=dict(expected_version=row['version'],source_key=source_key,status='confirmed_manual',source='Synthetic protected portal observation',published_at='2026-11-11T18:00:00+03:00',reason='SYNTHETIC_MEDICAL_TEXT reason never goes to diagnostics'))
    assert response.status_code==200,response.text
    row=response.json();first=row['publications'][0]
    assert first['published_at']=='2026-11-11T15:00:00+00:00'
    response=client.post(url+'/publication',json=dict(expected_version=row['version'],source_key=source_key,previous_id=first['id'],status='problem',source='Synthetic corrected observation',reason='Incorrect prior observation; retained as history'))
    assert response.status_code==200,response.text
    row=response.json();assert len(row['publications'])==2 and row['publications'][1]['previous_id']==first['id']
    assert client.get(f'/cases/{cid}/operations').status_code==200
    with SessionLocal() as db:
        evaluate(db,now+timedelta(minutes=8))
        assert db.get(m.OpsIncident,iid).active_condition is False
        # Marker in free-form transport error must not pass the diagnostic allowlist.
        intent=db.get(m.AS2Dispatch,did);intent.last_error='SYNTHETIC_SECRET_TOKEN';db.commit()
    row=client.get(url).json()
    result=client.post(url+'/download/diagnostic',json=dict(expected_version=row['version'],reason='Synthetic diagnostic export for inspection'))
    assert result.status_code==200,result.text
    with ZipFile(io.BytesIO(result.content)) as archive:
        assert archive.namelist()==['diagnostic.json'];raw=archive.read('diagnostic.json')
    assert b'SYNTHETIC_SECRET_TOKEN' not in raw and b'SYNTHETIC_MEDICAL_TEXT' not in raw and b'SYNTHETIC_PACKAGE' not in raw
    assert artifact['xml_sha256'].encode() in raw and b'confirmed_manual' in raw
    assert xml not in result.content
    assert client.get(url.replace(cid,'other-case')).status_code==404
    with SessionLocal() as db:
        user=db.get(m.User,uid);db.query(m.UserRole).filter_by(user_id=uid).delete();role=db.query(m.Role).filter_by(role_code='readonly_auditor').one();user.role='viewer';db.add(m.UserRole(user_id=uid,role_id=role.id));db.commit()
    assert client.post(url+'/publication',json=dict(expected_version=row['version'],source_key=source_key,status='confirmed_manual',source='forbidden',reason='Synthetic unauthorized confirmation')).status_code==403


def test_migration_0035_preserves_transport(tmp_path):
    from tests.test_migrations import migrate_test_database,application_command,database_contents
    path=tmp_path/'migration.db';result=migrate_test_database(path,'20261111_0035');assert result.returncode==0,result.stderr
    result=application_command(path,'-c','''
from app.database import SessionLocal
from app import models as m
from sqlalchemy import text
with SessionLocal() as db:
    user=m.User(email='synthetic@example.invalid');db.add(user);db.flush()
    for i in range(2):
        case=m.Case(case_number='SYNTH-'+str(i));db.add(case);db.flush()
        revision=m.IcsrRevision(case_id=case.id,revision_number=1,author_id=user.id,schema_version='pv1-clinical-1',snapshot_json='{}',checksum_sha256='a'*64,validation_json='{}',reason='Synthetic historical snapshot');db.add(revision);db.flush()
        artifact=m.IcsrExportArtifact(case_id=case.id,revision_id=revision.id,idempotency_key='key'+str(i),request_checksum='a'*64,revision_checksum='a'*64,snapshot_schema='pv1-clinical-1',profile_id='synthetic',profile_manifest_sha256='a'*64,profile_json='{}',exporter_version='synthetic',rules_version='synthetic',envelope_json='{}',validation_json='{}',xml_sha256='b'*64,storage_path='synthetic-never-read.xml',byte_length=0,locally_valid=False,xsd_valid=False,author_id=user.id,reason='Synthetic historical artifact');db.add(artifact);db.flush()
        db.execute(text("INSERT INTO tblAS2Dispatches(id,case_id,artifact_id,command_key,command_hash,contour,recipient,config_version,config_hash,config_json,state,transport_state,ack_state,next_at,version,author_id,reason,created_at,updated_at) VALUES (:id,:case,:artifact,:command,:hash,'local_stand','synthetic','local-1',:hash,'{}',:state,'pending','pending',0,1,:user,'Synthetic history',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),dict(id='d'+str(i),case=case.id,artifact=artifact.id,command='command'+str(i),hash='a'*64,state='uncertain' if i else 'locally_acknowledged',user=user.id))
        db.add(m.AS2Attempt(dispatch_id='d'+str(i),number=1,state='unknown' if i else 'waiting_mdn'))
    db.commit()
''');assert result.returncode==0,result.stderr
    schema,before=database_contents(path);result=migrate_test_database(path,'20261112_0036');assert result.returncode==0,result.stderr
    _,after=database_contents(path)
    for table,rows in before.items():
        if table=='alembic_version':continue
        assert ([r[:len(rows[0])] for r in after[table]] if rows else after[table])==rows
    assert all(not rows for name,rows in after.items() if name.startswith('tblOps'))
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT send_hold,hold_state FROM tblAS2Dispatches').fetchall()==[(0,'none'),(0,'none')]
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
