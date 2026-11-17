"""Durable loopback host for the pinned pyas2lib protocol engine.

No CMS, signing, encryption, canonicalization or MIC calculation is implemented here.
SQLite inbox precedes HTTP acknowledgement and is independent of the PV1 database.
"""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime,timezone
from email import message_from_bytes
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import threading
import time
from urllib.error import URLError,HTTPError
from urllib.request import Request,build_opener,ProxyHandler
from urllib.parse import urlsplit,parse_qs
from uuid import uuid4
from xml.etree import ElementTree as ET
from app.as2_settings import registry,configuration,public,config_hash,digest,dumps,base_url
from app.as2_adapter import NoRedirect
from app.as2_ack import PROFILE

MAX_WIRE=40*1024*1024
# Upstream debug/error messages can contain MIME. Only our technical codes are logged.
logging.getLogger('pyas2lib').disabled=True


@contextmanager
def database(path):
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA busy_timeout=10000');db.execute('PRAGMA synchronous=FULL')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback();raise
    finally:db.close()


class Engine:
    def __init__(self,config_path,side):
        self.path=Path(config_path).resolve();self.side=side;self.other='b' if side=='a' else 'a'
        self.root=self.path.parent/('engine-'+side);self.root.mkdir(parents=True,exist_ok=True)
        self.db_path=self.root/'engine.db';self.stop=threading.Event()
        with database(self.db_path) as db:
            db.executescript('''
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS send_holds (id TEXT PRIMARY KEY, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS outbound (
 id TEXT PRIMARY KEY, config_version TEXT NOT NULL, config_hash TEXT NOT NULL,
 kind TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE, payload_sha TEXT NOT NULL,
 headers TEXT NOT NULL, wire BLOB NOT NULL, mic TEXT, digest_alg TEXT,
 destination TEXT NOT NULL, state TEXT NOT NULL, tries INTEGER NOT NULL DEFAULT 0,
 next_at REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,error TEXT);
CREATE TABLE IF NOT EXISTS inbox (
 id TEXT PRIMARY KEY, channel TEXT NOT NULL, raw_sha TEXT NOT NULL, raw BLOB NOT NULL,
 state TEXT NOT NULL, received_at REAL NOT NULL, duplicates INTEGER NOT NULL DEFAULT 0,
 UNIQUE(channel,raw_sha));
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE,
 metadata TEXT NOT NULL, raw BLOB, payload BLOB, created_at REAL NOT NULL);
''')
            db.execute("INSERT OR IGNORE INTO meta VALUES ('engine_id',?)",(str(uuid4()),))
            self.id=db.execute("SELECT value FROM meta WHERE key='engine_id'").fetchone()[0]
            # A previous owner may have died after the network accepted the message.
            for row in db.execute("SELECT * FROM outbound WHERE state='sending'").fetchall():
                db.execute("UPDATE outbound SET state='unknown',error='restart_during_http',updated_at=? WHERE id=?",(time.time(),row['id']))
                self.event(db,'restart:'+row['id'],dict(kind='transport',request_id=row['id'],message_id=row['message_id'],config_version=row['config_version'],state='unknown',reason='restart_during_http'))

    def cfg(self,version=None):return configuration(version,self.path)

    def event(self,db,key,metadata,raw=None,payload=None):
        data={**metadata,'contour':'local_stand','engine_id':self.id,'side':self.side}
        if raw is not None:data['raw_sha256']=digest(raw)
        if payload is not None:data['payload_sha256']=digest(payload)
        db.execute('INSERT OR IGNORE INTO events(event_key,metadata,raw,payload,created_at) VALUES (?,?,?,?,?)',(key,dumps(data),raw,payload,time.time()))

    def identities(self,cfg):
        from pyas2lib.as2 import Organization,Partner
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        now=datetime.now(timezone.utc)
        for side in (self.side,self.other):
            cert=x509.load_pem_x509_certificate(Path(cfg[side]['cert_file']).read_bytes())
            if not cert.not_valid_before_utc<=now<=cert.not_valid_after_utc:raise ValueError('certificate_expired_or_not_yet_valid')
            if cert.fingerprint(hashes.SHA256()).hex()!=cfg[side]['fingerprint']:raise ValueError('certificate_fingerprint_mismatch')
        own,other=cfg[self.side],cfg[self.other]
        org=Organization(as2_name=own['as2_id'],sign_key=Path(own['key_file']).read_bytes(),
            sign_key_pass=Path(own['password_file']).read_text('ascii').strip(),
            decrypt_key=Path(own['key_file']).read_bytes(),decrypt_key_pass=Path(own['password_file']).read_text('ascii').strip(),
            mdn_url=own['mdn_url'],domain='pv1-local.invalid')
        if digest(org.sign_key[1].asn1.dump())!=own['fingerprint']:raise ValueError('own_key_certificate_mismatch')
        ca=Path(cfg['ca_file']).read_bytes()
        # Also validate our certificate chain; self-signed bypass is explicitly off.
        Partner(as2_name=own['as2_id'],verify_cert=Path(own['cert_file']).read_bytes(),verify_cert_ca=ca,validate_certs=True,ignore_self_signed=False).load_verify_cert()
        partner=Partner(as2_name=other['as2_id'],verify_cert=Path(other['cert_file']).read_bytes(),verify_cert_ca=ca,
            encrypt_cert=Path(other['cert_file']).read_bytes(),encrypt_cert_ca=ca,validate_certs=True,ignore_self_signed=False,
            sign=True,encrypt=True,digest_alg='sha256',enc_alg='aes_256_cbc',key_enc_alg='rsaes_pkcs1v15',
            mdn_mode='ASYNC',mdn_digest_alg='sha256',canonicalize_as_binary=True)
        return org,partner

    @staticmethod
    def task_view(row):
        return {k:row[k] for k in ('id','config_version','config_hash','kind','message_id','payload_sha','mic','digest_alg','state','tries','created_at','updated_at','error')}

    def task(self,key):
        with database(self.db_path) as db:
            row=db.execute('SELECT * FROM outbound WHERE id=?',(key,)).fetchone()
            return self.task_view(row) if row else None

    def enqueue(self,key,raw,cfg,kind='icsr'):
        from pyas2lib.as2 import Message
        if len(raw)>32*1024*1024 or kind not in ('icsr','ack'):raise ValueError('invalid_payload')
        prior=self.task(key)
        if prior:
            if prior['payload_sha']!=digest(raw) or prior['config_hash']!=config_hash(cfg) or prior['kind']!=kind:raise ValueError('request_id_conflict')
            return prior
        org,partner=self.identities(cfg)
        msg=Message(sender=org,receiver=partner)
        # Upstream selects its lossless binary MIME path for octet-stream. XML media
        # type selects 7/8bit serialization and changes LF to CRLF in 1.4.4.
        msg.build(raw,filename='payload.xml' if kind=='icsr' else 'ack.json',content_type='application/octet-stream',message_id=key)
        with database(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM send_holds WHERE id=?',(key,)).fetchone():raise ValueError('manual_hold')
            db.execute('INSERT OR IGNORE INTO outbound(id,config_version,config_hash,kind,message_id,payload_sha,headers,wire,mic,digest_alg,destination,state,next_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (key,cfg['version'],config_hash(cfg),kind,msg.message_id,digest(raw),dumps(msg.headers),msg.content,msg.mic.decode(),msg.digest_alg,cfg[self.other]['url'],'pending',time.time(),time.time(),time.time()))
        prior=self.task(key)
        if prior['payload_sha']!=digest(raw) or prior['config_hash']!=config_hash(cfg):raise ValueError('request_id_conflict')
        return prior

    def hold(self,key):
        """Durable tombstone also fences an enqueue request arriving after this call."""
        with database(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE');db.execute('INSERT OR IGNORE INTO send_holds VALUES (?,?)',(key,time.time()))
            row=db.execute('SELECT * FROM outbound WHERE id=?',(key,)).fetchone()
            stopped=not row or row['state'] in ('cancelled','not_delivered') or (row['state']=='pending' and row['tries']==0)
            if row and (stopped or row['state']=='pending'):
                db.execute('UPDATE outbound SET state=?,error=? WHERE id=?',('cancelled' if stopped else 'unknown','manual_hold',key))
            self.event(db,'manual-hold:'+key,dict(kind='hold',request_id=key,state='engine_stopped' if stopped else 'delivery_possible',config_version=row['config_version'] if row else None))
            return dict(request_id=key,engine_id=self.id,hold_state='engine_stopped' if stopped else 'delivery_possible')

    def receive(self,channel,raw):
        if len(raw)>MAX_WIRE:raise ValueError('wire_size')
        key=str(uuid4());checksum=digest(raw)
        with database(self.db_path) as db:
            db.execute('INSERT OR IGNORE INTO inbox(id,channel,raw_sha,raw,state,received_at) VALUES (?,?,?,?,?,?)',(key,channel,checksum,raw,'pending',time.time()))
            row=db.execute('SELECT * FROM inbox WHERE channel=? AND raw_sha=?',(channel,checksum)).fetchone()
            if row['id']!=key:
                db.execute('UPDATE inbox SET duplicates=duplicates+1 WHERE id=?',(row['id'],))
                self.event(db,'duplicate:'+key,dict(kind='duplicate',original_inbox_id=row['id'],state='retained',raw_sha256=checksum))
            return row['id']

    def mdn(self,raw):
        from pyas2lib.as2 import Message,Mdn
        from pyas2lib.utils import unquote_as2name
        probe=Mdn();probe.payload=message_from_bytes(raw)
        try:mid,recipient=probe.detect_mdn()
        except Exception:return dict(kind='mdn',trusted=False,state='review',reason='mdn_structure_unknown'),None
        with database(self.db_path) as db:row=db.execute('SELECT * FROM outbound WHERE message_id=?',(mid,)).fetchone()
        meta=dict(kind='mdn',message_id=mid,trusted=False,state='review',reason='unknown_correlation')
        if not row:return meta,None
        cfg=self.cfg(row['config_version'])
        if config_hash(cfg)!=row['config_hash']:
            meta['reason']='configuration_changed';return meta,None
        org,partner=self.identities(cfg)
        meta.update(request_id=row['id'],config_version=cfg['version'],config_hash=config_hash(cfg),fingerprint=cfg[self.other]['fingerprint'])
        outer=message_from_bytes(raw)
        if unquote_as2name(outer.get('AS2-From',''))!=partner.as2_name or unquote_as2name(outer.get('AS2-To',''))!=org.as2_name or recipient!=partner.as2_name:
            meta['reason']='mdn_party_mismatch';return meta,None
        original=Message(sender=org,receiver=partner);original.message_id=mid;original.mic=row['mic'].encode();original.digest_alg=row['digest_alg']
        mdn=Mdn();status,detail=mdn.parse(raw,lambda found,who:original if found==mid and who==partner.as2_name else None)
        # Library performs signature + MIC comparison; additionally require a MIC and matching algorithm.
        parts=[p for p in mdn.payload.walk() if p.get_content_type()=='message/disposition-notification'] if mdn.payload else []
        received=parts[0].get_payload()[-1].get('Received-Content-MIC','') if len(parts)==1 else ''
        bits=[x.strip() for x in received.split(',')]
        mic_ok=len(bits)==2 and bits[0]==row['mic'] and bits[1].replace('-','').lower()==row['digest_alg']
        signature_ok=mdn.digest_alg=='sha256'
        meta.update(disposition=status,mic=received,mic_algorithm=row['digest_alg'],signature_verified=signature_ok,mic_verified=mic_ok,
                    trusted=signature_ok and mic_ok,reason='verified' if signature_ok and mic_ok else 'mdn_signature_or_mic_failed',
                    state='positive' if signature_ok and mic_ok and status=='processed' else 'negative' if signature_ok and mic_ok else 'review')
        return meta,None

    def incoming_message(self,raw):
        from pyas2lib.as2 import Message
        from pyas2lib.utils import unquote_as2name
        from asn1crypto import cms
        headers=message_from_bytes(raw);last=None
        for cfg in reversed(registry(self.path)['versions']):
            if unquote_as2name(headers.get('AS2-From',''))!=cfg[self.other]['as2_id'] or unquote_as2name(headers.get('AS2-To',''))!=cfg[self.side]['as2_id']:continue
            if headers.get('Receipt-Delivery-Option')!=cfg[self.other]['mdn_url']:
                return dict(kind='incoming',trusted=False,state='review',reason='unexpected_async_mdn_address'),None,None,None
            options=headers.get('Disposition-Notification-Options','').lower().replace(' ','')
            if options not in ('signed-receipt-protocol=required,pkcs7-signature;signed-receipt-micalg=optional,sha256','signed-receipt-protocol=required,pkcs7-signature;signed-receipt-micalg=required,sha256'):
                return dict(kind='incoming',trusted=False,state='review',reason='unexpected_mdn_policy'),None,None,None
            try:
                org,partner=self.identities(cfg);msg=Message()
                status,exception,mdn=msg.parse(raw,find_org_cb=lambda key:org if key==org.as2_name else None,find_partner_cb=lambda key:partner if key==partner.as2_name else None)
                # pyas2lib 1.4.4 returns enc_alg=None for AES despite successful decryption.
                # Inspect only declared algorithm metadata with its ASN.1 dependency, not crypto.
                declared=cms.ContentInfo.load(headers.get_payload(decode=True))['content']
                cipher=declared['encrypted_content_info']['content_encryption_algorithm']['algorithm'].native
                key_algorithm=declared['recipient_infos'][0].chosen['key_encryption_algorithm']['algorithm'].native
                good=status=='processed' and not exception[0] and msg.encrypted and msg.signed and msg.digest_alg=='sha256' and cipher=='aes256_cbc' and key_algorithm=='rsaes_pkcs1v15'
                meta=dict(kind='ack' if self.side=='a' else 'received_icsr',message_id=msg.message_id,config_version=cfg['version'],config_hash=config_hash(cfg),
                    fingerprint=cfg[self.other]['fingerprint'],cipher=cipher,key_encryption=key_algorithm,signature_verified=good,encryption_verified=good,trusted=good,state='verified' if good else 'review',reason='verified' if good else 'message_crypto_failed')
                last=(meta,msg.content if good else None,mdn,cfg)
                if good:return last
            except Exception:last=(dict(kind='incoming',trusted=False,state='review',reason='certificate_or_crypto_failed',config_version=cfg['version']),None,None,cfg)
        return last or (dict(kind='incoming',trusted=False,state='review',reason='unknown_as2_party'),None,None,None)

    def queue_mdn(self,db,inbox_id,mdn,cfg):
        if mdn is None or mdn.mdn_mode!='ASYNC' or mdn.mdn_url!=cfg[self.other]['mdn_url']:return
        key='mdn-'+inbox_id
        db.execute('INSERT OR IGNORE INTO outbound(id,config_version,config_hash,kind,message_id,payload_sha,headers,wire,mic,digest_alg,destination,state,next_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (key,cfg['version'],config_hash(cfg),'mdn',mdn.message_id,digest(mdn.content),dumps(mdn.headers),mdn.content,None,'sha256',mdn.mdn_url,'pending',time.time(),time.time(),time.time()))

    def make_ack(self,payload,mid):
        if b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():raise ValueError('xml_dtd')
        root=ET.fromstring(payload);h='{urn:hl7-org:v3}'
        if root.tag!=h+'MCCI_IN200100UV01':raise ValueError('not_ich_batch')
        batch=root.find(h+'id');messages=root.findall(h+'PORR_IN049016UV')
        results=[dict(message_id=m.find(h+'id').get('extension'),code='accepted',detail='Synthetic receiver archived exact bytes; not regulatory acceptance') for m in messages]
        if batch is None or not results or any(not r['message_id'] for r in results):raise ValueError('xml_identity')
        return dumps(dict(profile=PROFILE,batch_id=batch.get('extension'),original_as2_message_id=mid,artifact_sha256=digest(payload),results=results)).encode()

    def process_inbox(self):
        with database(self.db_path) as db:row=db.execute("SELECT * FROM inbox WHERE state='pending' ORDER BY received_at LIMIT 1").fetchone()
        if not row:return False
        raw=bytes(row['raw']);mdn=cfg=payload=None
        try:
            if row['channel']=='mdn':meta,payload=self.mdn(raw)
            else:meta,payload,mdn,cfg=self.incoming_message(raw)
        except Exception:meta=dict(kind=row['channel'],trusted=False,state='review',reason='protocol_processing_failed')
        # Queue a separate ACK before marking the inbox consumed; its deterministic key is idempotent.
        if self.side=='b' and meta.get('trusted') and payload is not None:
            try:self.enqueue('ack-'+row['id'],self.make_ack(payload,meta['message_id']),cfg,'ack')
            except Exception:meta['application']='requires_review'
        with database(self.db_path) as db:
            self.event(db,'inbox:'+row['id'],{**meta,'inbox_id':row['id']},raw,payload)
            if mdn is not None and cfg:self.queue_mdn(db,row['id'],mdn,cfg)
            db.execute("UPDATE inbox SET state='processed' WHERE id=?",(row['id'],))
        return True

    def process_outbound(self):
        if self.cfg().get('_runtime',{}).get('outgoing_disabled'):return False
        with database(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM outbound WHERE state='pending' AND next_at<=? ORDER BY CASE kind WHEN 'mdn' THEN 0 ELSE 1 END,created_at LIMIT 1",(time.time(),)).fetchone()
            if not row:return False
            if db.execute('SELECT 1 FROM send_holds WHERE id=?',(row['id'],)).fetchone():
                db.execute("UPDATE outbound SET state='unknown',error='manual_hold' WHERE id=?",(row['id'],));return True
            db.execute("UPDATE outbound SET state='sending',tries=tries+1,updated_at=? WHERE id=?",(time.time(),row['id']))
        state,error='unknown',None;cfg=self.cfg(row['config_version']);count=row['tries']+1
        try:
            # Certificate validity is also checked immediately before each send/retry.
            if config_hash(cfg)!=row['config_hash']:raise ValueError('configuration_changed')
            self.identities(cfg)
        except Exception:state,error='not_delivered','certificate_invalid_before_http'
        else:
            try:
                req=Request(row['destination'],data=bytes(row['wire']),headers=json.loads(row['headers']),method='POST')
                with build_opener(ProxyHandler({}),NoRedirect()).open(req,timeout=cfg['http_timeout']) as response:
                    response.read(1024)
                    state='http_accepted' if row['kind']=='mdn' else 'waiting_mdn'
            except URLError as exc:
                reason=getattr(exc,'reason',None)
                if isinstance(reason,ConnectionRefusedError) or getattr(reason,'winerror',None)==10061:
                    state='pending' if count<cfg['max_connect_attempts'] else 'not_delivered';error='connection_refused_before_delivery'
                else:state,error='unknown','http_result_unknown'
            except (TimeoutError,OSError):state,error='unknown','http_result_unknown'
        with database(self.db_path) as db:
            db.execute('UPDATE outbound SET state=?,error=?,next_at=?,updated_at=? WHERE id=?',(state,error,time.time()+min(2**count,10),time.time(),row['id']))
            self.event(db,'http:'+row['id']+':'+str(count),dict(kind='transport',request_id=row['id'],message_id=row['message_id'],config_version=row['config_version'],config_hash=row['config_hash'],state=state,reason=error,try_number=count))
        return True

    def events(self,after):
        with database(self.db_path) as db:rows=db.execute('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 1',(after,)).fetchall()
        return [dict(seq=r['seq'],event_key=r['event_key'],metadata=json.loads(r['metadata']),created_at=r['created_at'],
                     raw=base64.b64encode(r['raw']).decode() if r['raw'] is not None else None,payload=base64.b64encode(r['payload']).decode() if r['payload'] is not None else None) for r in rows]

    def loop(self):
        while not self.stop.is_set():
            try:
                worked=self.process_inbox();worked=self.process_outbound() or worked
            except Exception:
                # Inbox/task remains durable. No medical content or traceback in ordinary logs.
                print('engine_processing_requires_review',flush=True);worked=False
            self.stop.wait(.05 if worked else .25)


def _serve(path,side):
    engine=Engine(path,side);cfg=engine.cfg();token=Path(cfg[side]['token_file']).read_text('ascii').strip()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def response(self,status,obj):
            raw=dumps(obj).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if not self.path.startswith('/control/') or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):return self.response(403,{'error':'forbidden'})
            try:
                path=urlsplit(self.path)
                if path.path=='/control/status':
                    from cryptography import x509
                    active=engine.cfg();certificates=[]
                    for c in registry(path=engine.path)['versions']:
                        for party in ('a','b'):
                            cert=x509.load_pem_x509_certificate(Path(c[party]['cert_file']).read_bytes())
                            certificates.append(dict(config_version=c['version'],side=party,fingerprint=c[party]['fingerprint'],expires_at=cert.not_valid_after_utc.isoformat()))
                    return self.response(200,dict(engine_id=engine.id,side=side,version='pyas2lib-1.4.4',active=active['version'],outgoing_disabled=active.get('_runtime',{}).get('outgoing_disabled',False),certificates=certificates))
                if path.path.startswith('/control/task/'):
                    row=engine.task(path.path.rsplit('/',1)[-1]);return self.response(200 if row else 404,row)
                if path.path=='/control/events':return self.response(200,dict(engine_id=engine.id,events=engine.events(int(parse_qs(path.query).get('after',['0'])[0]))))
                return self.response(404,{'error':'not_found'})
            except Exception:return self.response(400,{'error':'control_request_invalid'})
        def do_POST(self):
            try:
                length=int(self.headers.get('Content-Length','-1'))
                if not 0<=length<=MAX_WIRE or self.headers.get('Transfer-Encoding'):return self.response(413,{'error':'length_required_or_limit'})
                body=self.rfile.read(length)
                if len(body)!=length:return self.response(400,{'error':'incomplete_body'})
                if self.path.startswith('/control/'):
                    if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):return self.response(403,{'error':'forbidden'})
                    if self.path=='/control/shutdown':
                        self.response(200,{'engine_id':engine.id});engine.stop.set();threading.Thread(target=server.shutdown,daemon=True).start();return
                    data=json.loads(body)
                    if self.path=='/control/hold':
                        from uuid import UUID
                        UUID(data['request_id'])
                        if side!='a':return self.response(403,{'error':'outbound_control_scope'})
                        return self.response(200,engine.hold(data['request_id']))
                    if self.path=='/control/send':
                        from uuid import UUID
                        UUID(data['request_id'])
                        if side!='a' or data.get('kind')!='icsr':return self.response(403,{'error':'outbound_control_scope'})
                        if engine.cfg().get('_runtime',{}).get('outgoing_disabled'):return self.response(409,{'error':'recovery_outgoing_disabled'})
                        row=engine.enqueue(data['request_id'],base64.b64decode(data['payload'],validate=True),engine.cfg(data['config_version']))
                        return self.response(200,row)
                    return self.response(404,{'error':'not_found'})
                if self.path not in ('/as2','/mdn'):return self.response(404,{'error':'not_found'})
                # HTTP header framing only; MIME canonicalization belongs entirely to pyas2lib.
                raw=''.join(k+': '+v+'\r\n' for k,v in self.headers.items()).encode('utf-8')+b'\r\n'+body
                engine.receive('mdn' if self.path=='/mdn' else 'as2',raw)
                return self.response(200,{'stored':True})
            except Exception:return self.response(400,{'error':'request_rejected'})
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        def get_request(self):
            connection,address=super().get_request();connection.settimeout(10);return connection,address
    server=Server(('127.0.0.1',urlsplit(base_url(cfg,side)).port),Handler)
    worker=threading.Thread(target=engine.loop,daemon=True);worker.start()
    print('AS2 local receiver ready '+side,flush=True)
    try:server.serve_forever(poll_interval=.2)
    finally:engine.stop.set();server.server_close();worker.join(12)


def serve(path,side):
    # Acquire before crash recovery: a second process must never recover a live sender.
    root=Path(path).resolve().parent/('engine-'+side);root.mkdir(parents=True,exist_ok=True)
    with (root/'process.lock').open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise SystemExit('This local engine already has an owner.') from None
        from app.local_runtime import runtime_guard
        with runtime_guard(path):_serve(path,side)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--side',choices=('a','b'),required=True)
    args=parser.parse_args();serve(args.config,args.side)
