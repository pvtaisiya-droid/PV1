"""Local UAT instance. Preparation is create-only; start/stop never reset its history."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
sys.dont_write_bytecode=True
FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0

def environment(root):
    env=os.environ.copy();env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',DATABASE_URL='sqlite:///'+(root/'pv1.db').as_posix(),PV_STORAGE_ROOT=str(root/'storage'),PV_AS2_CONFIG=str(root/'config.json'))
    env['PV_AUTH_COOKIE_NAME']='pv_uat_'+hashlib.sha256(str(root).encode()).hexdigest()[:12]
    env['PV_INSTANCE_LABEL']='Объединённая учебная версия — только синтетические данные'
    env.pop('PV_RESTORE_PATH_MAP',None);return env

def run(args,root):
    result=subprocess.run(list(map(str,args)),cwd=PROJECT,env=environment(root),creationflags=FLAGS,capture_output=True,text=True,encoding='utf-8',errors='replace')
    if result.stdout:print(result.stdout.strip(),flush=True)
    if result.returncode:
        if result.stderr:print(result.stderr.strip(),file=sys.stderr)
        raise RuntimeError('Instance command failed; see its bounded output')

def seed(root):
    os.environ.update(environment(root)) # before any application import
    from app.database import SessionLocal
    from app import models as m,clinical_revision,r3_artifacts,operations
    from app.r3_export import export_revision,dump
    from app.e2b_import import E2BPackageService
    from tests.r3_export_helpers import specimen,revision,envelope
    listing=[];materials=root/'materials';materials.mkdir()
    with SessionLocal() as db:
        user=db.query(m.User).filter_by(login='pv.admin').one();user.login='uat.fn';db.commit()
        operations.save_policy(db,'local-1',operations.PolicyWrite(expected_number=0,autopublish='unknown',mdn_seconds=30,ack_seconds=60,publication_seconds=120,approaching_seconds=86400,certificate_days=7,health_seconds=60,reminder_seconds=3600,reminder_limit=2,owner_id=user.id,reason='Синтетическая политика приёмки; не условия РЗН'))
        for study,code,title in [(False,'registered','СИНТЕТИЧЕСКИЙ — зарегистрированный препарат'),(True,'study','СИНТЕТИЧЕСКИЙ — клиническое исследование')]:
            data=specimen(root/'storage'/code,study=study)
            # Stable distinct synthetic identities, no clinical/transport successes invented.
            remap={v['id']:code+'-'+v['id'] for rows in data['records'].values() for v in rows}
            remap[data['case']['id']]='uat-'+code
            for obj in [data['case']]+[v for rows in data['records'].values() for v in rows]:
                for k,v in list(obj.items()):
                    if (k=='id' or k.endswith('_id')) and isinstance(v,str) and v in remap:obj[k]=remap[v]
            data['case'].update(case_number='UAT-'+code.upper(),worldwide_case_id='RU-SYNTH-'+code.upper(),local_sender_case_id='SYNTH-'+code.upper())
            for i,p in enumerate(data['records']['CaseProduct'],1):p['reported_product_name']=f'Синтетический {code} препарат {i}'
            for i,r in enumerate(data['records']['Reaction'],1):r['reported_term']=f'Синтетическая {code} реакция {i}'
            # Avoid an artificial death/parent history distracting from the two bounded UAT cases.
            excluded={'parent','parent_medical_history','death_cause'}
            data['records']['CaseClinicalRecord']=[r for r in data['records']['CaseClinicalRecord'] if r['kind'] not in excluded]
            for r in data['records']['CaseClinicalRecord']:
                fields=json.loads(r['fields_json'])
                if r['kind']=='patient':fields.pop('D.9.1',None);fields.pop('D.9.3',None)
                if r['kind']=='narrative':fields['H.1']['raw']=fields['H.1']['value']=title+'. Учебный текст; коды MedDRA вымышлены, словарь не проверен.'
                r['fields_json']=dump(fields)
            db.add(m.Case(**data['case']));db.flush()
            for name in ('Patient','Reaction','CaseProduct','Attachment','CaseDrugDosage','CaseDrugSubstance','CaseDrugIndication','CaseDrugRechallenge','CaseProductReactionAssessment','CaseClinicalRecord'):
                for item in data['records'][name]:db.add(getattr(m,name)(**item));db.flush()
            db.commit();cid=data['case']['id']
            rev=clinical_revision.create(db,cid,'Подготовка синтетического первичного случая для приёмки',author_id=user.id);db.commit()
            artifact=r3_artifacts.prepare(db,cid,rev.id,r3_artifacts.PrepareRequest(idempotency_key=uuid4(),expected_revision_checksum=rev.checksum_sha256,sender_id='SYNTH-SENDER',receiver_id='SYNTH-RECEIVER',purpose='local_test',scenario='initial',reason='Синтетический первичный XML; передача ещё не выполнялась'))
            if not artifact.locally_valid:raise ValueError('Synthetic fixture failed local export: '+artifact.validation_json)
            initial=r3_artifacts.verified_bytes(artifact);(materials/(code+'-initial.xml')).write_bytes(initial)
            follow=deepcopy(data)
            for r in follow['records']['CaseClinicalRecord']:
                fields=json.loads(r['fields_json'])
                if r['kind']=='narrative':fields['H.1']['raw']=fields['H.1']['value']=title+'. Последующая информация: добавлено наблюдение после отмены препарата.'
                if r['kind']=='report':fields['C.1.5']['raw']='20260307';fields['C.1.5']['value']='2026-03-07';fields['C.1.5']['parsed']='2026-03-07'
                r['fields_json']=dump(fields)
            env=envelope();env.update(batch_id='SYNTH-FU-B-'+code,message_id='SYNTH-FU-M-'+code,scenario='followup')
            generated=export_revision(revision(follow),env,root/'storage')
            if not generated['locally_valid']:raise ValueError('Synthetic follow-up export failed')
            (materials/(code+'-followup.xml')).write_bytes(generated['xml'])
            batch=E2BPackageService().prepare_batch(db,filename=code+'-followup.xml',content=generated['xml'],uploaded_by_user_id=user.id)
            incoming=db.query(m.IcsrMessage).filter_by(batch_id=batch.id).one()
            if incoming.status!='review':raise ValueError('Follow-up not reviewable: '+incoming.issues_json)
            listing.append(dict(case_id=cid,title=title,artifact_id=artifact.id,revision_id=rev.id,followup_id=incoming.id))
        return listing

def prepare(root,engine,ports):
    if root.exists():raise ValueError('Каталог уже существует. Подготовка не перезаписывает историю; используйте start.')
    if len(set(ports))!=3:raise ValueError('Нужны три разных loopback-порта')
    for port in ports:
        with socket.socket() as sock:sock.bind(('127.0.0.1',port))
    root.mkdir(parents=True)
    run([engine,'tools/as2_stand.py','prepare','--root',root,'--ports',*ports[:2]],root)
    run([sys.executable,'-m','app.manage','init-db'],root)
    listing=seed(root)
    manifest=dict(format='pv1-uat-1',source=str(PROJECT),app_python=sys.executable,engine_python=str(engine),ports=ports,login='uat.fn',cases=listing,created_at=datetime.now(timezone.utc).isoformat(),password='not configured; run password command',synthetic=True)
    (root/'acceptance.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    # Local shortcuts contain paths only, no credentials. No global execution policy change.
    for action in ('password','start','status','stop'):
        quote=lambda x:"'"+str(x).replace("'","''")+"'"
        script='& '+quote(sys.executable)+' '+quote(PROJECT/'tools/acceptance_stand.py')+' '+action+' --root '+quote(root)+'\n'
        (root/(action+'.ps1')).write_text(script,'utf-8-sig')
    lines=['<meta charset="utf-8"><h1>PV1 — синтетическая приёмка</h1><p>Сначала password.ps1, затем start.ps1. Логин uat.fn. Пароль не сохранён.</p>']
    for entry in listing:
        lines.append(f'<p>{entry["title"]}: <a href="http://127.0.0.1:{ports[2]}/cases/{entry["case_id"]}/clinical">случай</a> · <a href="http://127.0.0.1:{ports[2]}/e2b/messages/{entry["followup_id"]}">follow-up</a></p>')
    (root/'START-HERE.html').write_text('\n'.join(lines),'utf-8')
    print('Prepared synthetic UAT instance. Set password with the password command; history is preserved.')

def manage(action,root):
    saved=json.loads((root/'acceptance.json').read_text('utf-8'))
    if saved['format']!='pv1-uat-1' or Path(saved['source']).resolve()!=PROJECT:raise ValueError('Run the tool from the recorded candidate source')
    engine=Path(saved['engine_python']);app=Path(saved['app_python'])
    if action=='password':
        os.environ.update(environment(root))
        from getpass import getpass
        from app.manage import bootstrap_admin
        password=getpass('Пароль '+saved['login']+' (не менее 12 символов): ')
        if password!=getpass('Повторите пароль: '):raise ValueError('Пароли не совпадают')
        bootstrap_admin(saved['login'],password,recover=True);print('Password set; value was not stored outside its hash.');return
    if action=='start':
        from app.local_runtime import FileGuard
        try:
            with FileGuard(root/'runtime.lock',exclusive=True):pass
        except OSError:print('Instance already active or stopping; use status. No new processes started.');return
        (root/'web.stop').unlink(missing_ok=True)
        run([engine,'tools/as2_stand.py','start','--root',root,'--app-python',app],root)
        with (root/'web.log').open('ab') as log:
            child=subprocess.Popen([str(app),str(PROJECT/'tools/acceptance_web.py'),'--stop',str(root/'web.stop'),'--port',str(saved['ports'][2])],cwd=PROJECT,env=environment(root),stdout=log,stderr=log,creationflags=FLAGS)
        (root/'web-process.json').write_text(json.dumps(dict(pid=child.pid,source=str(PROJECT))),encoding='utf-8')
        print('Local web: http://127.0.0.1:'+str(saved['ports'][2])+'/login')
    elif action=='stop':
        (root/'web.stop').write_text('stop','ascii');run([engine,'tools/as2_stand.py','stop','--root',root],root)
        from app.local_runtime import FileGuard
        for _ in range(40):
            try:
                with FileGuard(root/'runtime.lock',exclusive=True):print('Own instance processes stopped.');return
            except OSError:time.sleep(.25)
        raise RuntimeError('Some instance process is still active; inspect its saved PID/log. No foreign process terminated.')
    else:
        run([engine,'tools/as2_stand.py','status','--root',root],root)
        import urllib.request
        try:
            with urllib.request.urlopen('http://127.0.0.1:'+str(saved['ports'][2])+'/health',timeout=2) as response:print('Web health: '+str(response.status))
        except OSError:print('Web not reachable')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','password','start','stop','status']);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--engine-python',type=Path);parser.add_argument('--ports',type=int,nargs=3,default=[18841,18842,18843]);args=parser.parse_args()
    root=args.root.resolve()
    if args.action=='prepare':
        if not args.engine_python:parser.error('--engine-python required for preparation')
        prepare(root,args.engine_python.resolve(),args.ports)
    else:manage(args.action,root)
