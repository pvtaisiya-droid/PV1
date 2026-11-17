"""Run only with the isolated pinned engine interpreter; synthetic crypto fault evidence."""
import base64
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
from urllib.error import URLError
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.as2_engine import Engine,database
from app.as2_settings import dumps
from tools.as2_stand import prepare
from pyas2lib.as2 import Message,Mdn


def wire(message):return ''.join(k+': '+v+'\r\n' for k,v in message.headers.items()).encode()+b'\r\n'+message.content


def main(root):
    a=Engine(root/'config.json','a');b=Engine(root/'config.json','b');cfg=a.cfg()
    content='synthetic\nрусский текст\r\nexact bytes\n'.encode()
    key=str(uuid4());a.enqueue(key,content,cfg)
    with database(a.db_path) as db:row=db.execute('SELECT * FROM outbound WHERE id=?',(key,)).fetchone()
    raw=''.join(k+': '+v+'\r\n' for k,v in __import__('json').loads(row['headers']).items()).encode()+b'\r\n'+bytes(row['wire'])
    org,partner=b.identities(cfg);received=Message()
    status,exception,good=received.parse(raw,find_org_cb=lambda _:org,find_partner_cb=lambda _:partner)
    assert status=='processed' and not exception[0]
    assert received.content==content
    assert a.mdn(wire(good))[0]['state']=='positive'
    original_mic=received.mic
    received.mic=base64.b64encode(b'not the engine MIC')
    wrong=Mdn(mdn_mode='ASYNC',digest_alg='sha256',mdn_url=cfg['a']['mdn_url']);wrong.build(received,'processed')
    result=a.mdn(wire(wrong))[0]
    assert result['signature_verified'] and not result['mic_verified'] and result['state']=='review'
    received.mic=original_mic
    negative=Mdn(mdn_mode='ASYNC',digest_alg='sha256',mdn_url=cfg['a']['mdn_url']);negative.build(received,'failed',detailed_status='synthetic-processing-error')
    assert a.mdn(wire(negative))[0]['state']=='negative'
    tampered=wire(good).replace(b'automatic-action/MDN-sent-automatically',b'automatic-action/MDN-sent-synthetically')
    result=a.mdn(tampered)[0];assert not result['signature_verified'] and result['state']=='review'
    received.message_id='unknown@pv1-local.invalid'
    unknown=Mdn(mdn_mode='ASYNC',digest_alg='sha256',mdn_url=cfg['a']['mdn_url']);unknown.build(received,'processed')
    assert a.mdn(wire(unknown))[0]['reason']=='unknown_correlation'
    # Rotation retains the old decrypting key and trust generation for late responses.
    prepare(root,rotate=True);newcfg=a.cfg()
    assert newcfg['version']=='local-2' and newcfg['a']['fingerprint']!=cfg['a']['fingerprint']
    assert b.incoming_message(raw)[0]['trusted']
    assert b.incoming_message(raw)[0]['config_version']=='local-1'
    assert a.mdn(wire(good))[0]['state']=='positive'
    key2=str(uuid4());a.enqueue(key2,b'new generation',newcfg)
    with database(a.db_path) as db:row2=db.execute('SELECT * FROM outbound WHERE id=?',(key2,)).fetchone()
    raw2=''.join(k+': '+v+'\r\n' for k,v in __import__('json').loads(row2['headers']).items()).encode()+b'\r\n'+bytes(row2['wire'])
    assert b.incoming_message(raw2)[0]['trusted'] and b.incoming_message(raw2)[0]['config_version']=='local-2'
    untrusted=deepcopy(cfg);untrusted['ca_file']=newcfg['ca_file']
    try:a.identities(untrusted)
    except Exception:pass
    else:raise AssertionError('Untrusted CA accepted')
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key_expired=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME,'Synthetic expired')]);now=datetime.now(timezone.utc)
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key_expired.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=2)).not_valid_after(now-timedelta(days=1)).sign(key_expired,hashes.SHA256())
    cert_path=root/'expired.cer';cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    expired=deepcopy(cfg);expired['a']['cert_file']=str(cert_path);expired['a']['fingerprint']=cert.fingerprint(hashes.SHA256()).hex()
    try:a.identities(expired)
    except ValueError as exc:assert str(exc)=='certificate_expired_or_not_yet_valid'
    else:raise AssertionError('Expired certificate accepted')
    # Deterministic crash point: persisted sending cannot be blindly retried after restart.
    with database(a.db_path) as db:db.execute("UPDATE outbound SET state='sending'")
    restored=Engine(root/'config.json','a');assert restored.id==a.id
    assert restored.task(key)['state']=='unknown' and not restored.process_outbound()
    # Managed refusal is the only automatic retry class; exact saved wire and ID are reused.
    import app.as2_engine as module
    class Refused:
        def open(self,request,**kwargs):
            calls.append((request.data,dict(request.header_items())))
            raise URLError(ConnectionRefusedError('synthetic refusal before delivery'))
    calls=[];original=module.build_opener;module.build_opener=lambda *args:Refused()
    try:
        key3=str(uuid4());restored.enqueue(key3,b'synthetic refused connection',newcfg)
        for _ in range(3):
            with database(a.db_path) as db:db.execute('UPDATE outbound SET next_at=0 WHERE id=?',(key3,))
            assert restored.process_outbound()
        assert restored.task(key3)['state']=='not_delivered' and restored.task(key3)['tries']==3
        assert len(calls)==3 and calls[0]==calls[1]==calls[2]
        assert not restored.process_outbound()
    finally:module.build_opener=original
    print('CRYPTO_FAULTS_AND_ROTATION_OK')


if __name__=='__main__':main(Path(sys.argv[1]))
