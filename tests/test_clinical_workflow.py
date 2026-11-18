"""Bounded internal stage-2 workflows, synthetic sources only."""
import json
from pathlib import Path
from tests.test_e2b_import import upload_xml, seed_product

SOURCE = Path(__file__).parent / 'fixtures/e2b_r3_drugs/source.xml'


def uploaded(client, xml=None):
    from tests.test_e2b_import import upload_package
    payload=xml or SOURCE.read_bytes()
    response = upload_package(client,payload) if payload.startswith(b'PK') else upload_xml(client,payload)
    assert response.status_code == 303, response.text
    bid = response.headers['location'].split('?')[0].rsplit('/', 1)[1]
    rows = client.get(f'/api/e2b/batches/{bid}/messages').json()
    assert rows, response.headers['location']
    return bid, rows


def plan(client, mid, mappings=None, **extra):
    info = client.get(f'/api/e2b/messages/{mid}')
    assert info.status_code == 200, info.text
    assert info.json()['status'] == 'review', info.text
    mappings = mappings or {n['key']: 'new' for n in info.json()['nodes']}
    response = client.post(f'/api/e2b/messages/{mid}/plans', json=dict(mappings=mappings, reason='Синтетическая сверка входящих данных', **extra))
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, p, choices=None):
    response = client.post(f'/api/e2b/plans/{p["id"]}/apply', json={'choices': choices or {}})
    assert response.status_code == 200, response.text
    return response.json()['case_id']


def test_initial_and_revision(client):
    seed_product()
    bid, rows = uploaded(client)
    assert len(rows) == 1 and rows[0]['status'] == 'review', rows
    mid = rows[0]['id']
    assert client.get(f'/e2b/messages/{mid}').status_code == 200
    p = plan(client, mid)
    assert client.get(f'/e2b/messages/{mid}?plan_id={p["id"]}').status_code == 200
    cid = apply(client, p)
    assert apply(client, p) == cid
    data = client.get(f'/api/cases/{cid}/clinical').json()
    assert any(r['kind'] == 'patient' for r in data['records'])
    products = client.get(f'/api/cases/{cid}/drug-products').json()
    assert any(p.get('product_id') for p in products)
    assert len(products) == 2 and len(products[0]['dosages']) + len(products[1]['dosages']) == 3
    revisions = client.get(f'/api/cases/{cid}/revisions').json()
    assert len(revisions) == 1
    snapshot = client.get(f'/api/cases/{cid}/revisions/{revisions[0]["id"]}').json()
    assert len(snapshot['snapshot']['records']['CaseProduct']) == 2
    assert len(snapshot['snapshot']['records']['SafetyReport']) == 1
    assert len(snapshot['snapshot']['records']['IncomingRequest']) == 1
    assert snapshot['snapshot']['records']['AuditTrail']
    assert snapshot['validation']['recipient_profile'] == 'not_validated'
    from copy import deepcopy
    from app.clinical_revision import validate
    incomplete=deepcopy(snapshot['snapshot'])
    for record in incomplete['records']['CaseClinicalRecord']:
        if record['kind']=='reporter':
            record['fields_json']=json.dumps({'local.contacts':{'state':'received_unvalidated','value':[{'use':'HP','value':None}]}})
    for record in incomplete['records']['Reaction']:
        record['reported_term']='[Исходный текст реакции отсутствует]';record['meddra_llt_code']='bad-code'
    rules={f['rule'] for f in validate(incomplete)['findings']}
    assert {'MIN-reporter','MIN-reaction'} <= rules
    assert client.get(f'/cases/{cid}/clinical').status_code == 200
    assert client.get(f'/cases/{cid}/clinical?kind=reporter').status_code == 200
    _, repeated = uploaded(client)
    assert repeated[0]['status']=='duplicate' and repeated[0]['case_id']==cid


def test_followup_manual_conflict_and_history(client):
    from app.database import SessionLocal
    from app.models import IcsrReviewPlan, IcsrRevision
    seed_product()
    _, rows = uploaded(client)
    first = plan(client, rows[0]['id'])
    cid = apply(client, first)
    old_rev = client.get(f'/api/cases/{cid}/revisions').json()[0]['id']
    before = client.get(f'/api/cases/{cid}/revisions/{old_rev}').json()
    with SessionLocal() as db:
        original = json.loads(db.get(IcsrReviewPlan, first['id']).preview_json)
        # Map through source facts created by the first import, explicitly in this test.
        from app import models
        mappings = {}
        for node in original['nodes']:
            cls = getattr(models, node['model'])
            candidates = db.query(cls).filter_by(case_id=cid).all() if hasattr(cls,'case_id') else db.query(cls).all()
            matches = [r for r in candidates if json.loads(r.source_facts_json) == node['source'] and (not hasattr(r,'kind') or r.kind==node['kind'])
                       and all(getattr(r,field)==mappings[key] for field,key in node['links'].items())]
            assert len(matches)==1, node['key']
            mappings[node['key']] = matches[0].id
    source = SOURCE.read_bytes().replace(b'Message-NotVersion-99', b'Message-Follow-Up-100').replace(b'<name>SY</name>',b'<name>FU</name>')
    from xml.etree import ElementTree as E
    from copy import deepcopy
    h='{urn:hl7-org:v3}'; tree=E.fromstring(source); primary=tree.find('.//'+h+'primaryRole')
    added=deepcopy(next(n for n in primary.findall(h+'subjectOf2') if n.find(h+'observation') is not None))
    added.find(h+'observation/'+h+'id').set('extension','rx-added')
    added.find('.//'+h+'originalText').text='Added synthetic reaction'
    primary.append(added)
    rx=primary.find(h+'subjectOf2/'+h+'observation')
    criterion=E.SubElement(E.SubElement(rx,h+'outboundRelationship2'),h+'observation')
    E.SubElement(criterion,h+'code',code='34',codeSystem='2.16.840.1.113883.3.989.2.1.1.19')
    E.SubElement(criterion,h+'value',nullFlavor='UNK')
    source=E.tostring(tree)
    _, following = uploaded(client, source)
    mid = following[0]['id']
    incoming=client.get(f'/api/e2b/messages/{mid}').json()
    mappings.update({n['key']:'new' for n in incoming['nodes'] if n['key'] not in mappings})
    assert client.post(f'/api/e2b/messages/{mid}/plans',json={'mappings':{},'reason':'Неоднозначные повторы не выбраны'}).status_code==400
    duplicate=dict(mappings);duplicate['reaction:1']=mappings['reaction:0']
    assert client.post(f'/api/e2b/messages/{mid}/plans',json={'mappings':duplicate,'reason':'Попытка скрытого объединения повторов'}).status_code==400
    with SessionLocal() as db:
        assessment=db.get(models.CaseProductReactionAssessment, mappings['product:0:assessment:0'])
        assessment.causality_method='naranjo'
        db.get(models.Reaction,mappings['reaction:0']).seriousness_death=True
        assessment_id=assessment.id
        db.commit()
    blocked=client.post(f'/api/e2b/messages/{mid}/plans',json={'mappings':mappings,'reason':'Защита рассчитанной причинности'})
    assert blocked.status_code==400 and 'причинность' in blocked.text
    mappings['product:0:assessment:0']='new'
    stale_plan = plan(client, mid, mappings)
    patient = next(r for r in client.get(f'/api/cases/{cid}/clinical').json()['records'] if r['kind']=='patient')
    response = client.patch(f'/api/cases/{cid}/clinical/patient/{patient["id"]}',
        params=dict(expected_case_version=client.get(f'/api/cases/{cid}').json()['version'], expected_version=patient['version']),
        json=dict(fields={'D.1':{'raw':'MANUAL'}}, change_reason='Подтверждено ручной сверкой'))
    assert response.status_code==200,response.text
    choices={node['key']:{f:'accept' for f in node['changes']} for node in stale_plan['preview']['diff'] if node['target'] not in ('new','ignore')}
    stale=client.post(f'/api/e2b/plans/{stale_plan["id"]}/apply',json={'choices':choices})
    assert stale.status_code==409,stale.text
    fresh=plan(client,mid,mappings)
    patient_diff=next(n for n in fresh['preview']['diff'] if n['kind']=='patient')
    assert patient_diff['changes']['D.1']['old']['raw']=='MANUAL'
    choices[patient_diff['key']]['D.1']='keep'
    assert fresh['preview']['diff'][0]['changes']['seriousness_death']['incoming'] is None
    assert apply(client,fresh,choices)==cid
    assert apply(client,fresh,choices)==cid
    with SessionLocal() as db:
        rx=db.get(models.Reaction,mappings['reaction:0'])
        assert rx.seriousness_death is None
        assert json.loads(rx.current_sources_json)['seriousness_death']['source_fact']['criteria']['seriousness_death']['null_flavor']=='UNK'
        assert db.get(models.CaseProductReactionAssessment,assessment_id).causality_method=='naranjo'
    current=client.get(f'/api/cases/{cid}/clinical').json()
    assert next(r for r in current['records'] if r['kind']=='patient')['fields']['D.1']['raw']=='MANUAL'
    assert client.get(f'/api/cases/{cid}/revisions/{old_rev}').json()==before
    assert len(client.get(f'/api/cases/{cid}/drug-products').json())==2
    assert len(client.get(f'/api/cases/{cid}/overview').json()['reactions'])==3
    assert len(client.get(f'/api/cases/{cid}/revisions').json())==3
    with SessionLocal() as db:
        import pytest
        revision=db.get(IcsrRevision,old_rev)
        revision.reason='Попытка изменения истории'
        with pytest.raises(ValueError, match='неизменяема'):
            db.commit()
        db.rollback()


def test_clinical_study_and_editor(client):
    from tests.test_e2b_import import Forms
    seed_product()
    _, rows=uploaded(client,(SOURCE.parent.parent/'e2b_r3_clinical/source.xml').read_bytes())
    assert rows[0]['status']=='review',rows
    cid=apply(client,plan(client,rows[0]['id']))
    response=client.get(f'/api/cases/{cid}/clinical');assert response.status_code==200,response.text
    records=response.json()['records']
    assert len([r for r in records if r['kind']=='reporter'])==2
    assert len([r for r in records if r['kind']=='medical_history'])==2
    patient=next(r for r in records if r['kind']=='patient');parent=next(r for r in records if r['kind']=='parent')
    assert parent['parent_id']==patient['id']
    assert patient['fields']['D.2.1']['precision']=='year'
    history=next(r for r in records if r['kind']=='medical_history' and 'D.7.1.r.4' in r['fields'])
    assert history['fields']['D.7.1.r.4']['state']=='null_flavor'
    study=next(r for r in records if r['kind']=='study')
    assert next(r for r in records if r['kind']=='study_registration')['parent_id']==study['id']
    lab=next(r for r in records if r['kind']=='test')
    assert lab['fields']['F.r.3.2']['value']['attributes']['value']=='1.2500'
    assert lab['fields']['F.r.3.3']['value']['value']=='mg/L'
    assert len([r for r in records if r['kind']=='literature'])==2
    assert next(r for r in records if r['kind']=='drug_history')['parent_id']==patient['id']
    assert next(r for r in records if r['kind']=='death_cause')['fields']['D.9.2.r.2']['raw']=='Synthetic cause'
    assert next(r for r in records if r['kind']=='autopsy_cause')['parent_id']==patient['id']
    extra=next(r for r in records if r['kind']=='drug_extra')
    assert extra['fields']['G.k.5a']['raw']=='3.2500' and extra['fields']['G.k.2.5']['state']=='null_flavor'
    interval=next(r for r in records if r['kind']=='pair_interval')
    assert interval['case_product_id']==extra['case_product_id'] and interval['reaction_id']
    assert next(r for r in records if r['kind']=='source_document')['attachment_id']
    # A real HTML write: no automatic reportability/urgency is inferred from study data.
    page=f'/cases/{cid}/clinical?kind=pv_decision'
    target=f'/cases/{cid}/clinical/pv_decision'
    form=Forms(client.get(page).text).forms[target]
    form.update({'change:local.expectedness':'on','raw:local.expectedness':'unknown',
        'change:local.reportability':'on','raw:local.reportability':'undecided',
        'change:local.document_version':'on','raw:local.document_version':'RSI-synthetic-1',
        'change_reason':'Синтетическое решение ответственного ФН'})
    bad=dict(form);bad.pop('csrf')
    assert client.post(target,data=bad,follow_redirects=False).status_code==403
    response=client.post(target,data=form,follow_redirects=False);assert response.status_code==303,response.text
    decisions=[r for r in client.get(f'/api/cases/{cid}/clinical').json()['records'] if r['kind']=='pv_decision']
    assert decisions[0]['fields']['local.reportability']['value']=='undecided'
    assert decisions[0]['fields']['local.reportability']['context']['author_id']
    assert decisions[0]['fields']['local.reportability']['context']['confirmed_at']
    assert client.get(f'/cases/{cid}').status_code==200


def test_packet_partial_and_order(client):
    from copy import deepcopy
    from xml.etree import ElementTree as E
    from app.database import SessionLocal
    from app.models import E2BImportBatch, Case
    seed_product()
    h='{urn:hl7-org:v3}'
    root=E.fromstring(SOURCE.read_bytes()); first=root.find(h+'PORR_IN049016UV')
    second=deepcopy(first)
    for node in second.iter(h+'id'):
        if node.get('extension') in ('Sender-Case-X','ZZ-SYNTH-0001','Message-NotVersion-99'):
            node.set('extension',node.get('extension')+'-B')
    bad=deepcopy(second)
    bad.find('.//'+h+'adverseEffectReference/'+h+'id').set('extension','missing-reaction')
    root.append(bad)
    other=deepcopy(root)
    for child in list(other):
        if child.tag==h+'PORR_IN049016UV':other.remove(child)
    other.find(h+'id').set('extension','Batch-B')
    other.append(second)
    from tests.test_e2b_import import make_zip
    bid,rows=uploaded(client,make_zip({'one.xml':E.tostring(root),'two.xml':E.tostring(other),'selected.pdf':b'%PDF-1.4 synthetic shared document'}))
    assert len(rows)==3 and [r['status'] for r in rows].count('error')==1,rows
    good=[r for r in rows if r['status']=='review']; broken=next(r for r in rows if r['status']=='error')
    assert client.post(f'/api/e2b/messages/{broken["id"]}/plans',json={'mappings':{},'reason':'Проверка запрета ошибки'}).status_code==400
    info=client.get(f'/api/e2b/messages/{good[0]["id"]}').json()
    assert len(info['documents'])==1
    ids=[apply(client,plan(client,r['id'],attachment_item_ids=[info['documents'][0]['id']] if n==0 else [])) for n,r in enumerate(good)]
    assert len(set(ids))==2
    with SessionLocal() as db:
        batch=db.get(E2BImportBatch,bid)
        assert batch.status=='partially_processed' and batch.detected_message_count==3
        assert db.query(Case).count()==2
        from app.models import Attachment
        assert db.query(Attachment).filter_by(file_name='selected.pdf').count()==1
    # Two pending messages of the same case require a documented sequence decision.
    xml=SOURCE.read_bytes().replace(b'Message-NotVersion-99',b'Message-Next-1')
    _,a=uploaded(client,xml)
    _,b=uploaded(client,xml.replace(b'Message-Next-1',b'Message-Next-2'))
    info=client.get(f'/api/e2b/messages/{a[0]["id"]}').json()
    mapping={n['key']:'ignore' for n in info['nodes']}
    blocked=client.post(f'/api/e2b/messages/{a[0]["id"]}/plans',json={'mappings':mapping,'reason':'Синтетическое решение порядка'})
    assert blocked.status_code==400 and 'другие сообщения' in blocked.text
    approved=plan(client,a[0]['id'],mapping,sequence_reason='Ответственный подтвердил источник как следующий')
    assert approved['preview']['pending_same_case']==[b[0]['id']]


def test_migration_preserves_history(tmp_path):
    import sqlite3
    import sqlalchemy as sa
    from datetime import datetime
    from tests.test_migrations import migrate_test_database, assert_matches_current_models
    path=tmp_path/'history.db'
    result=migrate_test_database(path,'20261110_0032');assert result.returncode==0,result.stderr
    engine=sa.create_engine(f'sqlite:///{path.as_posix()}');meta=sa.MetaData();meta.reflect(bind=engine)
    common=dict(created_at=datetime(2024,1,1),updated_at=datetime(2024,1,1),is_active=True,is_deleted=False,version=4)
    names=['tblCases','tblPatients','tblCaseProducts','tblReactions','tblCaseDrugDosages','tblCaseProductReactionAssessments']
    with engine.begin() as c:
        for i in range(2):
            c.execute(meta.tables['tblCases'].insert().values(**common,id=f'c{i}',case_number=f'H{i}',number_is_provisional=False,case_version=1,workflow_status='new',is_locked=False))
            c.execute(meta.tables['tblPatients'].insert().values(**common,id=f'pt{i}',case_id=f'c{i}',patient_role='patient',patient_initials=f'S{i}',birth_date_raw='1980',age_unit=None))
            c.execute(meta.tables['tblCaseProducts'].insert().values(**common,id=f'd{i}',case_id=f'c{i}',drug_role='unknown'))
            c.execute(meta.tables['tblReactions'].insert().values(**common,id=f'r{i}',case_id=f'c{i}',reported_term='Historical synthetic',onset_date_raw='2025'))
            c.execute(meta.tables['tblCaseDrugDosages'].insert().values(**common,id=f'dose{i}',case_product_id=f'd{i}',dose_value='0.2500'))
            c.execute(meta.tables['tblCaseProductReactionAssessments'].insert().values(**common,id=f'a{i}',case_product_id=f'd{i}',reaction_id=f'r{i}',causality_method='manual_text',relatedness='unknown'))
    engine.dispose()
    with sqlite3.connect(path) as c:
        before={name:c.execute(f'SELECT * FROM "{name}" ORDER BY id').fetchall() for name in names}
    result=migrate_test_database(path);assert result.returncode==0,result.stderr
    with sqlite3.connect(path) as c:
        for name,rows in before.items():
            assert [r[:len(rows[0])] for r in c.execute(f'SELECT * FROM "{name}" ORDER BY id').fetchall()]==rows
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert c.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='20261117_0037'
        migrated=c.execute('SELECT kind,fields_json,interpretation_profile FROM tblCaseClinicalRecords').fetchall()
        assert len(migrated)==4 and all(x[2]=='legacy_unverified' for x in migrated)
        assert c.execute('SELECT COUNT(*) FROM tblIcsrRevisions').fetchone()[0]==0
    assert_matches_current_models(path)


def test_clinical_guards_and_unknowns(client):
    from app import clinical_graph
    sample={'message':{'import_source':{'message_id':'synthetic'}},'products':[{
        'drug_role':'unknown','coded_identifiers':[], 'source_facts':{'role':{'state':'missing'}},
        'dosages':[{'dose_value':None,'dose_unit':None,'source_facts':{'dose':{'nullFlavor':'UNK'}}}]}]}
    nodes=clinical_graph.graph(sample)
    assert 'drug_role' not in nodes[0]['values'] and 'coded_identifiers_json' not in nodes[0]['values']
    assert nodes[1]['values']['dose_value'] is None and nodes[1]['source']['dose']['nullFlavor']=='UNK'

    from app.database import SessionLocal
    from app.models import Case, User, UserRole, Role
    from app.clinical_values import meaningful
    assert not meaningful({'state':'manual','value':{'nested':{'value':''}}})
    with SessionLocal() as db:
        a=Case(case_number='CLINICAL-A');b=Case(case_number='CLINICAL-B');db.add_all([a,b]);db.commit();aid,bid=a.id,b.id
    def add(cid,kind,fields,**links):
        return client.post(f'/api/cases/{cid}/clinical/{kind}',params={'expected_case_version':client.get(f'/api/cases/{cid}').json()['version']},
            json=dict(fields=fields,change_reason='Синтетическая проверка ввода',**links))
    a=add(aid,'patient',{'D.1':{'raw':'A'},'D.2.1':{'raw':'2024'}});assert a.status_code==200,a.text
    b=add(bid,'patient',{'D.1':{'raw':'B'}});assert b.status_code==200,b.text
    assert add(aid,'medical_history',{},parent_id=b.json()['id']).status_code==404
    assert add(aid,'patient',{'D.2.1':{'raw':'20261355'}}).status_code==400
    history=add(aid,'medical_history',{'D.7.1.r.2':{'null_flavor':'UNK'}},parent_id=a.json()['id'])
    assert history.status_code==200,history.text
    assert history.json()['fields']['D.7.1.r.2']['state']=='null_flavor'
    v=client.get(f'/api/cases/{aid}').json()['version']
    url=f'/api/cases/{aid}/clinical/patient/{a.json()["id"]}'
    payload={'fields':{'D.1':None},'change_reason':'Явная очистка текущего значения'}
    params={'expected_case_version':v,'expected_version':a.json()['version']}
    changed=client.patch(url,params=params,json=payload);assert changed.status_code==200,changed.text
    assert changed.json()['fields']['D.1']['state']=='cleared' and changed.json()['fields']['D.2.1']['raw']=='2024'
    assert client.patch(url,params=params,json=payload).status_code==409
    assert client.post(url+'/archive',params={'expected_case_version':client.get(f'/api/cases/{aid}').json()['version'],'expected_version':changed.json()['version']},
        json={'change_reason':'Архивирование без удаления дочерней истории'}).status_code==400
    with SessionLocal() as db:
        row=db.get(Case,aid);row.is_locked=True;db.commit()
    assert add(aid,'reporter',{'local.name_line':{'raw':'Источник'}}).status_code==400
    with SessionLocal() as db:
        user=db.query(User).filter_by(login='test-admin').one();db.query(UserRole).filter_by(user_id=user.id).delete()
        viewer=db.query(Role).filter_by(role_code='readonly_auditor').one();user.role='viewer';db.add(UserRole(user_id=user.id,role_id=viewer.id));db.commit()
    assert add(bid,'reporter',{'local.name_line':{'raw':'Источник'}}).status_code==403
