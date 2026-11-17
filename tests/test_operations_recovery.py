"""One actual offline integration backup + isolated running restore, with retained evidence."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4
import httpx
from tests.test_as2_local import PROJECT,ENGINE,FLAGS,stand,until,enqueue
from tests.test_operations import setup,write


def probe(command,**args):
    result=subprocess.run([str(ENGINE),'tests/operations_recovery_probe.py',command],input=json.dumps(args),cwd=PROJECT,
        capture_output=True,text=True,timeout=60,creationflags=FLAGS)
    assert result.returncode==0,result.stdout+result.stderr
    return result.stdout


def ports():
    handles=[socket.socket() for _ in range(3)]
    try:
        for h in handles:h.bind(('127.0.0.1',0))
        return [h.getsockname()[1] for h in handles]
    finally:
        for h in handles:h.close()


def test_coordinated_offline_backup_and_running_restore(client,tmp_path,monkeypatch):
    from app.database import SessionLocal,engine
    from app import models as m
    from app.as2_adapter import call
    from app.as2_settings import configuration
    from app.config import get_settings
    from app.operations_monitor import evaluate
    from tests.conftest import TEST_ADMIN_LOGIN,TEST_ADMIN_PASSWORD
    source,cid,artifact,xml,url,uid=setup(client,tmp_path,monkeypatch)
    dispatch,_,_=enqueue(client,cid,artifact);did=dispatch['id']
    source_database=Path(engine.url.database);source_storage=get_settings().storage_root
    password=secrets.token_urlsafe(32)  # memory only; never an argument, file or report
    destination=tmp_path/'backup';secret_component=tmp_path/'vault/keys.encrypted';restored=tmp_path/'restored'
    args=dict(root=str(PROJECT),database=str(source_database),storage=str(source_storage),config=str(source/'config.json'),destination=str(destination),secret_output=str(secret_component),password=password,offline=True)
    source_worker=None;web=None;source_stop=source/'test-worker.stop';restored_started=False
    try:
        stand(source,'start','--no-worker')
        def ready():
            try:return call('status') and call('status',side='b')
            except Exception:return False
        until(ready);source_engine_id=call('status')['engine_id']
        with (source/'test-worker.log').open('ab') as log:
            source_worker=subprocess.Popen([sys.executable,'-m','app.as2_worker','--stop-file',str(source_stop)],cwd=PROJECT,env=os.environ.copy(),stdout=log,stderr=log,creationflags=FLAGS)
        dispatch_url=f'/api/cases/{cid}/as2/{did}'
        complete=until(lambda:(r if (r:=client.get(dispatch_url).json())['state']=='locally_acknowledged' else None))
        ack=next(e for e in complete['events'] if e['kind']=='ack')
        ack_bytes=client.get(dispatch_url+'/events/'+ack['id']+'/payload').content
        source_stop.write_text('stop');source_worker.wait(15)
        write(client,url,'decide')
        row=write(client,url,'submitted',submitted_at='2026-11-11T17:00:00+03:00',reference_number='SYNTHETIC-MANUAL-RECEIPT')
        source_key=next(k for k in row['sources'] if k.startswith('manual:'))
        response=client.post(url+'/publication',json=dict(expected_version=row['version'],source_key=source_key,status='not_confirmed',source='Synthetic portal inspection',reason='Publication not observed during synthetic inspection'))
        assert response.status_code==200,response.text
        row=response.json();publication_id=row['publications'][0]['id'];manual_id=source_key.split(':')[1]
        # Different artifacts/intents let recovery preserve both unknown and never-sent states.
        extra=[]
        with SessionLocal() as db:revision=db.get(m.IcsrRevision,artifact['revision_id']);rid=revision.id;revision_sha=revision.checksum_sha256
        for _ in range(2):
            response=client.post(f'/api/cases/{cid}/revisions/{rid}/r3-artifacts',json=dict(idempotency_key=str(uuid4()),expected_revision_checksum=revision_sha,sender_id='SYNTH-SENDER',receiver_id='SYNTH-RECEIVER',purpose='local_test',scenario='initial',reason='Synthetic recovery pending state'))
            assert response.status_code==200 and response.json()['locally_valid'],response.text
            extra_artifact=response.json();new_dispatch,_,_=enqueue(client,cid,extra_artifact);extra.append((extra_artifact,new_dispatch))
        uncertain_id=extra[0][1]['id'];queued_id=extra[1][1]['id'];attempt_id=str(uuid4())
        with SessionLocal() as db:
            intent=db.get(m.AS2Dispatch,uncertain_id);intent.state='uncertain'
            db.add(m.AS2Attempt(id=attempt_id,dispatch_id=uncertain_id,number=1,state='unknown',engine_id=source_engine_id,message_id=attempt_id+'@pv1-local.invalid',send_count=1))
            db.commit();uncertain_path=db.get(m.IcsrExportArtifact,extra[0][0]['id']).storage_path
            evaluate(db)
        assert 'ACTIVE_BACKUP_REJECTED' in probe('reject-active',**args)
        assert not destination.exists()
        stand(source,'stop')
        # Wait only for this stand's guards; no process name killing.
        engine.dispose()
        from app.local_runtime import FileGuard
        def offline():
            try:
                with FileGuard(source/'runtime.lock',exclusive=True):return True
            except OSError:return False
        until(offline,8)
        assert 'UNCERTAINTY_AND_DURABLE_FENCE_PREPARED' in probe('uncertain',config=str(source/'config.json'),attempt_id=attempt_id,artifact_path=uncertain_path)
        assert 'EXPIRED_ARCHIVE_AND_NEGATIVE_CHECKS_VERIFIED' in probe('archive-expired',config=str(source/'config.json'))
        assert 'COORDINATED_BACKUP_VERIFIED' in probe('create',**args)
        before=hashlib.sha256(source_database.read_bytes()).hexdigest()
        chosen=ports()
        assert 'RESTORE_VERIFIED_OUTGOING_DISABLED' in probe('restore',snapshot=str(destination),destination=str(restored),secret_component=str(secret_component),password=password,ports=chosen[:2])
        report=json.loads((restored/'restore-report.json').read_text('utf-8'));assert report['recovery_complete'] and report['outgoing_disabled']
        assert len([c for c in report['certificates'] if c['validity_at_restore']=='expired'])==3
        assert 'historical receipt trust unchanged' in report['certificate_check']
        rotated=subprocess.run([str(ENGINE),'tools/as2_stand.py','rotate','--root',str(restored)],cwd=restored/'source',capture_output=True,text=True,timeout=20,creationflags=FLAGS)
        assert rotated.returncode==0,rotated.stderr
        assert json.loads((restored/'config.json').read_text('utf-8'))['runtime']['outgoing_disabled'] is True
        # Start only restored code/data on different loopback ports, reusing installed interpreters.
        result=subprocess.run([str(ENGINE),'tools/as2_stand.py','start','--root',str(restored),'--app-python',sys.executable],cwd=restored/'source',capture_output=True,text=True,timeout=20,creationflags=FLAGS)
        assert result.returncode==0,result.stderr;restored_started=True
        env=os.environ.copy();env.update(DATABASE_URL='sqlite:///'+(restored/'pv1.db').as_posix(),PV_STORAGE_ROOT=str(restored/'storage'),PV_AS2_CONFIG=str(restored/'config.json'),PV_RESTORE_PATH_MAP=str(restored/'path-map.json'))
        with (restored/'web.log').open('ab') as log:
            web=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(chosen[2])],cwd=restored/'source',env=env,stdout=log,stderr=log,creationflags=FLAGS)
        with httpx.Client(base_url='http://127.0.0.1:'+str(chosen[2]),timeout=4) as restored_client:
            def web_ready():
                try:return restored_client.get('/login').status_code==200
                except httpx.TransportError:return False
            until(web_ready)
            login=restored_client.post('/login',data=dict(login=TEST_ADMIN_LOGIN,password=TEST_ADMIN_PASSWORD,next_url='/'))
            assert login.status_code==303,login.text
            assert restored_client.get(artifact['download_url']).content==xml
            assert restored_client.get(dispatch_url+'/events/'+ack['id']+'/payload').content==ack_bytes
            saved=restored_client.get(url).json();assert saved['publications'][0]['id']==publication_id and any(a['id']==manual_id for a in saved['actions'])
            assert saved['automatic']['ack_state']=='accepted' and saved['publications'][0]['status']=='not_confirmed'
            revision=restored_client.get(f'/api/cases/{cid}/revisions/{rid}').json();assert revision['checksum']==revision_sha
            assert restored_client.get(f'/cases/{cid}/operations').status_code==200
            # The original XML still exists. Removing only the restored copy must fail.
            with sqlite3.connect(restored/'pv1.db') as db:
                original_path=Path(db.execute('SELECT storage_path FROM tblIcsrExportArtifacts WHERE id=?',(artifact['id'],)).fetchone()[0])
            copied=restored/'storage'/original_path.relative_to(source_storage)
            assert original_path.is_file() and copied.resolve().is_relative_to((restored/'storage').resolve())
            held=copied.with_suffix('.held');assert not held.exists();copied.rename(held)
            try:assert restored_client.get(artifact['download_url']).status_code==409
            finally:held.rename(copied)
            cfg=configuration(path=restored/'config.json')
            def restored_ready():
                try:return call('status',cfg=cfg)['engine_id']==source_engine_id
                except Exception:return False
            until(restored_ready)
            assert call('status',cfg=cfg)['outgoing_disabled'] is True
            try:call('send',dict(request_id=str(uuid4()),kind='icsr',config_version='local-1',payload='c3ludGhldGlj'),cfg=cfg)
            except ValueError:pass
            else:raise AssertionError('Restored sender accepted a new send')
            def worker_alive():
                with sqlite3.connect(restored/'pv1.db') as db:return db.execute("SELECT observed_at FROM tblOpsSignals WHERE key='worker'").fetchone()
            until(worker_alive)
            assert restored_client.get(f'/api/cases/{cid}/as2/{uncertain_id}').json()['state']=='uncertain'
            assert restored_client.get(f'/api/cases/{cid}/as2/{queued_id}').json()['state']=='queued'
            with sqlite3.connect(restored/'engine-a/engine.db') as db:
                assert db.execute('SELECT state,tries FROM outbound WHERE id=?',(attempt_id,)).fetchone()==('unknown',1)
            with sqlite3.connect(restored/'pv1.db') as db:assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert hashlib.sha256(source_database.read_bytes()).hexdigest()==before
        print('RECOVERY_PROOF: '+str(restored))
        print('RECOVERY: retained XML/ACK/revision/manual/publication/incidents, separate ports, restored keys, no source fallback, outgoing fenced, unknown preserved')
    finally:
        source_stop.write_text('stop')
        if source_worker and source_worker.poll() is None:source_worker.terminate();source_worker.wait(5)
        stand(source,'stop')
        if restored_started:
            subprocess.run([str(ENGINE),'tools/as2_stand.py','stop','--root',str(restored)],cwd=restored/'source',capture_output=True,text=True,timeout=15,creationflags=FLAGS)
        if web and web.poll() is None:web.terminate();web.wait(8)
