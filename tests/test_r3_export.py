"""Bounded offline export risks. All people, IDs and dictionary codes are synthetic."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
import pytest
from lxml import etree as E
from tests.r3_export_helpers import specimen, revision, envelope

NS={'h':'urn:hl7-org:v3'}


def test_multiblock_official_xsd_and_values(tmp_path):
    from app.r3_export import export_revision
    from app.r3_profile import validate_xml, LocalResolver, profile
    data=specimen(tmp_path,study=True)
    result=export_revision(revision(data),envelope(),tmp_path)
    assert result['locally_valid'] and result['xsd_valid'],result['findings']
    root=E.fromstring(result['xml'])
    xp=lambda x:root.xpath(x,namespaces=NS)
    assert xp('./h:id/@extension')==['SYNTH-BATCH']
    assert xp('./h:PORR_IN049016UV/h:id/@extension')==['SYNTH-MESSAGE']
    assert xp('.//h:investigationEvent/h:id/@extension')==['RU-SYNTH-0001','SYNTH-CASE-01']
    assert xp('.//h:primaryRole/h:player1/h:birthTime/@value')==['1980']
    assert xp('.//h:role[h:code[@code="PRN"]]/h:associatedPerson/h:birthTime/@value')==['196002']
    assert xp('.//h:role[h:code[@code="PRN"]]/h:associatedPerson/h:name/@nullFlavor')==['MSK']
    assert 'Подтверждённый русский текст: ёж, <символы> & кавычки "…".' in ''.join(root.itertext())
    assert xp('.//h:substanceAdministration/h:doseQuantity/@value')==['0.2500']*3
    assert xp('.//h:substanceAdministration/h:effectiveTime/h:comp/h:low/@value').count('202602')==3
    assert xp('.//h:observation/h:value[@code="99901001"]/@codeSystemVersion')==['27.0']
    assert xp('.//h:researchStudy/h:title/text()')==['Синтетическое исследование']
    reactions=xp('.//h:observation[h:code[@code="29"]]/h:id/@root')
    products=xp('.//h:substanceAdministration[h:consumable]/h:id/@root')
    assert len(reactions)==len(set(reactions))==2 and len(products)==len(set(products))==2
    pairs=xp('.//h:causalityAssessment[h:code[@code="39"]]')
    assert [(p.xpath('.//h:adverseEffectReference/h:id/@root',namespaces=NS)[0],p.xpath('.//h:productUseReference/h:id/@root',namespaces=NS)[0]) for p in pairs]==list(zip(reactions,products))
    assert any(f['rule']=='MEDDRA' and f['level']=='not_performed' for f in result['findings'])
    assert result['pv_decisions'] and result['documents']==['document']
    assert validate_xml(result['xml'].replace(b'ITSVersion="XML_1.0"',b'ITSVersion="INVALID"'))
    assert validate_xml(b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///secret">]><x>&a;</x>')
    with pytest.raises(ValueError,match='XSD'):
        LocalResolver(profile()).resolve('https://invalid.example/schema.xsd',None,None)
    # The same common blocks also support a spontaneous registered-product report.
    registered=specimen(tmp_path/'registered',study=False)
    spontaneous=export_revision(revision(registered),envelope(),tmp_path/'registered')
    assert spontaneous['locally_valid'] and spontaneous['xsd_valid'],spontaneous['findings']


def test_blocking_content_integrity_and_documents(tmp_path):
    from app.r3_export import export_revision, dump
    data=specimen(tmp_path)
    broken=revision(data);broken['schema_version']='unknown'
    with pytest.raises(ValueError,match='версия'):export_revision(broken,envelope(),tmp_path)
    broken=revision(data);broken['checksum_sha256']='0'*64
    with pytest.raises(ValueError,match='сумма'):export_revision(broken,envelope(),tmp_path)
    data['records']['CaseProduct'][0]['coded_identifiers_json']=dump([{'code':'SYNTH-IDMP'}])
    data['records']['Reaction'][0]['meddra_llt_code']=None
    data['records']['Reaction'][1]['onset_date_raw']='2025'  # contradicts the canonical repeat
    data['records']['CaseDrugDosage'][0]['dosage_form_code']='SYNTH-CODE-WITHOUT-SYSTEM'
    data['records']['CaseClinicalRecord'][0]['fields_json']=dump({'C.1.3':{'raw':'1'},'C.1.4':{'raw':None,'null_flavor':'UNK'}})
    (tmp_path/'synthetic.pdf').write_bytes(b'changed after revision')
    result=export_revision(revision(data),envelope(),tmp_path)
    assert not result['locally_valid']
    rules={f['rule'] for f in result['findings'] if f['level']=='error'}
    assert {'IDMP','REQUIRED','DOCUMENT-HASH','PROJECTION-COVERAGE','CODE-SYSTEM'}<=rules
    assert all(f['path'] and f['source'] and f['version'] for f in result['findings'])


def test_protected_form_snapshot_download_followup_and_rollback(client,monkeypatch):
    from app.database import SessionLocal
    from app import models as m
    from app.config import get_settings
    from app.r3_export import dump
    from app import r3_artifacts
    from tests.test_e2b_import import Forms
    data=specimen(get_settings().storage_root,study=True)
    source=revision(data)
    with SessionLocal() as db:
        author=db.query(m.User).filter_by(login='test-admin').one().id
        db.add_all([m.Case(id=data['case']['id'],case_number='SYNTH-EXPORT'),m.Case(id='other-case',case_number='SYNTH-OTHER')]);db.flush()
        db.add(m.IcsrRevision(**source,revision_number=1,author_id=author,validation_json='{}',reason='Synthetic fixed snapshot'))
        db.commit()
    cid=data['case']['id'];rid=source['id'];target=f'/cases/{cid}/r3-export'
    page=client.get(target,params={'revision_id':rid});assert page.status_code==200,page.text
    form=Forms(page.text).forms[target]
    form.update(sender_id='SYNTH-SENDER',receiver_id='SYNTH-RECEIVER',purpose='local_test',scenario='initial',reason='Синтетическая подготовка XML')
    no_csrf=dict(form);no_csrf.pop('csrf')
    assert client.post(target,data=no_csrf,follow_redirects=False).status_code==403
    response=client.post(target,data=form,follow_redirects=False)
    assert response.status_code==303,response.text
    aid=response.headers['location'].split('artifact_id=')[1]
    assert client.post(target,data=form,follow_redirects=False).headers['location']==response.headers['location']
    result=client.get(f'/api/cases/{cid}/r3-artifacts/{aid}').json()
    assert result['locally_valid'],result
    assert 'Локальная проверка пройдена' in client.get(response.headers['location']).text
    downloaded=client.get(result['download_url'])
    assert downloaded.status_code==200
    assert hashlib.sha256(downloaded.content).hexdigest()==result['xml_sha256']
    with SessionLocal() as db:
        assert db.query(m.IcsrExportArtifact).count()==1
        row=db.get(m.Case,cid);row.narrative='Текущая карточка после фиксации';db.commit()
        artifact=db.get(m.IcsrExportArtifact,aid);path=Path(artifact.storage_path)
        assert path.read_bytes()==downloaded.content
        artifact.reason='mutated'
        with pytest.raises(ValueError,match='неизменяем'):db.commit()
        db.rollback()
    assert client.get(result['download_url']).content==downloaded.content
    assert client.get(f'/api/cases/other-case/r3-artifacts/{aid}/download').status_code==404
    assert client.get('/cases/other-case/r3-export',params={'revision_id':rid}).status_code==404
    # Explicit follow-up, same identities, new immutable content and latest receipt.
    newer=deepcopy(data)
    report=newer['records']['CaseClinicalRecord'][0];facts=json.loads(report['fields_json']);facts['C.1.5']['raw']='20260305';report['fields_json']=dump(facts)
    next_source=revision(newer);next_source['id']='revision-followup'
    with SessionLocal() as db:
        db.add(m.IcsrRevision(**next_source,revision_number=2,author_id=author,validation_json='{}',reason='Synthetic follow-up snapshot'));db.commit()
    payload=dict(idempotency_key=str(uuid4()),expected_revision_checksum=next_source['checksum_sha256'],sender_id='SYNTH-SENDER',receiver_id='SYNTH-RECEIVER',purpose='local_test',scenario='followup',previous_artifact_id=aid,reason='Синтетический follow-up')
    api=f'/api/cases/{cid}/revisions/{next_source["id"]}/r3-artifacts'
    follow=client.post(api,json=payload);assert follow.status_code==200,follow.text
    assert follow.json()['locally_valid'] and follow.json()['envelope']['message_id']!=result['envelope']['message_id']
    xml=E.fromstring(client.get(follow.json()['download_url']).content)
    assert xml.xpath('.//h:investigationEvent/h:availabilityTime/@value',namespaces=NS)==['20260305']
    # Failed final database commit removes only files owned by this preparation.
    from sqlalchemy import event
    def fail_commit(session):raise RuntimeError('synthetic commit failure')
    folder=get_settings().storage_root/'r3';before=set(folder.iterdir())
    from sqlalchemy.orm import Session
    event.listen(Session,'before_commit',fail_commit)
    try:
        with pytest.raises(RuntimeError,match='synthetic commit failure'):
            client.post(api,json={**payload,'idempotency_key':str(uuid4())})
    finally:event.remove(Session,'before_commit',fail_commit)
    assert set(folder.iterdir())==before
    with SessionLocal() as db:assert db.query(m.IcsrExportArtifact).count()==2
    # Failed readiness remains diagnostic, never a ready download.
    bad=client.post(api,json={**payload,'idempotency_key':str(uuid4()),'scenario':'initial','previous_artifact_id':None,'sender_id':'OTHER-SENDER'})
    assert bad.status_code==200 and not bad.json()['locally_valid'] and bad.json()['download_url'] is None
    assert client.get(f'/api/cases/{cid}/r3-artifacts/{bad.json()["id"]}/download').status_code==409
    path.write_bytes(b'corrupted')
    assert client.get(result['download_url']).status_code==409
    with SessionLocal() as db:
        user=db.query(m.User).filter_by(login='test-admin').one();db.query(m.UserRole).filter_by(user_id=user.id).delete()
        role=db.query(m.Role).filter_by(role_code='readonly_auditor').one();user.role='viewer';db.add(m.UserRole(user_id=user.id,role_id=role.id));db.commit()
    assert client.post(api,json={**payload,'idempotency_key':str(uuid4())}).status_code==403


def test_migration_0033_preserves_linked_revisions(tmp_path):
    from tests.test_migrations import migrate_test_database,application_command,database_contents
    path=tmp_path/'m.db'
    result=migrate_test_database(path,'20261111_0033');assert result.returncode==0,result.stderr
    result=application_command(path,'-c','''
from app.database import SessionLocal
from app.models import Case, Reaction, IcsrRevision, User
with SessionLocal() as db:
    user=User(email='synthetic@example.invalid');db.add(user);db.flush()
    for i in range(2):
        c=Case(id='case'+str(i),case_number='SYNTH-'+str(i));db.add(c);db.flush()
        db.add(Reaction(case_id=c.id,reported_term='Синтетическая реакция'))
        db.add(IcsrRevision(case_id=c.id,revision_number=1,author_id=user.id,schema_version='pv1-clinical-1',snapshot_json='{"synthetic":true}',checksum_sha256='a'*64,validation_json='{}',reason='Synthetic historical immutable evidence'))
    db.commit()
''');assert result.returncode==0,result.stderr
    schema,before=database_contents(path)
    result=migrate_test_database(path,'20261111_0034');assert result.returncode==0,result.stderr
    after_schema,after=database_contents(path)
    assert all(after[name]==rows for name,rows in before.items() if name!='alembic_version')
    assert set(schema)-{r for r in schema if r[1]=='alembic_version'} <= set(after_schema)
    assert after['tblIcsrExportArtifacts']==[] and after['alembic_version']==[('20261111_0034',)]
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE tblIcsrRevisions SET reason='changed'")
        assert len(db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'immutable_icsr_export_%'").fetchall())==2
