"""Bounded stage 2.4 scenarios; synthetic data, no external dictionaries/services."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from tests.test_e2b_import import seed_product, upload_xml, preview_data, Forms
from tests.form_helpers import post_current

FIXTURE=Path(__file__).parent/'fixtures/e2b_r3_drugs/source.xml'
H='{urn:hl7-org:v3}'


def test_parser():
    from app.e2b_import import E2BParser
    parser=E2BParser(); root=ET.fromstring(FIXTURE.read_bytes())
    parsed=parser.parse(ET.tostring(root)); products=parsed.products
    assert len(products)==2 and [p['drug_role'] for p in products]==['suspect','concomitant']
    assert products[0]['reported_product_name']==products[1]['reported_product_name']=='Test Product'
    assert not any(w.get('blocking') for w in parsed.warnings)
    a,b=products
    assert len(a['dosages'])==len(a['indications'])==len(a['substances'])==len(a['assessments'])==2
    assert a['dosages'][0]['dose_value']=='0.2500' and a['dosages'][0]['interval_value']=='12.00'
    assert a['dosages'][0]['source_facts']['start']['precision']=='year'
    assert a['dosages'][1]['source_facts']['start']['precision']=='month'
    assert a['dosages'][1]['source_facts']['end']['state']=='null_flavor'
    assert a['substances'][0]['denominator_value']=='1.00' and a['substances'][0]['denominator_unit']=='mL'
    assert a['indications'][0]['meddra_llt_code']=='10001001' and a['indications'][0]['meddra_version']=='29.0'
    assert json.loads(a['assessments'][0]['reaction_ref'])[1]=='rx-2'
    assert a['rechallenges'][0]['rechallenge_code']=='2' and b['rechallenges'][0]['rechallenge_code']=='4'
    assert a['coded_identifiers'][0]['code_system']=='urn:synthetic:product'
    assert a['authorization_number']=='SYNTH-AUTH-1'
    assert a['source_facts']['organizer_code']['code']=='4'
    assert a['source_facts']['warnings']==a['warnings']
    # Reorder drug components and reactions: association is by exact root+extension.
    org=root.find('.//'+H+'organizer'); comps=org.findall(H+'component')
    for c in comps:org.remove(c)
    for c in reversed(comps):org.append(c)
    primary=root.find('.//'+H+'primaryRole'); observations=[n for n in primary if n.find(H+'observation') is not None]
    for n in observations:primary.remove(n)
    for n in reversed(observations):primary.append(n)
    other=parser.parse(ET.tostring(root)).products
    assert [p['drug_role'] for p in other]==['concomitant','suspect']
    assert other[1]['assessments'][0]['reaction_ref']==a['assessments'][0]['reaction_ref']
    # Unknown roles, invalid number and absent date remain visibly distinct.
    for ca in root.findall('.//'+H+'causalityAssessment'):
        if ca.find(H+'code').get('code')=='20':ca.find(H+'value').set('code','999')
    dose=root.find('.//'+H+'doseQuantity');dose.set('value','not-a-number')
    low=root.find('.//'+H+'organizer//'+H+'low');low.attrib.clear();low.set('nullFlavor','UNK')
    unknown=parser.parse(ET.tostring(root))
    assert all(p['drug_role']=='unknown' and p['source_facts']['role']['state']=='unsupported' for p in unknown.products)
    assert any('Числовое' in w['message'] for w in unknown.warnings)
    assert any(d['source_facts']['start']['state']=='null_flavor' for p in unknown.products for d in p['dosages'])
    root.find('.//'+H+'ingredient').set('classCode','IACT')
    unsupported=parser.parse(ET.tostring(root))
    assert any(w.get('blocking') and 'ACTI' in w['message'] for w in unsupported.warnings)


def imported(client):
    seed_product(); response=upload_xml(client,FIXTURE.read_bytes())
    preview=response.headers['location'].split('?')[0]
    page=client.get(preview)
    assert page.status_code==200 and 'Показание 2' in page.text and '0.2500' in page.text
    data=preview_data(client,preview,'confirm')
    response=post_current(client,preview+'/confirm',data=data,follow_redirects=False)
    assert response.status_code == 303, response.text
    destination = client.get(response.headers['location'])
    cid = destination.context['report'].case_id
    return cid,preview,data


def versions(client,cid,pid=None,row=None):
    result={'expected_case_version':client.get(f'/api/cases/{cid}').json()['version']}
    if pid:result['expected_parent_version']=client.get(f'/api/cases/{cid}/drug-products/{pid}').json()['version']
    if row:result['expected_version']=row['version']
    return result


def test_import(client):
    from app.database import SessionLocal
    from app.models import Case, CaseProduct, E2BTransmission, AuditTrail, CaseDrugDosage
    cid,preview,old_confirm=imported(client)
    products=client.get(f'/api/cases/{cid}/drug-products').json()
    a=next(p for p in products if p['drug_role']=='suspect');b=next(p for p in products if p['drug_role']=='concomitant')
    assert len(products)==2 and a['product_id']==b['product_id']
    assert len(a['dosages'])==2 and len(a['assessments'])==2
    reactions=client.get(f'/api/cases/{cid}/overview').json()['reactions']
    r2=next(r for r in reactions if r['reported_term']=='Реакция 2')
    assert all(x['reaction_id']==r2['id'] for x in a['assessments']+a['rechallenges'])
    assert all(x['causality_method']=='imported_text' and x['naranjo_score'] is None for x in a['assessments'])
    with SessionLocal() as db:
        tx=db.query(E2BTransmission).one(); original_json=tx.normalized_json
        assert a['dosages'][0]['drug_provenance']['source']['import_source']['item_id']==tx.import_item_id
        assert a['dosages'][0]['drug_provenance']['source']['import_source']['checksum_sha256']
    # PATCH omitted fields stay, explicit null clears only the selected current value.
    row=a['dosages'][0];url=f'/api/cases/{cid}/drug-products/{a["id"]}/dosage/{row["id"]}'
    params=versions(client,cid,a['id'],row)
    changed=client.patch(url,params=params,json={'dose_unit':None,'change_reason':'Синтетическая сверка единицы дозы'})
    assert changed.status_code==200,changed.text
    assert changed.json()['dose_unit'] is None and changed.json()['dose_value']=='0.2500'
    assert changed.json()['drug_provenance']['source']==row['drug_provenance']['source']
    assert client.patch(url,params=params,json={'dose_unit':'g','change_reason':'Устаревшая версия дозы'}).status_code==409
    read=client.get(f'/api/cases/{cid}/drug-products/{a["id"]}').json()
    assert len(read['dosages'])==2 and read['dosages'][1]==a['dosages'][1]
    page=client.get(f'/cases/{cid}/drug-products');assert page.status_code==200 and 'Показание 2' in page.text and 'Реакция 2' in page.text
    assert client.get(f'/cases/{cid}').status_code==200
    # Reconfirmation and a new-message follow-up cannot append/overwrite repeats.
    response=post_current(client,preview+'/confirm',data=old_confirm,follow_redirects=False)
    assert response.status_code in (303,409)
    follow=FIXTURE.read_bytes().replace(b'Message-NotVersion-99',b'Message-Follow-Up-100')
    follow_preview=upload_xml(client,follow).headers['location'].split('?')[0]
    response=post_current(client,follow_preview+'/confirm',data=preview_data(client,follow_preview,'confirm'),follow_redirects=False)
    assert 'validation=' in response.headers.get('location',''),response.text
    with SessionLocal() as db:
        assert db.query(Case).count()==1 and db.query(CaseProduct).count()==2 and db.query(CaseDrugDosage).count()==3
        assert db.query(E2BTransmission).one().normalized_json==original_json
        audit=db.query(AuditTrail).filter_by(entity_id=row['id'],action='update').one()
        assert audit.user_id and audit.change_reason and '0.2500' in audit.old_value


def test_bad_refs(client):
    from app.database import SessionLocal
    from app.models import Case,CaseProduct,E2BImportIssue
    seed_product()
    root=ET.fromstring(FIXTURE.read_bytes())
    node=root.find('.//'+H+'adverseEffectReference/'+H+'id');node.set('extension','missing-rx')
    preview=upload_xml(client,ET.tostring(root)).headers['location'].split('?')[0]
    page=client.get(preview);assert 'Не разрешена однозначная XML-ссылка' in page.text
    # Even a forged confirm request cannot bypass a blocking issue.
    response=post_current(client,preview+'/confirm',data=preview_data(client,preview,'confirm'),follow_redirects=False)
    assert not response.headers.get('location','').startswith('/cases/')
    with SessionLocal() as db:
        assert db.query(Case).count()==db.query(CaseProduct).count()==0
        assert db.query(E2BImportIssue).filter_by(is_blocking=True).count()>0


def test_editor(client):
    from app.database import SessionLocal
    from app.models import Case,Reaction,CaseProduct,CaseDrugDosage,AuditTrail
    with SessionLocal() as db:
        case=Case(case_number='DRUG-MANUAL');db.add(case);db.flush()
        rx=Reaction(case_id=case.id,reported_term='Ручная реакция');db.add(rx);db.commit();cid,rid=case.id,rx.id
    # Working HTML create path and CSRF, then read API.
    formurl=f'/cases/{cid}/drug-editor/product'
    catalog_id=seed_product('Catalog label')
    form=Forms(client.get(formurl).text).forms[formurl]
    form.update(product_id=catalog_id,reported_product_name='Ручной препарат',drug_role='concomitant',change_reason='Первичный синтетический ввод')
    bad=dict(form);bad.pop('csrf')
    assert client.post(formurl,data=bad,follow_redirects=False).status_code==403
    assert client.post(formurl,data=form,follow_redirects=False).status_code==303
    product=client.get(f'/api/cases/{cid}/drug-products').json()[0];pid=product['id']
    assert product['product_id']==catalog_id and product['reported_product_name']=='Ручной препарат'
    edit=client.get(formurl,params={'row_id':pid})
    form=Forms(edit.text).forms[formurl]
    form.update(product_id=catalog_id,drug_role='interacting',authorization_holder='Ручной держатель',change_reason='Уточнение роли препарата по источнику')
    response=client.post(formurl,data=form,follow_redirects=False)
    assert response.status_code==303 and 'validation=' not in response.headers['location']
    changed=client.get(f'/api/cases/{cid}/drug-products/{pid}').json()
    assert changed['drug_role']=='interacting' and changed['authorization_holder']=='Ручной держатель'
    with SessionLocal() as db:
        legacy=db.get(CaseProduct,pid);legacy.drug_role='treatment';db.commit()
    form=Forms(client.get(formurl,params={'row_id':pid}).text).forms[formurl]
    form.update(product_id=catalog_id,drug_role='treatment',authorization_holder='Уточнённый держатель',change_reason='Правка держателя без подтверждения старой роли')
    response=client.post(formurl,data=form,follow_redirects=False)
    assert response.status_code==303 and 'validation=' not in response.headers['location']
    assert client.get(f'/api/cases/{cid}/drug-products/{pid}').json()['drug_role']=='treatment'
    values={'dosage':{'dose_value':'1.2300','dose_unit':'mg','start_raw':'202503'},
        'indication':{'reported_text':'Ручное показание','meddra_llt_code':'10001001','meddra_version':'29.0'},
        'substance':{'reported_substance_name':'Вещество','strength_value':'0.50','denominator_value':'1.00','denominator_unit':'mL'},
        'assessment':{'reaction_id':rid,'assessment_source':'Исследователь','assessment_method_text':'Собственная оценка','relatedness':'Возможно'},
        'rechallenge':{'reaction_id':rid,'rechallenge_code':'3','code_system':'2.16.840.1.113883.3.989.2.1.1.16','code_version':'1.0'}}
    rows={}
    for kind,payload in values.items():
        response=client.post(f'/api/cases/{cid}/drug-products/{pid}/{kind}',params=versions(client,cid,pid),json=payload)
        assert response.status_code==200,response.text
        rows[kind]=response.json()
        edit=f'/cases/{cid}/drug-editor/{kind}'
        page=client.get(edit,params={'product_id':pid,'row_id':rows[kind]['id']})
        assert page.status_code==200,page.text
        form=Forms(page.text).forms[edit];form.update(payload,change_reason='Подтверждение введённых синтетических данных')
        if kind=='dosage':form['dose_unit']='g'
        response=client.post(edit,data=form,follow_redirects=False)
        assert response.status_code==303 and 'validation=' not in response.headers['location'],response.text
    product=client.get(f'/api/cases/{cid}/drug-products/{pid}').json()
    assert product['dosages'][0]['dose_unit']=='g'
    assert product['indications'][0]['meddra_llt_code']=='10001001'
    assert product['rechallenges'][0]['drug_provenance']['rechallenge_meaning'].endswith('результат неизвестен')
    # Archive retains the row/source and audit, and removes it only from active read.
    row=product['dosages'][0]
    response=client.post(f'/api/cases/{cid}/drug-products/{pid}/dosage/{row["id"]}/archive',params=versions(client,cid,pid,row),json={'change_reason':'Ошибочный синтетический режим архивирован'})
    assert response.status_code==200,response.text
    assert client.get(f'/api/cases/{cid}/drug-products/{pid}').json()['dosages']==[]
    with SessionLocal() as db:
        archived=db.get(CaseDrugDosage,row['id']);assert archived.is_deleted and archived.deleted_by
        assert db.query(AuditTrail).filter_by(entity_id=row['id'],action='soft_delete').count()==1


def test_guards(client):
    from app.database import SessionLocal
    from app.models import Case,CaseProduct,CaseDrugDosage,Reaction,User,UserRole,Role
    with SessionLocal() as db:
        a=Case(case_number='GUARD-A');b=Case(case_number='GUARD-B');db.add_all([a,b]);db.flush()
        pa=CaseProduct(case_id=a.id);pb=CaseProduct(case_id=b.id);db.add_all([pa,pb]);db.flush()
        d=CaseDrugDosage(case_product_id=pb.id,dose_value='2');r=Reaction(case_id=b.id,reported_term='Other');db.add_all([d,r]);db.commit()
        cid,pid,foreign_id,foreign_rx=a.id,pa.id,d.id,r.id
    params=versions(client,cid,pid);params['expected_version']=1
    url=f'/api/cases/{cid}/drug-products/{pid}/dosage/{foreign_id}'
    assert client.patch(url,params=params,json={'dose_value':'99','change_reason':'Попытка подменить принадлежность'}).status_code==404
    assert client.post(f'/api/cases/{cid}/drug-products/{pid}/assessment',params=versions(client,cid,pid),json={'reaction_id':foreign_rx,'relatedness':'x'}).status_code==400
    with SessionLocal() as db:
        assert db.get(CaseDrugDosage,foreign_id).dose_value=='2'
        case=db.get(Case,cid);case.is_locked=True;db.commit()
    create=f'/api/cases/{cid}/drug-products/{pid}/dosage'
    assert client.post(create,params=versions(client,cid,pid),json={'dose_value':'1'}).status_code==400
    with SessionLocal() as db:
        user=db.query(User).filter_by(login='test-admin').one();db.query(UserRole).filter_by(user_id=user.id).delete()
        viewer=db.query(Role).filter_by(role_code='readonly_auditor').one();user.role='viewer';db.add(UserRole(user_id=user.id,role_id=viewer.id));db.commit()
    assert client.post(create,params=versions(client,cid,pid),json={'dose_value':'1'}).status_code==403


def test_migration(tmp_path):
    import sqlalchemy as sa
    from tests.test_migrations import migrate_test_database,assert_matches_current_models
    path=tmp_path/'up.db';result=migrate_test_database(path,'20261110_0031');assert result.returncode==0,result.stderr
    engine=sa.create_engine(f'sqlite:///{path.as_posix()}');metadata=sa.MetaData();metadata.reflect(bind=engine)
    common=dict(created_at=datetime(2025,1,1),updated_at=datetime(2025,1,1),is_active=True,is_deleted=False,version=3)
    tables=['tblCases','tblCaseProducts','tblReactions','tblCaseDrugSubstances','tblCaseProductReactionAssessments']
    with engine.begin() as c:
        for i in range(2):
            c.execute(metadata.tables['tblCases'].insert().values(**common,id=f'c{i}',case_number=f'C{i}',number_is_provisional=False,case_version=1,workflow_status='new',is_locked=False))
            c.execute(metadata.tables['tblCaseProducts'].insert().values(**common,id=f'p{i}',case_id=f'c{i}',drug_role='concomitant',
                **(dict(dose_value='0.2500',dose_unit='mg',therapy_start_date_raw='2025',therapy_start_date_precision='year',indication_text='Historical',indication_meddra_pt_code='10009999',rechallenge_result='positive') if i==0 else {})))
            c.execute(metadata.tables['tblReactions'].insert().values(**common,id=f'r{i}',case_id=f'c{i}',reported_term='Historical reaction'))
            c.execute(metadata.tables['tblCaseDrugSubstances'].insert().values(**common,id=f's{i}',case_product_id=f'p{i}',reported_substance_name='Historical',substance_role='active',strength_value='1.00',strength_unit='mg'))
            c.execute(metadata.tables['tblCaseProductReactionAssessments'].insert().values(**common,id=f'a{i}',case_product_id=f'p{i}',reaction_id=f'r{i}',causality_method='naranjo',naranjo_score=4,relatedness='possible'))
    engine.dispose()
    with sqlite3.connect(path) as c:before={t:c.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall() for t in tables}
    result=migrate_test_database(path);assert result.returncode==0,result.stderr
    with sqlite3.connect(path) as c:
        for t,rows in before.items():assert [r[:len(rows[0])] for r in c.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall()]==rows
        assert c.execute('SELECT case_product_id,dose_value,start_raw,interpretation_profile FROM tblCaseDrugDosages').fetchall()==[('p0','0.2500','2025','legacy_single_unverified')]
        assert c.execute('SELECT meddra_llt_code,legacy_meddra_pt_code FROM tblCaseDrugIndications').fetchall()==[(None,'10009999')]
        assert c.execute('SELECT COUNT(*) FROM tblCaseDrugRechallenges').fetchone()[0]==0
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert c.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='20261117_0037'
    assert_matches_current_models(path)
