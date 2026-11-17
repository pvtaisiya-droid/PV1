"""One bounded developer rehearsal of the shipped UAT copy, ordinary authenticated HTML."""
from datetime import datetime,timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
import httpx

class Forms(HTMLParser):
    def __init__(self,html):
        super().__init__();self.forms=[];self.current=None;self.control=None;self.feed(html)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='form':self.current=dict(action=a.get('action',''),data={});self.forms.append(self.current)
        if self.current is None:return
        if tag=='input' and a.get('name') and (a.get('type') not in ('checkbox','radio') or 'checked' in a):self.current['data'][a['name']]=a.get('value','')
        if tag=='textarea':self.control=('textarea',a.get('name'));self.text=''
        if tag=='select':self.control=('select',a.get('name'));self.options=[]
        if tag=='option' and self.control and self.control[0]=='select':self.options.append((a.get('value',''),'selected' in a))
    def handle_data(self,data):
        if self.control and self.control[0]=='textarea':self.text+=data
    def handle_endtag(self,tag):
        if tag in ('textarea','select') and self.control and self.current:
            kind,name=self.control
            if name:self.current['data'][name]=self.text if kind=='textarea' else next((v for v,selected in self.options if selected),self.options[0][0] if self.options else '')
            self.control=None
        if tag=='form':self.current=None
    def one(self,action,**hidden):
        matches=[f for f in self.forms if f['action']==action and all(f['data'].get(k)==v for k,v in hidden.items())]
        assert len(matches)==1,(action,hidden,len(matches));return matches[0]['data']

def wait(fn,seconds=25):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        try:
            value=fn()
            if value:return value
        except (httpx.TransportError,ConnectionError):pass
        time.sleep(.2)
    raise AssertionError('Bounded local wait expired')

def main():
    root=Path(sys.argv[1]).resolve();manifest=json.loads((root/'acceptance.json').read_text('utf-8'))
    assert manifest['synthetic'] is True and manifest['format']=='pv1-uat-1'
    source=Path(manifest['source']);app=manifest['app_python'];env=os.environ.copy()
    env.update(PYTHONUTF8='1',DATABASE_URL='sqlite:///'+(root/'pv1.db').as_posix(),PV_STORAGE_ROOT=str(root/'storage'),PV_AS2_CONFIG=str(root/'config.json'))
    env.pop('PV_RESTORE_PATH_MAP',None)
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    def command(*args,input=None):
        result=subprocess.run([app,*map(str,args)],cwd=source,env=env,input=input,capture_output=True,text=True,timeout=45,creationflags=flags)
        assert result.returncode==0,result.stdout+result.stderr;return result.stdout
    def manage(action):return command('tools/acceptance_stand.py',action,'--root',root)
    password=secrets.token_urlsafe(24)
    command('-c',"import sys;from app.manage import bootstrap_admin;bootstrap_admin('uat.fn',sys.stdin.read(),recover=True)",input=password)
    try:
        manage('start')
        assert 'No new processes started' in manage('start')
        with httpx.Client(base_url='http://127.0.0.1:'+str(manifest['ports'][2]),timeout=8,follow_redirects=False) as client:
            wait(lambda:client.get('/login').status_code==200)
            assert client.post('/login',data=dict(login='uat.fn',password=password,next_url='/')).status_code==303
            def page(url):
                r=client.get(url);assert r.status_code==200,(url,r.status_code,r.text[:500]);return r
            def forms(url):return Forms(page(url).text)
            def submit(url,data):
                r=client.post(url,data=data);assert r.status_code==303,(url,r.status_code,r.text[:1000]);return r.headers['location']
            cid='uat-registered';case=manifest['cases'][0]
            if len(sys.argv)<3:
                for case in manifest['cases']:
                    cid=case['case_id'];page('/cases/'+cid);page('/cases/'+cid+'/clinical');page('/cases/'+cid+'/drug-products')
                    page('/cases/'+cid+'/revisions/'+case['revision_id']);page('/e2b/messages/'+case['followup_id'])
                cid='uat-registered';case=manifest['cases'][0]
                records=page(f'/api/cases/{cid}/clinical').json()['records']
                lab=next(r for r in records if r['kind']=='test');reporter=next(r for r in records if r['kind']=='sender')
                target=f'/cases/{cid}/clinical/test';data=forms(f'/cases/{cid}/clinical?kind=test&row_id='+lab['id']).one(target)
                assert 'raw:F.r.3.2' not in data and 'scalar:F.r.3.2' in data
                data.update({'change:F.r.3.2':'on','scalar:F.r.3.2':'1.5000','change_reason':'Учебная корректировка результата анализа без JSON'})
                missing=dict(data);missing.pop('csrf');assert client.post(target,data=missing).status_code==403
                submit(target,data);assert client.post(target,data=data).status_code==409
                target=f'/cases/{cid}/clinical/sender';data=forms(f'/cases/{cid}/clinical?kind=sender&row_id='+reporter['id']).one(target)
                data.update({'change:local.contacts':'on','part:local.contacts:value':'mailto:synthetic@example.invalid','part:local.contacts:use':'WP','change_reason':'Учебное дополнение контакта обычной формой'})
                submit(target,data)
                reaction=f'/cases/{cid}/reactions/registered-reaction1/edit';data=forms(reaction).one(reaction)
                data.update(reported_term='Синтетическая зарегистрированная реакция — уточнено',change_reason='Учебная правка реакции с сохранением источника');submit(reaction,data)
                product=f'/cases/{cid}/drug-editor/product';data=forms(f'/cases/{cid}/drug-editor/product?row_id=registered-product1').one(product)
                data.update(reported_product_name='Синтетический зарегистрированный препарат — уточнено',change_reason='Учебная правка наименования препарата');submit(product,data)
                study='uat-study';study_records=page('/api/cases/'+study+'/clinical').json()['records'];study_row=next(r for r in study_records if r['kind']=='study')
                target=f'/cases/{study}/clinical/study';data=forms(f'/cases/{study}/clinical?kind=study&row_id='+study_row['id']).one(target)
                data.update({'change:C.5.2':'on','raw:C.5.2':'Синтетическое исследование — уточнение протокола','change_reason':'Учебная правка названия исследования'});submit(target,data)
                target=f'/cases/{cid}/revisions';data=forms(f'/cases/{cid}/clinical').one(target);data['reason']='Учебная ревизия после исправлений в формах';submit(target,data)
            else:
                # Prior form checks succeeded; only clear the deliberately unmapped
                # reporter contact through its explicit audited form, then continue.
                records=page(f'/api/cases/{cid}/clinical').json()['records']
                reporter=next(r for r in records if r['kind']=='reporter' and r['fields'].get('local.contacts'))
                target=f'/cases/{cid}/clinical/reporter'
                data=forms(f'/cases/{cid}/clinical?kind=reporter&row_id='+reporter['id']).one(target)
                data.update({'change:local.contacts':'on','clear:local.contacts':'on','change_reason':'Учебная явная очистка неподдержанного контакта после диагностики XML'})
                submit(target,data)
                target=f'/cases/{cid}/revisions';data=forms(f'/cases/{cid}/clinical').one(target);data['reason']='Учебная ревизия после разбора блокирующего контакта';submit(target,data)
            revisions=page(f'/api/cases/{cid}/revisions').json();rid=revisions[-1]['id']
            target=f'/cases/{cid}/r3-export';data=forms(target+'?revision_id='+rid).one(target)
            data.update(sender_id='SYNTH-SENDER',receiver_id='SYNTH-RECEIVER',purpose='local_test',scenario='initial',reason='Учебная подготовка точных XML-байтов из новой ревизии')
            location=submit(target,data);result=page(location);assert 'Скачать сохранённый XML' in result.text
            aid=location.split('artifact_id=')[1].split('&')[0];artifact=page(f'/api/cases/{cid}/r3-artifacts/{aid}').json()
            raw=page(artifact['download_url']).content
            import hashlib
            assert hashlib.sha256(raw).hexdigest()==artifact['xml_sha256']
            target=f'/cases/{cid}/as2/enqueue';data=forms(f'/cases/{cid}/as2?artifact_id='+aid).one(target)
            data.update(config_version='local-1',reason='Учебная локальная передача через настоящий движок');submit(target,data)
            with sqlite3.connect(root/'pv1.db') as db:did=db.execute('SELECT id FROM tblAS2Dispatches WHERE artifact_id=?',(aid,)).fetchone()[0]
            automatic=wait(lambda:(r if (r:=page(f'/api/cases/{cid}/as2/{did}').json())['state']=='locally_acknowledged' else None))
            assert automatic['transport_state']=='positive' and automatic['ack_state']=='accepted'
            print('HTML_FORMS_AND_REAL_LOCAL_EXCHANGE_OK',flush=True)
            # Bounded explicit follow-up decision: apply report+narrative, keep other repeats.
            mid=case['followup_id'];info=page('/api/e2b/messages/'+mid).json();target='/e2b/messages/'+mid
            data=forms(target).one(target)
            for node in info['nodes']:
                if node['kind'] in ('report','narrative'):
                    assert len(node['candidates'])==1
                    data['match:'+node['key']]=node['candidates'][0]['id']
                else:data['match:'+node['key']]='ignore'
            data.update(reason='Учебный follow-up: принимаем даты и описание, остальные повторы сохраняем')
            location=submit(target,data);preview=page(location)
            assert 'Текущее значение' in preview.text and 'Входящее значение' in preview.text
            data=Forms(preview.text).one(target)
            for k in list(data):
                if k.startswith('choice|'):data[k]='accept'
            submit(target,data)
            after=page(f'/api/cases/{cid}/revisions').json();assert len(after)==len(revisions)+2
            # Separate manual facts/publication/incident, ordinary HTML controls.
            with sqlite3.connect(root/'pv1.db') as db:oid=db.execute('SELECT id FROM tblOpsSubmissions WHERE artifact_id=?',(aid,)).fetchone()[0]
            ops=f'/cases/{cid}/operations';target=ops+'/'+oid+'/manual'
            data=forms(ops).one(target,operation='decide');data['reason']='Учебное решение о резервной подаче с возможным дублем';submit(target,data)
            data=forms(ops).one(target,operation='submitted');data.update(submitted_at=datetime.now(timezone.utc).isoformat(),reference_number='SYNTHETIC-RECEIPT',reason='Учебная имитация ручной подачи, без обращения к АИС');submit(target,data)
            pageinfo=page(f'/api/cases/{cid}/operations/{oid}').json();manual_key=next(k for k in pageinfo['sources'] if k.startswith('manual:'))
            pub=ops+'/'+oid+'/publication';data=forms(ops).one(pub,source_key=manual_key);data.update(status='confirmed_manual',source='Синтетическое подтверждение для приёмки',reason='Учебная ручная проверка публикации без АИС');submit(pub,data)
            data=forms(ops).one(pub,source_key=manual_key);data.update(status='problem',source='Повторная синтетическая проверка',reason='Исправление ошибочной учебной отметки публикации');submit(pub,data)
            pageinfo=page(f'/api/cases/{cid}/operations/{oid}').json();assert len(pageinfo['publications'])==2
            with sqlite3.connect(root/'pv1.db') as db:uid=db.execute("SELECT id FROM tblUsers WHERE login='uat.fn'").fetchone()[0]
            data=forms(ops).one(target,operation='assign');data.update(owner_id=uid,reason='Назначение ответственного за учебный разбор');submit(target,data)
            command('-m','app.operations_monitor','--once','--stop-file',root/'one-pass.stop')
            pageinfo=page(f'/api/cases/{cid}/operations/{oid}').json();assert pageinfo['incidents']
            iid=pageinfo['incidents'][0]['id'];incident='/operations/incident/'+iid
            fs=forms('/operations');matches=[f for f in fs.forms if f['action']==incident]
            assert matches,'Incident HTML actions unavailable'
            data=matches[0]['data'];data.update(operation='acknowledge',reason='Учебное ознакомление, результат отправки не изменяем');submit(incident,data)
            assert page(f'/api/cases/{cid}/as2/{did}').json()['ack_state']=='accepted'
            print('FOLLOWUP_PUBLICATION_AND_INCIDENT_HTML_OK',flush=True)
        # Real restart, retained data; never recreate the database.
        manage('stop');manage('start')
        with httpx.Client(base_url='http://127.0.0.1:'+str(manifest['ports'][2]),timeout=5) as client:wait(lambda:client.get('/login').status_code==200)
        with sqlite3.connect(root/'pv1.db') as db:
            assert db.execute('SELECT count(*) FROM tblAS2Attempts WHERE dispatch_id=?',(did,)).fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM tblOpsPublications WHERE submission_id=?',(oid,)).fetchone()[0]==2
        print('CANDIDATE_START_RESTART_HISTORY_OK',flush=True)
    finally:manage('stop')

if __name__=='__main__':main()
