"""Small AS2 extension of backup_snapshot: coordinated offline copy and fenced restore."""
import argparse
import base64
from contextlib import ExitStack,closing
from datetime import datetime,timezone
from getpass import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
from tools import backup_snapshot as base
from app.local_runtime import FileGuard
from app.as2_settings import registry,config_hash,public as public_settings

FORMAT='pv1-integration-backup-1'

def protect(path):
    path.mkdir(parents=True,exist_ok=False)
    if os.name=='nt':
        owner=subprocess.check_output(['whoami'],text=True).strip()
        subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',owner+':(OI)(CI)F'],check=True,capture_output=True)
    else:path.chmod(0o700)

def encrypt(payload,password):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(password)<16:raise ValueError('Use a separate passphrase of at least 16 characters; never store it with the archive.')
    salt=secrets.token_bytes(32);nonce=secrets.token_bytes(12)
    key=Scrypt(salt=salt,length=32,n=16384,r=8,p=1).derive(password.encode())
    raw=AESGCM(key).encrypt(nonce,json.dumps(payload).encode(),FORMAT.encode())
    return json.dumps(dict(format=FORMAT,salt=base64.b64encode(salt).decode(),nonce=base64.b64encode(nonce).decode(),ciphertext=base64.b64encode(raw).decode())).encode()

def decrypt(raw,password):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    value=json.loads(raw)
    if value['format']!=FORMAT:raise ValueError('Unknown secret component format')
    decode=lambda k:base64.b64decode(value[k],validate=True)
    key=Scrypt(salt=decode('salt'),length=32,n=16384,r=8,p=1).derive(password.encode())
    return json.loads(AESGCM(key).decrypt(decode('nonce'),decode('ciphertext'),FORMAT.encode()))

def reference_snapshot_check(database,original_storage,copied_storage):
    with closing(base.readonly(database)) as db:
        for raw,expected in db.execute('SELECT snapshot_json,checksum_sha256 FROM tblIcsrRevisions'):
            if hashlib.sha256(raw.encode()).hexdigest()!=expected:raise ValueError('Clinical revision checksum mismatch')
            data=json.loads(raw)
            for attachment in data.get('records',{}).get('Attachment',[]):
                if not attachment.get('storage_path'):continue
                original=Path(attachment['storage_path']).resolve()
                if not original.is_relative_to(original_storage):raise ValueError('Snapshot document is outside selected storage')
                recovered=copied_storage/original.relative_to(original_storage)
                if not recovered.is_file() or (attachment.get('checksum_sha256') and base.digest(recovered)!=attachment['checksum_sha256']):raise ValueError('Snapshot document missing or changed in copy')
        for table,path_column,hash_column in [('tblIcsrExportArtifacts','storage_path','xml_sha256'),('tblAS2Events','raw_path','raw_sha256'),('tblAS2Events','payload_path','payload_sha256')]:
            for value,expected in db.execute(f'SELECT {path_column},{hash_column} FROM {table} WHERE {path_column} IS NOT NULL'):
                original=Path(value).resolve()
                if not original.is_relative_to(original_storage):raise ValueError('Evidence outside selected storage')
                copied=copied_storage/original.relative_to(original_storage)
                if not copied.is_file() or base.digest(copied)!=expected:raise ValueError('Evidence checksum mismatch')

def create(root,database,storage,config,destination,secret_output,password,offline=False):
    root,database,storage,config,destination,secret_output=[Path(p).resolve() for p in (root,database,storage,config,destination,secret_output)]
    if not offline:raise ValueError('Stop owned writers and explicitly select --offline')
    runtime=config.parent
    if destination.exists() or secret_output.exists():raise ValueError('Backup destinations must be new')
    if any(destination.is_relative_to(p) or p.is_relative_to(destination) for p in (storage,runtime)) or secret_output.is_relative_to(destination) or secret_output.is_relative_to(storage) or secret_output.is_relative_to(runtime):raise ValueError('Separate backup, source and encrypted secret locations are required')
    cfg=registry(config);before_config=base.digest(config);reference_storage=storage
    path_map=runtime/'path-map.json'
    if path_map.is_file():
        mapping=json.loads(path_map.read_text('utf-8'))
        if mapping.get('format')!='pv1-storage-restore-1' or Path(mapping['storage']).resolve()!=storage:raise ValueError('Source restore map does not match its storage')
        reference_storage=Path(mapping['original_storage']).resolve()
    with ExitStack() as stack:
        stack.enter_context(FileGuard(runtime/'runtime.lock',exclusive=True))
        databases=[database]+[runtime/('engine-'+s)/'engine.db' for s in ('a','b')]
        for side in ('a','b'):stack.enter_context(FileGuard(runtime/('engine-'+side)/'process.lock',exclusive=True))
        before={}
        for path in databases:
            if not path.is_file():raise ValueError('All PV1 and engine databases must exist')
            db=stack.enter_context(closing(sqlite3.connect(path,timeout=0)))
            db.execute('BEGIN IMMEDIATE')  # refuse an active writer; hold reservation across every copy
            stack.callback(db.rollback)
            before[str(path)]=base.database_state(path)
        protect(destination)
        report=base.create_snapshot(root,database,storage,destination/'pv1',offline=True,reference_storage=reference_storage)
        if not report['recovery_complete']:raise ValueError('Unresolved references; backup is incomplete')
        reference_snapshot_check(destination/'pv1/database.sqlite3',reference_storage,destination/'pv1/storage')
        (destination/'engines').mkdir();engines={}
        for side,path in zip(('a','b'),databases[1:]):
            target=destination/'engines'/(side+'.db')
            with closing(base.readonly(path)) as source,closing(sqlite3.connect(target)) as copied:source.backup(copied)
            with closing(base.readonly(target)) as db:
                if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('Engine database integrity failure')
                engines[side]=dict(engine_id=db.execute("SELECT value FROM meta WHERE key='engine_id'").fetchone()[0],sequence=db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0],outbound=db.execute('SELECT count(*) FROM outbound').fetchone()[0])
        (destination/'public').mkdir();secret_files={};component_sources={}
        public=dict(schema=cfg['schema'],active=cfg['active'],versions=[])
        for source in cfg['versions']:
            copied=public_settings(source);copied['ca_file']=source['ca_file'];copied['enabled']=source['enabled']
            for side in ('a','b'):
                copied[side].update({field:source[side][field] for field in ('cert_file','key_file','password_file','token_file')})
            public['versions'].append(copied)
        for index,c in enumerate(public['versions']):
            for party,field in [(None,'ca_file')]+[(s,'cert_file') for s in ('a','b')]:
                holder=c if party is None else c[party];source=Path(holder[field]);name=f'{index}-{party or "ca"}.pem'
                shutil.copy2(source,destination/'public'/name);holder[field]='public/'+name;component_sources[str(source)]=base.digest(source)
            for side in ('a','b'):
                for field in ('key_file','password_file','token_file'):
                    source=Path(c[side][field]);slot=f'{index}-{side}-{field}'
                    secret_files[slot]=base64.b64encode(source.read_bytes()).decode();component_sources[str(source)]=base.digest(source)
                    c[side][field]='secret:'+slot
        # Never copy arbitrary runtime attributes into the ordinary public configuration.
        public.pop('runtime',None)
        base.write_json(destination/'config-public.json',public)
        registry(destination/'config-public.json')
        secret_output.parent.mkdir(parents=True,exist_ok=True)
        with secret_output.open('xb') as stream:stream.write(encrypt(dict(files=secret_files),password));stream.flush();os.fsync(stream.fileno())
        if os.name=='nt':
            owner=subprocess.check_output(['whoami'],text=True).strip()
            subprocess.run(['icacls',str(secret_output),'/inheritance:r','/grant:r',owner+':F'],check=True,capture_output=True)
        else:secret_output.chmod(0o600)
        if before_config!=base.digest(config) or any(before[str(p)]!=base.database_state(p) for p in databases) or any(base.digest(Path(p))!=h for p,h in component_sources.items()):raise ValueError('Source changed during offline copy')
        manifest=dict(format=FORMAT,created_at=datetime.now(timezone.utc).isoformat(),engines=engines,original_storage=str(reference_storage),source_storage=str(storage),original_root=str(root),
            config_versions=[dict(version=c['version'],sha256=config_hash(c)) for c in cfg['versions']],secret_component=dict(sha256=base.digest(secret_output),format='AES-256-GCM/Scrypt',slots=sorted(secret_files),passphrase='external; never included'),
            files=base.inventory(destination))
        base.write_json(destination/'manifest.json',manifest)
    return verify(destination)

def verify(snapshot):
    snapshot=Path(snapshot).resolve();manifest=json.loads((snapshot/'manifest.json').read_text('utf-8'))
    if manifest['format']!=FORMAT:raise ValueError('Unsupported coordinated snapshot')
    actual=base.inventory(snapshot);actual.pop('manifest.json')
    if actual!=manifest['files']:raise ValueError('Coordinated snapshot inventory mismatch')
    result=base.verify_snapshot(snapshot/'pv1')
    if not result['recovery_complete']:raise ValueError('Incomplete PV1 evidence copy')
    reference_snapshot_check(snapshot/'pv1/database.sqlite3',Path(manifest['original_storage']),snapshot/'pv1/storage')
    return manifest

def restore(snapshot,destination,secret_component,password,ports):
    snapshot,destination,secret_component=[Path(p).resolve() for p in (snapshot,destination,secret_component)]
    manifest=verify(snapshot)
    if destination.exists() or destination.is_relative_to(snapshot) or snapshot.is_relative_to(destination):raise ValueError('Restore only into a new separate directory')
    if len(set(ports))!=2 or any(not 1024<=p<=65535 for p in ports):raise ValueError('Two distinct loopback ports are required')
    if base.digest(secret_component)!=manifest['secret_component']['sha256']:raise ValueError('Wrong encrypted secret component')
    secret_files=decrypt(secret_component.read_bytes(),password)['files']
    if set(secret_files)!=set(manifest['secret_component']['slots']):raise ValueError('Missing secret components')
    cfg=json.loads((snapshot/'config-public.json').read_text('utf-8'))
    from urllib.parse import urlsplit
    original_ports={urlsplit(c[s]['url']).port for c in cfg['versions'] for s in ('a','b')}
    if original_ports.intersection(ports):raise ValueError('Use different ports from the source environment')
    protect(destination)
    shutil.copy2(snapshot/'pv1/database.sqlite3',destination/'pv1.db')
    shutil.copytree(snapshot/'pv1/storage',destination/'storage')
    shutil.copytree(snapshot/'pv1/source',destination/'source')
    shutil.copytree(snapshot/'public',destination/'public');(destination/'keys').mkdir()
    for slot,content in secret_files.items():(destination/'keys'/slot).write_bytes(base64.b64decode(content,validate=True))
    for c in cfg['versions']:
        c['ca_file']=str(destination/c['ca_file'])
        for side in ('a','b'):
            c[side]['cert_file']=str(destination/c[side]['cert_file'])
            for field in ('key_file','password_file','token_file'):c[side][field]=str(destination/'keys'/c[side][field].removeprefix('secret:'))
    cfg['runtime']=dict(outgoing_disabled=True,ports=dict(zip(('a','b'),ports)),recovery_id=secrets.token_hex(16),source_fenced=False)
    base.write_json(destination/'config.json',cfg)
    base.write_json(destination/'path-map.json',dict(format='pv1-storage-restore-1',original_storage=manifest['original_storage'],storage=str(destination/'storage')))
    for side in ('a','b'):
        folder=destination/('engine-'+side);folder.mkdir();shutil.copy2(snapshot/'engines'/(side+'.db'),folder/'engine.db')
    reference_snapshot_check(destination/'pv1.db',Path(manifest['original_storage']),destination/'storage')
    report=base.database_report(destination/'pv1.db',Path(manifest['original_root']),Path(manifest['original_storage']),destination/'storage')
    if not report['recovery_complete']:raise ValueError('Restored file/reference verification failed')
    from app.as2_archive import inspect_archive
    certificates=[]
    for c in registry(destination/'config.json')['versions']:
        certificates.extend(inspect_archive(c))
    from app.as2_engine import Engine,database as engine_database
    for side in ('a','b'):
        engine=Engine(destination/'config.json',side)
        if engine.id!=manifest['engines'][side]['engine_id']:raise ValueError('Engine identity changed')
        if engine.process_outbound():raise ValueError('Restored sender was not fenced')
        with engine_database(engine.db_path) as db:
            if db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0]<manifest['engines'][side]['sequence']:raise ValueError('Engine history lost')
    base.write_json(destination/'restore-report.json',dict(format=FORMAT,verified_at=datetime.now(timezone.utc).isoformat(),snapshot_manifest_sha256=base.digest(snapshot/'manifest.json'),
        outgoing_disabled=True,recovery_complete=True,engine_identity_preserved=True,
        certificates=certificates,certificate_check='archive_integrity_only; transport validity and historical receipt trust unchanged',
        warnings=[dict(code='archived_certificate_'+c['validity_at_restore'],version=c['version'],party=c['party']) for c in certificates if c['validity_at_restore']!='current'],
        storage_inventory=base.inventory(destination/'storage'),database_sha256=base.digest(destination/'pv1.db')))
    return dict(recovery_complete=True,outgoing_disabled=True,destination=str(destination))

def main(arguments=None):
    parser=argparse.ArgumentParser();commands=parser.add_subparsers(dest='command',required=True)
    create_args=commands.add_parser('integration-create')
    for name in ('root','database','storage','config','destination','secret-output'):create_args.add_argument('--'+name,type=Path,required=True)
    create_args.add_argument('--offline',action='store_true')
    check=commands.add_parser('integration-verify');check.add_argument('snapshot',type=Path)
    recovery=commands.add_parser('integration-restore')
    for name in ('snapshot','destination','secret-component'):recovery.add_argument('--'+name,type=Path,required=True)
    recovery.add_argument('--ports',nargs=2,type=int,required=True)
    args=parser.parse_args(arguments)
    try:
        if args.command=='integration-create':
            password=getpass('Separate recovery passphrase (16+ characters): ')
            if password!=getpass('Repeat recovery passphrase: '):raise ValueError('Passphrases differ')
            create(args.root,args.database,args.storage,args.config,args.destination,args.secret_output,password,args.offline)
        elif args.command=='integration-restore':restore(args.snapshot,args.destination,args.secret_component,getpass('Recovery passphrase: '),args.ports)
        else:verify(args.snapshot)
    except Exception as exc:
        parser.exit(1,'Coordinated backup/restore failed ('+type(exc).__name__+'); incomplete output is retained, no source is deleted.\n')
    print('Coordinated operation verified; restored outgoing transport remains disabled.');return 0

if __name__=='__main__':raise SystemExit(main())
