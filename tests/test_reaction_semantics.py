import json
import sqlite3
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from tests.test_e2b_import import seed_product, upload_xml, preview_data, Forms
from tests.form_helpers import post_current

FIXTURE = Path(__file__).parent/'fixtures/e2b_r3_reactions/source.xml'


def test_coded_reaction():
    from app.e2b_import import E2BParser, normalize_seriousness, parsed_case_flags
    from app.reaction_semantics import H, CRITERIA, path
    parser=E2BParser(); root=ET.fromstring(FIXTURE.read_bytes())
    parsed=parser.parse(ET.tostring(root)); r=parsed.reactions[0]
    assert r['meddra_llt_code']=='10000001' and r['meddra_version']=='29.0' and r['meddra_pt_code'] is None
    assert r['reported_term']=='Синтетическая реакция'
    assert r['seriousness_death'] is True and r['seriousness_life_threatening'] is False
    assert r['seriousness_hospitalization'] is None and r['seriousness_disability'] is None
    assert r['seriousness_congenital_anomaly'] is None
    facts=r['source_facts']
    assert facts['criteria']['seriousness_hospitalization']['state']=='null_flavor'
    assert facts['criteria']['seriousness_disability']['state']=='invalid'
    assert facts['criteria']['seriousness_congenital_anomaly']['state']=='missing'
    assert r['reported_seriousness'] is False and r['is_serious'] is True
    assert r['outcome']=='recovered/resolved' and any('противоречит' in w for w in facts['warnings'])
    for e in root.iter(): e.attrib.pop('displayName',None)
    other=parser.parse(ET.tostring(root)).reactions[0]
    for key in [*CRITERIA,'is_serious','outcome','meddra_llt_code','meddra_version']:
        assert other[key]==r[key]
    obs=root.find('.//'+H+'primaryRole/'+path('subjectOf2/observation'))
    for child in list(obs):
        if child.tag==H+'outboundRelationship2': obs.remove(child)
    obs.find(H+'value').attrib.pop('codeSystemVersion')
    unknown=parser.parse(ET.tostring(root)); u=unknown.reactions[0]
    assert u['meddra_version'] is None and u['is_serious'] is None and u['outcome'] is None
    assert normalize_seriousness(unknown.message,unknown.reactions)=='unknown'
    assert parsed_case_flags(unknown.as_dict())['submission_due_date'] is None
    bad=FIXTURE.read_text('utf-8').replace('code="1" codeSystem="2.16.840.1.113883.3.989.2.1.1.11"','code="999" codeSystem="2.16.840.1.113883.3.989.2.1.1.11"')
    b=parser.parse(bad.encode()).reactions[0]
    assert b['outcome'] is None and b['source_facts']['outcome']['raw']=='999'
    assert b['source_facts']['outcome']['state']=='unsupported'


def test_import_read_and_patch(client):
    from app.database import SessionLocal
    from app.models import Case, Reaction, E2BTransmission, AuditTrail
    seed_product()
    response=upload_xml(client,FIXTURE.read_bytes())
    preview=response.headers['location'].split('?')[0]
    assert '10000001' in client.get(preview).text and 'PT: не установлен' in client.get(preview).text
    response=post_current(client,preview+'/confirm',data=preview_data(client,preview,'confirm'),follow_redirects=False)
    assert response.headers['location'].startswith('/cases/')
    with SessionLocal() as db:
        case=db.query(Case).one(); reaction=db.query(Reaction).one(); tx=db.query(E2BTransmission).one()
        case_id,rid=case.id,reaction.id
        original_json=tx.normalized_json; original_facts=reaction.source_facts_json
        assert json.loads(original_facts)['import_source']['item_id']==tx.import_item_id
        assert case.seriousness=='serious'
    read=client.get(f'/api/cases/{case_id}/overview').json()['reactions'][0]
    assert read['meddra_llt_code']=='10000001' and read['meddra_pt_code'] is None and read['meddra_version']=='29.0'
    assert read['seriousness_hospitalization'] is None and read['reaction_provenance']['dictionary_validation']=='not_performed'
    version=client.get(f'/api/cases/{case_id}').json()['version']
    url=f'/api/cases/{case_id}/reactions/{rid}?expected_version={version}&expected_reaction_version={read["version"]}'
    result=client.patch(url,json={'meddra_llt_code':None,'change_reason':'Синтетическая правка: код требует проверки.'})
    assert result.status_code==200,result.text
    data=result.json()
    assert data['meddra_llt_code'] is None and data['meddra_version']=='29.0'
    assert data['seriousness_death'] is True and data['seriousness_life_threatening'] is False
    assert data['seriousness_hospitalization'] is None
    assert client.patch(url,json={'outcome':None,'change_reason':'Устаревшая форма проверки'}).status_code==409
    with SessionLocal() as db:
        assert db.get(Reaction,rid).source_facts_json==original_facts
        assert db.query(E2BTransmission).one().normalized_json==original_json
        assert db.query(AuditTrail).filter_by(entity_type='Reaction',entity_id=rid,action='update').count()==1
    edit=f'/cases/{case_id}/reactions/{rid}/edit'
    page=client.get(edit)
    assert page.status_code==200 and 'Неизвестно / не указано' in page.text
    form=Forms(page.text).forms[edit]
    form.update(change_reason='Ручная сверка синтетического исхода',is_serious='',seriousness_death='',
        seriousness_life_threatening='false',seriousness_hospitalization='',outcome='')
    assert client.post(edit,data=form,follow_redirects=False).status_code==303
    with SessionLocal() as db:
        r=db.get(Reaction,rid)
        assert r.seriousness_death is None and r.seriousness_life_threatening is False
        assert r.is_serious is None and r.source_facts_json==original_facts
    assert client.get(f'/cases/{case_id}').status_code==200


def test_manual_and_guards(client):
    from app.database import SessionLocal
    from app.models import Case, Reaction, User, UserRole, Role
    with SessionLocal() as db:
        case=Case(case_number='SYNTH-MANUAL'); db.add(case); db.commit(); cid=case.id
    response=post_current(client,f'/api/cases/{cid}/reactions',json={'reported_term':'Синтетическая реакция',
        'meddra_llt_code':'10000001','meddra_version':'29.0','seriousness_hospitalization':False})
    assert response.status_code==200,response.text
    row=response.json(); rid=row['id']
    assert row['seriousness_hospitalization'] is False and row['seriousness_death'] is None and row['is_serious'] is None
    # Existing add form, including omitted unknowns and explicit false.
    added=post_current(client,f'/cases/{cid}/reactions',data={'reported_term':'Ручной ввод',
        'meddra_llt_code':'10000002','meddra_version':'29.0','is_serious':'','seriousness_death':'false'},follow_redirects=False)
    assert added.status_code==303
    with SessionLocal() as db:
        r=db.query(Reaction).filter_by(reported_term='Ручной ввод').one()
        assert r.seriousness_death is False and r.seriousness_hospitalization is None and r.is_serious is None
        legacy=Reaction(case_id=cid,reported_term='Историческая',meddra_pt_code='10009999',is_serious=False,
            seriousness_death=False,seriousness_hospitalization=False)
        db.add(legacy); db.commit(); legacy_id=legacy.id; legacy_version=legacy.version
        version=db.get(Case,cid).version
    result=client.patch(f'/api/cases/{cid}/reactions/{legacy_id}?expected_version={version}&expected_reaction_version={legacy_version}',
        json={'outcome':'Только уточнение исхода','change_reason':'Не подтверждаем исторические критерии или MedDRA'})
    assert result.status_code==200,result.text
    facts=result.json()['reaction_provenance']
    assert 'историческое' in facts['field_statuses']['seriousness_death']
    assert 'историческое' in facts['field_statuses']['meddra_pt_code']
    assert result.json()['is_serious'] is False and result.json()['meddra_llt_code'] is None
    assert 'историческое значение' in client.get(f'/cases/{cid}').text
    with SessionLocal() as db:
        case=db.get(Case,cid); case.is_locked=True; db.commit(); version=case.version
    url=f'/api/cases/{cid}/reactions/{rid}?expected_version={version}&expected_reaction_version={row["version"]}'
    assert client.patch(url,json={'outcome':'new','change_reason':'Попытка изменить закрытый случай'}).status_code==400
    with SessionLocal() as db:
        user=db.query(User).filter_by(login='test-admin').one()
        db.query(UserRole).filter_by(user_id=user.id).delete()
        viewer=db.query(Role).filter_by(role_code='readonly_auditor').one()
        user.role='viewer'; db.add(UserRole(user_id=user.id,role_id=viewer.id)); db.commit()
    assert client.patch(url,json={'outcome':'new','change_reason':'Нет права редактирования'}).status_code==403
    with SessionLocal() as db: assert db.get(Reaction,rid).outcome is None


def test_upgrade_0030(tmp_path):
    import sqlalchemy as sa
    from migration_support.schema_20260930 import metadata
    from tests.test_migrations import migrate_test_database, assert_matches_current_models
    dbpath=tmp_path/'upgrade.db'; result=migrate_test_database(dbpath,'20261110_0030')
    assert result.returncode==0,result.stderr
    engine=sa.create_engine(f'sqlite:///{dbpath.as_posix()}')
    common=dict(created_at=datetime(2025,1,1),updated_at=datetime(2025,1,1),is_active=True,is_deleted=False,version=1)
    tables=['tblCases','tblReactions','tblCaseProducts','tblCaseProductReactionAssessments']
    with engine.begin() as conn:
        for i in range(2):
            conn.execute(metadata.tables['tblCases'].insert().values(**common,id=f'c{i}',case_number=f'C-{i}',
                number_is_provisional=False,case_version=1,workflow_status='new',is_locked=False))
            conn.execute(metadata.tables['tblReactions'].insert().values(**common,id=f'r{i}',case_id=f'c{i}',reported_term='Synthetic',
                meddra_pt_code=f'1000000{i}',meddra_version='28.0',is_serious=bool(i),seriousness_death=bool(i),
                seriousness_life_threatening=False,seriousness_hospitalization=False,seriousness_disability=False,
                seriousness_congenital_anomaly=False,seriousness_other_medically_important=False))
            conn.execute(metadata.tables['tblCaseProducts'].insert().values(**common,id=f'p{i}',case_id=f'c{i}',drug_role='suspect'))
            conn.execute(metadata.tables['tblCaseProductReactionAssessments'].insert().values(**common,id=f'a{i}',case_product_id=f'p{i}',reaction_id=f'r{i}'))
    engine.dispose()
    with sqlite3.connect(dbpath) as conn: before={t:conn.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall() for t in tables}
    result=migrate_test_database(dbpath); assert result.returncode==0,result.stderr
    with sqlite3.connect(dbpath) as conn:
        for t,rows in before.items():
            after=conn.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall()
            assert [r[:len(rows[0])] for r in after]==rows
        assert conn.execute('SELECT interpretation_profile,source_facts_json,reported_seriousness FROM tblReactions').fetchall()==[(None,None,None)]*2
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='20261110_0032'
    assert_matches_current_models(dbpath)
