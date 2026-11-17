"""Engine-interpreter helper for one synthetic recovery rehearsal, never a production CLI."""
import json
from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    command=sys.argv[1];args=json.loads(sys.stdin.read())
    if command=='archive-expired':
        from copy import deepcopy
        from datetime import datetime,timedelta,timezone
        import secrets
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization as s
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from app.as2_archive import inspect_archive
        from app.as2_engine import Engine
        from tools.integration_snapshot import encrypt,decrypt
        path=Path(args['config']);registry=json.loads(path.read_text('utf-8'))
        cfg=deepcopy(registry['versions'][0]);cfg['version']='local-expired-archive'
        folder=path.parent/'keys'/'expired';folder.mkdir()
        now=datetime.now(timezone.utc)
        ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'SYNTHETIC expired archive CA')])
        ca=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=90)).not_valid_after(now-timedelta(days=30)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True).add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),True).sign(ca_key,hashes.SHA256())
        cfg['ca_file']=str(folder/'ca.pem');Path(cfg['ca_file']).write_bytes(ca.public_bytes(s.Encoding.PEM))
        for side in ('a','b'):
            key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            leaf=x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,cfg[side]['as2_id'])])).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=80)).not_valid_after(now-timedelta(days=31)).add_extension(x509.BasicConstraints(ca=False,path_length=None),True).add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),True).sign(ca_key,hashes.SHA256())
            password=secrets.token_urlsafe(32)
            for field,suffix,raw in [('cert_file','.cer',leaf.public_bytes(s.Encoding.PEM)),('key_file','.pem',key.private_bytes(s.Encoding.PEM,s.PrivateFormat.PKCS8,s.BestAvailableEncryption(password.encode()))+leaf.public_bytes(s.Encoding.PEM)),('password_file','.pass',password.encode())]:
                cfg[side][field]=str(folder/(side+suffix));Path(cfg[side][field]).write_bytes(raw)
            cfg[side]['fingerprint']=leaf.fingerprint(hashes.SHA256()).hex()
        assert all(v['validity_at_restore']=='expired' for v in inspect_archive(cfg))
        for field,replacement in [('cert_file',cfg['b']['cert_file']),('key_file',cfg['b']['key_file']),('password_file',cfg['b']['password_file'])]:
            bad=deepcopy(cfg);bad['a'][field]=replacement
            try:inspect_archive(bad)
            except Exception:pass
            else:raise AssertionError('Archive accepted wrong '+field)
        damaged=Path(cfg['a']['cert_file']);original=damaged.read_bytes();damaged.write_bytes(b'not a certificate')
        try:
            try:inspect_archive(cfg)
            except Exception:pass
            else:raise AssertionError('Archive accepted damaged certificate')
        finally:damaged.write_bytes(original)
        blob=encrypt({'files':{}},'synthetic separate test passphrase')
        try:decrypt(blob,'incorrect passphrase')
        except Exception:pass
        else:raise AssertionError('Wrong recovery password accepted')
        registry['versions'].append(cfg);path.write_text(json.dumps(registry),encoding='utf-8')
        try:Engine(path,'a').identities(cfg)
        except ValueError as exc:assert str(exc)=='certificate_expired_or_not_yet_valid'
        else:raise AssertionError('Live exchange accepted expired certificate')
        print('EXPIRED_ARCHIVE_AND_NEGATIVE_CHECKS_VERIFIED');return
    if command in ('create','restore','reject-active'):
        from tools.integration_snapshot import create,restore
        password=args.pop('password')
        if command=='restore':restore(password=password,**args);print('RESTORE_VERIFIED_OUTGOING_DISABLED');return
        if command=='reject-active':
            try:create(password=password,**args)
            except OSError:print('ACTIVE_BACKUP_REJECTED');return
            raise AssertionError('Active runtime backup unexpectedly accepted')
        create(password=password,**args);print('COORDINATED_BACKUP_VERIFIED');return
    if command=='uncertain':
        from app.as2_engine import Engine,database
        engine=Engine(args['config'],'a');cfg=engine.cfg()
        # A durable tombstone must also stop a later enqueue with the same request ID.
        tombstone=str(uuid4());assert engine.hold(tombstone)['hold_state']=='engine_stopped'
        try:engine.enqueue(tombstone,b'synthetic fenced request',cfg)
        except ValueError as exc:assert str(exc)=='manual_hold'
        else:raise AssertionError('Late enqueue escaped hold')
        pending=str(uuid4());engine.enqueue(pending,b'synthetic never sent',cfg)
        assert engine.hold(pending)['hold_state']=='engine_stopped' and engine.task(pending)['state']=='cancelled'
        engine.enqueue(args['attempt_id'],Path(args['artifact_path']).read_bytes(),cfg)
        with database(engine.db_path) as db:
            db.execute("UPDATE outbound SET state='unknown',error='synthetic_crash_point',tries=1 WHERE id=?",(args['attempt_id'],))
        assert engine.hold(args['attempt_id'])['hold_state']=='delivery_possible'
        print('UNCERTAINTY_AND_DURABLE_FENCE_PREPARED');return
    raise ValueError('Unknown bounded probe')

if __name__=='__main__':main()
