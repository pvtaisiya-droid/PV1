"""Cross-workflow checks on synthetic installations only."""
import json

import pytest


def test_r3_short_card_updates_canonical_facts_without_rewriting_revision(client):
    from tests.test_e2b_import import seed_product
    from tests.test_clinical_workflow import uploaded, plan, apply
    from tests.test_ae_card import form, save
    from app.database import SessionLocal
    from app import models as m, ae_card
    seed_product()
    bid, rows = uploaded(client)
    assert client.get(f'/safety-reports/e2b-imports/{bid}').status_code == 200
    cid = apply(client, plan(client, rows[0]['id']))
    before = client.get(f'/api/cases/{cid}/revisions').json()[0]
    with SessionLocal() as db:
        case = db.get(m.Case, cid)
        report = db.get(m.SafetyReport, case.safety_report_id)
        rid = report.id
        state = ae_card.state_for(report)
        original_xml = db.get(m.IcsrMessage, rows[0]['id']).source_xml
    state['description'] = 'Синтетическое уточнение через короткую карточку'
    state['patient'].update(age='37 лет', sex='female', other='Синтетический анамнез')
    response = save(client, rid, state)
    assert response.status_code == 303, response.text
    facts = client.get(f'/api/cases/{cid}/clinical').json()['records']
    patient = next(r['fields'] for r in facts if r['kind']=='patient')
    assert patient['D.2.2a']['raw'] == '37.0'
    assert patient['D.2.2b']['raw'] == 'a'
    assert patient['D.5']['raw'] == '2'
    assert any(r['kind']=='narrative' and r['fields'].get('H.1',{}).get('raw')==state['description'] for r in facts)
    with SessionLocal() as db:
        assert db.get(m.IcsrMessage, rows[0]['id']).source_xml == original_xml
        assert db.get(m.IcsrRevision, before['id']).checksum_sha256 == before['checksum']
        assert db.query(m.Patient).filter_by(case_id=cid, is_deleted=False).count() == 1
    # An edit from the clinical screen is shown by the existing short form too.
    with SessionLocal() as db:
        case_version = db.get(m.Case, cid).version
    row = next(r for r in facts if r['kind']=='patient')
    result = client.patch(f'/api/cases/{cid}/clinical/patient/{row["id"]}',
        params={'expected_case_version': case_version, 'expected_version': row['version']},
        json={'fields': {'D.2.2a': {'raw':'38'}}, 'change_reason':'Синтетическое уточнение возраста'})
    assert result.status_code == 200, result.text
    _, page = form(client, rid)
    assert page.context['ae_state']['patient']['age'].startswith('38')


@pytest.mark.parametrize('parent', ['20261112_0030','20261112_0036'])
def test_upgrade_from_either_history_preserves_existing_case(tmp_path, parent):
    import sqlite3
    from tests.test_migrations import migrate_test_database, assert_matches_current_models
    path = tmp_path/'synthetic.db'
    result = migrate_test_database(path, parent)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO tblCases (id,case_number,workflow_status,case_version,version,is_active,is_deleted,number_is_provisional,is_locked,created_at,updated_at) VALUES ('merge-case','SYNTH-MERGE','new',1,1,1,0,0,0,'2026-01-01','2026-01-01')")
        columns = [r[1] for r in db.execute('PRAGMA table_info(tblCases)')]
        before = db.execute('SELECT '+','.join('"'+c+'"' for c in columns)+' FROM tblCases').fetchone()
    result = migrate_test_database(path)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT '+','.join('"'+c+'"' for c in columns)+' FROM tblCases').fetchone()==before
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()==('20261117_0037',)
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert_matches_current_models(path)


def test_r3_inn_choice_is_confirmed_and_catalog_changes_block_stale_plan(client):
    from tests.test_clinical_workflow import uploaded
    from app.database import SessionLocal
    from app import models as m
    _, rows = uploaded(client)
    mid = rows[0]['id']
    with SessionLocal() as db:
        source = json.loads(db.get(m.IcsrMessage, mid).normalized_json)['products'][0]
        assert source['active_substances']
        product = m.Product(product_name='Synthetic catalog brand', product_code='SYNTH-MERGE-DRUG', strength='10 mg')
        db.add(product)
        db.flush()
        pid = product.id
        for name in source['active_substances']:
            substance = m.Substance(substance_name=name, inn_name=name)
            db.add(substance)
            db.flush()
            db.add(m.ProductSubstance(product_id=pid, substance_id=substance.id))
        db.commit()
    def prepare():
        info = client.get(f'/api/e2b/messages/{mid}').json()
        node = next(n for n in info['nodes'] if n['key']=='product:0')
        assert node['product_match']['matched_by']=='inn'
        response = client.post(f'/api/e2b/messages/{mid}/plans', json={
            'mappings': {n['key']:'new' for n in info['nodes']},
            'reason':'Синтетическое подтверждение препарата по МНН',
            'product_choices': {node['key']: {'product_id':pid, 'input_token':node['product_match']['input_token']}}})
        assert response.status_code==200, response.text
        return response.json()['id']
    plan = prepare()
    with SessionLocal() as db:
        db.get(m.Product, pid).strength = '20 mg'
        db.commit()
    assert client.post(f'/api/e2b/plans/{plan}/apply',json={'choices':{}}).status_code==409
    plan = prepare()
    response = client.post(f'/api/e2b/plans/{plan}/apply',json={'choices':{}})
    assert response.status_code==200, response.text
    with SessionLocal() as db:
        row = db.query(m.CaseProduct).filter_by(case_id=response.json()['case_id'], product_id=pid).one()
        assert row.product_match_status=='manual'
        decision = json.loads(db.get(m.IcsrMessage,mid).decision_json)
        assert decision['product_resolutions']['product:0']['manual'] is True


def test_short_card_does_not_assign_case_seriousness_to_several_reactions(client):
    from tests.test_e2b_import import seed_product
    from tests.test_clinical_workflow import uploaded, plan, apply
    from tests.test_ae_card import save
    from app.database import SessionLocal
    from app import models as m, ae_card
    seed_product()
    _, messages = uploaded(client)
    cid = apply(client, plan(client, messages[0]['id']))
    with SessionLocal() as db:
        case = db.get(m.Case,cid)
        assert len(case.reactions)>1
        report = case.safety_report
        rid = report.id
        state = ae_card.state_for(report)
        before = (case.narrative, case.seriousness, [(r.id,r.is_serious) for r in case.reactions])
    state['serious'] = 'no' if state['serious']=='yes' else 'yes'
    state['criteria'] = []
    state['description'] = 'This entire attempted edit must roll back'
    response = save(client, rid, state)
    assert response.status_code==422
    with SessionLocal() as db:
        case=db.get(m.Case,cid)
        assert (case.narrative,case.seriousness,[(r.id,r.is_serious) for r in case.reactions])==before


def test_detailed_r3_import_preserves_company_scope_gate_and_original_source(client):
    from tests.test_clinical_workflow import uploaded, plan, apply
    from tests.test_e2b_import import seed_product
    from app.database import SessionLocal
    from app import models as m
    _, rows = uploaded(client)
    mid = rows[0]['id']
    p = plan(client, mid)
    with SessionLocal() as db:
        original = db.get(m.IcsrMessage, mid).source_xml
    result = client.post(f'/api/e2b/plans/{p["id"]}/apply',json={'choices':{}})
    assert result.status_code==400
    with SessionLocal() as db:
        assert db.query(m.Case).count()==0
        assert db.get(m.IcsrMessage,mid).status=='review'
        assert db.get(m.IcsrMessage,mid).source_xml==original
    seed_product()
    assert apply(client, plan(client,mid))


@pytest.mark.parametrize('r3_first', [True,False])
def test_mixed_r2_r3_package_keeps_unprocessed_case_until_both_are_confirmed(client,r3_first):
    from pathlib import Path
    from tests.test_e2b_import import seed_product,e2b_xml,make_zip,upload_package
    from tests.test_e2b_group_import import group_form
    from tests.form_helpers import post_current
    from app.database import SessionLocal
    from app import models as m
    seed_product()
    r2_xml=e2b_xml(case_id='SYNTH-R2-MIX')
    r3_xml=Path('tests/fixtures/e2b_r3_drugs/source.xml').read_bytes()
    response=upload_package(client,make_zip({'r2/case.xml':r2_xml,'r2/cioms.pdf':b'%PDF SYNTH R2',
        'r3/case.xml':r3_xml,'r3/cioms.pdf':b'%PDF SYNTH R3'}))
    path=response.headers['location'].split('?')[0]
    groups=client.get(path).context['preview']['case_groups']
    first=next(i for i,g in enumerate(groups) if bool(g.get('r3_message_id'))==r3_first)
    second=1-first
    for position,index in enumerate([first,second]):
        url,values,_=group_form(client,path,index)
        result=post_current(client,url,data=values,follow_redirects=False)
        assert result.status_code==303 and 'validation=' not in result.headers['location'],result.headers
        with SessionLocal() as db:
            batch=db.query(m.E2BImportBatch).one()
            assert (batch.status=='imported') == (position==1)
            assert db.query(m.Case).count()==position+1
            assert db.query(m.E2BTransmission).count()==position+1
            for case in db.query(m.Case):
                contents={Path(a.storage_path).read_bytes() for a in db.query(m.Attachment).filter_by(case_id=case.id)}
                is_r2=case.worldwide_case_id=='SYNTH-R2-MIX'
                assert (r2_xml if is_r2 else r3_xml) in contents
                assert (r3_xml if is_r2 else r2_xml) not in contents
                assert (b'%PDF SYNTH R2' if is_r2 else b'%PDF SYNTH R3') in contents
                assert (b'%PDF SYNTH R3' if is_r2 else b'%PDF SYNTH R2') not in contents
