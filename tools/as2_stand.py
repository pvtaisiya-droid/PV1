"""Reproducible Windows local stand. Run with .tools/as2-venv Python."""
import argparse
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from app.as2_settings import dumps,configuration,registry
from app.as2_adapter import call


def atomic_json(path,value):
    temporary=path.with_suffix('.part');temporary.write_text(dumps(value),'utf-8');os.replace(temporary,path)


def prepare(root,ports=(18741,18742),rotate=False):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization as s
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    root=root.resolve();root.mkdir(parents=True,exist_ok=True)
    if os.name=='nt':
        owner=subprocess.check_output(['whoami'],text=True).strip()
        subprocess.run(['icacls',str(root),'/inheritance:r','/grant:r',owner+':(OI)(CI)F'],check=True,capture_output=True)
    else:root.chmod(0o700)
    path=root/'config.json'
    if path.exists() and not rotate:raise ValueError('Стенд уже существует: используйте start/status/rotate, без удаления истории.')
    old=registry(path) if rotate else None
    version='local-'+str(len(old['versions'])+1 if old else 1)
    keys=root/'keys'/version;keys.mkdir(parents=True)
    now=datetime.now(timezone.utc)
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'PV1 synthetic local CA '+version)])
    ca=x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=60)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True).add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),True).sign(ca_key,hashes.SHA256())
    ca_path=keys/'ca.pem';ca_path.write_bytes(ca.public_bytes(s.Encoding.PEM))
    # CA private key is not retained; rotation creates a new explicit local trust generation.
    cfg=dict(version=version,contour='local_stand',enabled=True,xml_sender_id='SYNTH-SENDER',xml_receiver_id='SYNTH-RECEIVER',
        ca_file=str(ca_path),algorithms=dict(signature='sha256',encryption='aes_256_cbc',key_encryption='rsaes_pkcs1v15'),
        http_timeout=3,max_connect_attempts=3,mdn_wait_seconds=30,ack_wait_seconds=60,ack_profile='pv1-local-ack-1')
    for side,port in zip(('a','b'),ports):
        as2_id='PV1-LOCAL-'+side.upper();key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,as2_id+' synthetic '+version)])
        cert=x509.CertificateBuilder().subject_name(name).issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=30)).add_extension(x509.BasicConstraints(ca=False,path_length=None),True).add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),True).sign(ca_key,hashes.SHA256())
        password=secrets.token_urlsafe(32);password_file=keys/(side+'.pass');password_file.write_text(password,'ascii')
        key_file=keys/(side+'.pem');key_file.write_bytes(key.private_bytes(s.Encoding.PEM,s.PrivateFormat.PKCS8,s.BestAvailableEncryption(password.encode()))+cert.public_bytes(s.Encoding.PEM))
        cert_file=keys/(side+'.cer');cert_file.write_bytes(cert.public_bytes(s.Encoding.PEM))
        token_file=root/(side+'.token')
        if not token_file.exists():token_file.write_text(secrets.token_urlsafe(48),'ascii')
        address=f'http://127.0.0.1:{port}'
        if old:address=old['versions'][0][side]['url'].removesuffix('/as2')
        cfg[side]=dict(as2_id=as2_id,url=address+'/as2',mdn_url=address+'/mdn',key_file=str(key_file),password_file=str(password_file),cert_file=str(cert_file),token_file=str(token_file),fingerprint=cert.fingerprint(hashes.SHA256()).hex())
    result=dict(schema='pv1-as2-local-1',active=version,versions=(old['versions'] if old else [])+[cfg])
    # Certificate rotation never authorizes a recovered clone to transmit.
    if old and 'runtime' in old:result['runtime']=old['runtime']
    atomic_json(path,result)
    print('Prepared synthetic '+version+' at '+str(root))


def environment(root):
    env=os.environ.copy();env['PYTHONUTF8']='1';env['PV_AS2_CONFIG']=str(root/'config.json')
    env['DATABASE_URL']='sqlite:///'+(root/'pv1.db').as_posix();env['PV_STORAGE_ROOT']=str(root/'storage')
    env.pop('PV_RESTORE_PATH_MAP',None)
    if (root/'path-map.json').is_file():env['PV_RESTORE_PATH_MAP']=str(root/'path-map.json')
    return env


def start(root,worker=True,app_python=None):
    cfg=configuration(path=root/'config.json');env=environment(root)
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    processes=[]
    for side in ('a','b'):
        try:
            if call('status',cfg=cfg,side=side):continue
        except Exception:pass
        with (root/('engine-'+side+'.log')).open('ab') as log:
            p=subprocess.Popen([sys.executable,'-m','app.as2_engine','--config',str(root/'config.json'),'--side',side],cwd=PROJECT,env=env,stdout=log,stderr=log,creationflags=flags)
            processes.append(dict(role=side,pid=p.pid))
    if worker:
        (root/'worker.stop').unlink(missing_ok=True)
        with (root/'worker.log').open('ab') as log:
            p=subprocess.Popen([str(app_python or PROJECT/'.venv/Scripts/python.exe'),'-m','app.as2_worker','--stop-file',str(root/'worker.stop')],cwd=PROJECT,env=env,stdout=log,stderr=log,creationflags=flags)
            processes.append(dict(role='worker',pid=p.pid))
        with (root/'operations-monitor.log').open('ab') as log:
            p=subprocess.Popen([str(app_python or PROJECT/'.venv/Scripts/python.exe'),'-m','app.operations_monitor','--stop-file',str(root/'worker.stop')],cwd=PROJECT,env=env,stdout=log,stderr=log,creationflags=flags)
            processes.append(dict(role='operations-monitor',pid=p.pid))
    atomic_json(root/'last-start.json',dict(processes=processes,created_at=datetime.now(timezone.utc).isoformat()))
    print('Started local processes: '+dumps(processes))


def stop(root):
    cfg=configuration(path=root/'config.json');(root/'worker.stop').write_text('stop','ascii')
    for side in ('a','b'):
        try:print(side+': '+dumps(call('shutdown',{},cfg=cfg,side=side)))
        except Exception:print(side+': not reachable; no foreign process terminated')


def status(root):
    cfg=configuration(path=root/'config.json')
    for side in ('a','b'):
        try:print(side+': '+dumps(call('status',cfg=cfg,side=side)))
        except Exception:print(side+': not reachable')
    live=sum(time.time()-p.stat().st_mtime<15 for p in root.glob('worker-*.heartbeat'))
    print('Workers with recent heartbeat: '+str(live))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('prepare','rotate','start','stop','status'))
    parser.add_argument('--root',required=True);parser.add_argument('--ports',nargs=2,type=int,default=[18741,18742]);parser.add_argument('--no-worker',action='store_true')
    parser.add_argument('--app-python',type=Path)
    args=parser.parse_args();root=Path(args.root).resolve()
    if args.command in ('prepare','rotate'):prepare(root,args.ports,args.command=='rotate')
    elif args.command=='start':start(root,not args.no_worker,args.app_python)
    elif args.command=='stop':stop(root)
    else:status(root)
