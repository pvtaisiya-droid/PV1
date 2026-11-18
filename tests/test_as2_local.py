"""Four bounded stage-4 scenarios. No regulator, real people or production secrets."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4
import pytest

PROJECT=Path(__file__).resolve().parents[1]
ENGINE=Path(os.environ.get('PV_AS2_ENGINE_PYTHON', str(PROJECT/'.tools/as2-venv/Scripts/python.exe')))
FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0


def run_engine(*args):
    result=subprocess.run([str(ENGINE),*map(str,args)],cwd=PROJECT,capture_output=True,text=True,timeout=40,creationflags=FLAGS)
    assert result.returncode==0,result.stdout+result.stderr
    return result


def stand(root,command,*extra):
    return run_engine('tools/as2_stand.py',command,'--root',root,*extra)


def prepare(root,monkeypatch):
    sockets=[socket.socket(),socket.socket()]
    try:
        for s in sockets:s.bind(('127.0.0.1',0))
        ports=[str(s.getsockname()[1]) for s in sockets]
    finally:
        for s in sockets:s.close()
    stand(root,'prepare','--ports',*ports)
    monkeypatch.setenv('PV_AS2_CONFIG',str(root/'config.json'))


def until(check,seconds=18):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=check()
        if value:return value
        time.sleep(.15)
    raise AssertionError('Bounded local wait expired')


def seed_artifact(client):
    from app.database import SessionLocal
    from app import models as m
    from app.config import get_settings
    from tests.r3_export_helpers import specimen
    data=specimen(get_settings().storage_root)
    cid=data['case']['id']
    with SessionLocal() as db:
        db.add_all([m.Case(**data['case'],case_number='SYNTH-AS2'),m.Case(id='other-case',case_number='SYNTH-OTHER')]);db.flush()
        for name in ('Patient','Reaction','CaseProduct','Attachment','CaseDrugDosage','CaseDrugSubstance','CaseDrugIndication','CaseDrugRechallenge','CaseProductReactionAssessment','CaseClinicalRecord'):
            for item in data['records'][name]:db.add(getattr(m,name)(**item));db.flush()
        db.commit();version=db.get(m.Case,cid).version
    response=client.post(f'/api/cases/{cid}/revisions',params={'expected_case_version':version},json={'reason':'Synthetic stage four immutable snapshot'})
    assert response.status_code==200,response.text
    rid=response.json()['id'];source=client.get(f'/api/cases/{cid}/revisions/{rid}').json()
    response=client.post(f'/api/cases/{cid}/revisions/{rid}/r3-artifacts',json=dict(idempotency_key=str(uuid4()),expected_revision_checksum=source['checksum'],sender_id='SYNTH-SENDER',receiver_id='SYNTH-RECEIVER',purpose='local_test',scenario='initial',reason='Synthetic AS2 exact saved artifact'))
    assert response.status_code==200,response.text
    artifact=response.json();assert artifact['locally_valid'],artifact
    return cid,artifact,client.get(artifact['download_url']).content


def enqueue(client,cid,artifact):
    payload=dict(command_key=str(uuid4()),config_version='local-1',expected_xml_sha256=artifact['xml_sha256'],reason='Synthetic authorized local delivery')
    url=f'/api/cases/{cid}/r3-artifacts/{artifact["id"]}/as2'
    response=client.post(url,json=payload);assert response.status_code==200,response.text
    return response.json(),url,payload


def engine_events(root,side):
    path=root/('engine-'+side)/'engine.db'
    if not path.exists():return []
    with sqlite3.connect(path) as db:
        return [(json.loads(meta),raw,payload) for meta,raw,payload in db.execute('SELECT metadata,raw,payload FROM events ORDER BY seq')]


def test_real_http_snapshot_mdn_ack_and_restart(client,tmp_path,monkeypatch):
    from app.as2_adapter import call
    from app.database import SessionLocal
    from app import models as m
    from tests.test_e2b_import import Forms
    root=tmp_path/'stand';prepare(root,monkeypatch)
    cid,artifact,xml=seed_artifact(client)
    page=client.get(f'/cases/{cid}/as2',params={'artifact_id':artifact['id']});assert page.status_code==200,page.text
    target=f'/cases/{cid}/as2/enqueue';form=Forms(page.text).forms[target];form.update(reason='Synthetic authorized form enqueue',config_version='local-1')
    no_csrf=dict(form);no_csrf.pop('csrf')
    assert client.post(target,data=no_csrf,follow_redirects=False).status_code==403
    response=client.post(target,data=form,follow_redirects=False);assert response.status_code==303,response.text
    assert client.post(target,data=form,follow_redirects=False).status_code==303
    with SessionLocal() as db:
        row=db.query(m.AS2Dispatch).one();did=row.id
        assert db.query(m.AS2Attempt).count()==0
    url=f'/api/cases/{cid}/as2/{did}'
    worker=None;stop=root/'test-worker.stop'
    try:
        stand(root,'start','--no-worker')
        def ready():
            try:return call('status') and call('status',side='b')
            except Exception:return False
        until(ready)
        engine_id=call('status')['engine_id']
        # Real worker process hands off once, then stops. No browser/DB consumer during replies.
        result=subprocess.run([sys.executable,'-m','app.as2_worker','--once'],cwd=PROJECT,env=os.environ.copy(),capture_output=True,text=True,timeout=20,creationflags=FLAGS)
        assert result.returncode==0,result.stderr
        until(lambda:len([x for x in engine_events(root,'a') if x[0]['kind'] in ('mdn','ack')])==2)
        received=[x for x in engine_events(root,'b') if x[0]['kind']=='received_icsr']
        assert len(received)==1 and received[0][2]==xml
        assert received[0][0]['trusted'] and received[0][0]['cipher']=='aes256_cbc'
        replies=engine_events(root,'a')
        mdn=next(x for x in replies if x[0]['kind']=='mdn')
        ack=next(x for x in replies if x[0]['kind']=='ack')
        assert mdn[0]['state']=='positive' and mdn[0]['signature_verified'] and mdn[0]['mic_verified'],mdn[0]
        assert ack[0]['trusted'] and ack[0]['encryption_verified'] and json.loads(ack[2])['artifact_sha256']==artifact['xml_sha256']
        # ACK gets its own genuine, signed asynchronous MDN at B.
        until(lambda:any(x[0]['kind']=='mdn' and x[0]['state']=='positive' for x in engine_events(root,'b')))
        with SessionLocal() as db:assert db.query(m.AS2Event).count()==0
        stand(root,'stop');time.sleep(.5);stand(root,'start','--no-worker');until(ready)
        assert call('status')['engine_id']==engine_id
        with (root/'test-worker.log').open('ab') as log:
            worker=subprocess.Popen([sys.executable,'-m','app.as2_worker','--stop-file',str(stop)],cwd=PROJECT,env=os.environ.copy(),stdout=log,stderr=log,creationflags=FLAGS)
        result=until(lambda:(r if (r:=client.get(url).json())['state']=='locally_acknowledged' else None))
        assert result['transport_state']=='positive' and result['ack_state']=='accepted' and result['publication']=='not_confirmed'
        assert len(result['attempts'])==1
        for event in result['events']:
            if event['kind']=='ack':assert client.get(url+'/events/'+event['id']+'/payload').content==ack[2]
        assert 'Локальные MDN и ACK подтверждены' in client.get(f'/cases/{cid}/as2').text
        assert client.get('/as2').status_code==200
        assert client.get(f'/api/cases/other-case/as2/{did}').status_code==404
        # Late identical wire notification is retained without applying twice.
        from email import message_from_bytes
        from urllib.request import Request,build_opener,ProxyHandler
        from app.as2_settings import configuration
        message=message_from_bytes(mdn[1]);body=mdn[1].split(b'\r\n\r\n',1)[1]
        headers=dict(message.items())
        with build_opener(ProxyHandler({})).open(Request(configuration()['a']['mdn_url'],data=body,headers=headers),timeout=3) as response:assert response.status==200
        until(lambda:any(e['kind']=='duplicate' for e in client.get(url).json()['events']))
        assert client.get(url).json()['state']=='locally_acknowledged'
        with SessionLocal() as db:
            assert db.query(m.IcsrRevision).count()==1 and db.query(m.IcsrExportArtifact).count()==1 and db.query(m.AS2Attempt).count()==1
        print('REAL_HTTP: exact XML, encrypted signed ICSR/ACK, verified async MDNs, durable restart and late duplicate')
    finally:
        stop.write_text('stop')
        if worker:
            try:worker.wait(15)
            except subprocess.TimeoutExpired:worker.terminate();worker.wait(5)
        stand(root,'stop')


def test_queue_uncertainty_events_leases_and_rights(client,tmp_path,monkeypatch):
    from app import as2_queue as q,as2_worker as worker,models as m
    from app.database import SessionLocal
    from app.as2_settings import configuration,config_hash,digest
    root=tmp_path/'stand';prepare(root,monkeypatch)
    cid,artifact,xml=seed_artifact(client);row,url,payload=enqueue(client,cid,artifact);did=row['id']
    assert client.post(url,json=payload).json()['id']==did
    assert client.post(url,json={**payload,'command_key':str(uuid4())}).status_code==409
    assert client.post(url,json={**payload,'signature_verified':True}).status_code==422
    def claim(owner):
        with SessionLocal() as db:return q.claim(db,owner)
    with ThreadPoolExecutor(2) as pool:claimed=list(pool.map(claim,('worker-one','worker-two')))
    assert claimed.count(did)==1
    with SessionLocal() as db:
        intent=db.get(m.AS2Dispatch,did);intent.lease_owner='test-worker'
        attempt=m.AS2Attempt(dispatch_id=did,number=1,state='requesting',engine_id='synthetic-engine');db.add(attempt);db.flush();aid=attempt.id;db.commit()
    calls=[]
    def fake_call(path,**kwargs):
        calls.append(path)
        return {'engine_id':'synthetic-engine'} if path=='status' else None
    monkeypatch.setattr(worker.adapter,'call',fake_call)
    monkeypatch.setattr(worker.adapter,'submit',lambda *a,**k:pytest.fail('Blind resend after uncertain handoff'))
    worker.process(did,'test-worker')
    with SessionLocal() as db:
        intent=db.get(m.AS2Dispatch,did);assert intent.state=='uncertain' and db.query(m.AS2Attempt).count()==1
        attempt=db.get(m.AS2Attempt,aid);attempt.message_id='technical@pv1-local.invalid';attempt.state='unknown';db.commit()
    cfg=configuration();seq=0
    def event(kind,**extra):
        nonlocal seq
        seq+=1
        metadata=dict(kind=kind,engine_id='synthetic-engine',side='a',contour='local_stand',config_version='local-1',config_hash=config_hash(cfg),request_id=aid,fingerprint=cfg['b']['fingerprint'],trusted=True,**extra)
        item=dict(seq=seq,event_key='synthetic-'+str(seq),metadata=metadata,raw=None,payload=None)
        return item
    def ingest(item):
        with SessionLocal() as db:
            e=q.ingest(db,'synthetic-engine',item);intent=db.get(m.AS2Dispatch,did);q.refresh(db,intent);db.commit();return e.id,intent.state
    ack=dict(profile='pv1-local-ack-1',batch_id=artifact['envelope']['batch_id'],artifact_sha256=artifact['xml_sha256'],original_as2_message_id='technical@pv1-local.invalid',results=[dict(message_id=artifact['envelope']['message_id'],code='accepted',detail='synthetic')])
    item=event('ack');raw=json.dumps(ack).encode();item['payload']=base64.b64encode(raw).decode();item['metadata']['payload_sha256']=digest(raw)
    key,state=ingest(item);assert state=='uncertain' # ACK before MDN cannot invent transport success
    assert ingest(item)[0]==key
    good=event('mdn',state='positive',signature_verified=True,mic_verified=True)
    assert ingest(good)[1]=='locally_acknowledged'
    unknown=event('mdn',state='positive');unknown['metadata']['request_id']=str(uuid4())
    unknown['raw']=base64.b64encode(b'Synthetic unmatched evidence').decode();unknown['metadata']['raw_sha256']=digest(b'Synthetic unmatched evidence')
    unknown_id,state=ingest(unknown);assert state=='locally_acknowledged'
    with SessionLocal() as db:assert db.get(m.AS2Event,unknown_id).processing_state=='unmatched'
    assert client.get(f'/api/as2/unmatched/{unknown_id}/raw').content==b'Synthetic unmatched evidence'
    assert client.get(f'/api/cases/{cid}/as2/{did}/events/{unknown_id}/raw').status_code==404
    assert f'/api/as2/unmatched/{unknown_id}/raw' in client.get('/as2').text
    bad=event('mdn',state='review');bad['metadata']['trusted']=False
    assert ingest(bad)[1]=='requires_review'
    rejected=deepcopy(ack);rejected['results'][0]['code']='rejected'
    item=event('ack');raw=json.dumps(rejected).encode();item['payload']=base64.b64encode(raw).decode();item['metadata']['payload_sha256']=digest(raw)
    assert ingest(item)[1]=='requires_review'
    read_url=f'/api/cases/{cid}/as2/{did}'
    state=client.get(read_url).json();assert state['ack_state']=='conflict'
    assert client.post(read_url+'/retry',json=dict(expected_version=state['version'],reason='Synthetic forbidden blind retry')).status_code==409
    with SessionLocal() as db:
        user=db.query(m.User).filter_by(login='test-admin').one();db.query(m.UserRole).filter_by(user_id=user.id).delete()
        role=db.query(m.Role).filter_by(role_code='readonly_auditor').one();user.role='viewer';db.add(m.UserRole(user_id=user.id,role_id=role.id));db.commit()
    assert client.post(read_url+'/reconcile',json=dict(expected_version=state['version'],reason='Synthetic unauthorized action')).status_code==403
    assert client.get(f'/api/cases/other-case/as2/{did}').status_code in (403,404)


def test_engine_crypto_faults_rotation_and_recovery(tmp_path,monkeypatch):
    root=tmp_path/'crypto';prepare(root,monkeypatch)
    result=run_engine('tests/as2_crypto_probe.py',root)
    assert 'CRYPTO_FAULTS_AND_ROTATION_OK' in result.stdout


def test_migration_0034_preserves_artifacts(tmp_path):
    from tests.test_migrations import migrate_test_database,application_command,database_contents
    path=tmp_path/'m.db'
    result=migrate_test_database(path,'20261111_0034');assert result.returncode==0,result.stderr
    result=application_command(path,'-c','''
from app.database import SessionLocal
from app import models as m
with SessionLocal() as db:
    user=m.User(email='synthetic@example.invalid');db.add(user);db.flush()
    for i in range(2):
        case=m.Case(case_number='SYNTH-'+str(i));db.add(case);db.flush()
        db.add(m.Reaction(case_id=case.id,reported_term='Synthetic reaction'))
        revision=m.IcsrRevision(case_id=case.id,revision_number=1,author_id=user.id,schema_version='pv1-clinical-1',snapshot_json='{}',checksum_sha256='a'*64,validation_json='{}',reason='Synthetic historical snapshot');db.add(revision);db.flush()
        db.add(m.IcsrExportArtifact(case_id=case.id,revision_id=revision.id,idempotency_key='key'+str(i),request_checksum='a'*64,revision_checksum='a'*64,snapshot_schema='pv1-clinical-1',profile_id='synthetic',profile_manifest_sha256='a'*64,profile_json='{}',exporter_version='synthetic',rules_version='synthetic',envelope_json='{}',validation_json='{}',xml_sha256='b'*64,storage_path='synthetic-never-read.xml',byte_length=0,locally_valid=False,xsd_valid=False,author_id=user.id,reason='Synthetic historical artifact'))
    db.commit()
''');assert result.returncode==0,result.stderr
    schema,before=database_contents(path)
    result=migrate_test_database(path,'20261111_0035');assert result.returncode==0,result.stderr
    after_schema,after=database_contents(path)
    assert all(after[name]==rows for name,rows in before.items() if name!='alembic_version')
    assert set(schema)-{r for r in schema if r[1]=='alembic_version'}<=set(after_schema)
    assert after['tblAS2Dispatches']==[] and after['alembic_version']==[('20261111_0035',)]
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE tblIcsrExportArtifacts SET reason='changed'")
        assert len(db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND (name LIKE 'as2_%_immutable' OR name LIKE 'no_delete_tblAS2%')").fetchall())==6
