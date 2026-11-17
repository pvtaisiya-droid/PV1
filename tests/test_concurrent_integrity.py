"""Cross-module regression checks for the final concurrency audit pass."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_concurrent_forms import Forms
from tests.test_concurrent_remaining import seed, snapshot, version


@pytest.mark.parametrize('child', ['comment', 'build_item', 'literature'])
def test_direct_children_invalidate_their_parent_forms(client, child):
    from app import models as m
    from app.database import SessionLocal

    ids = seed()
    with SessionLocal() as db:
        component = db.query(m.PSMFComponent).filter_by(is_deleted=False).first()
        if child == 'comment':
            parent = component
            row = m.PSMFComment(object_type='component', object_id=parent.id, comment_text='Review')
        elif child == 'build_item':
            parent = db.get(m.PSMFPartnerBuild, ids['build'])
            row = m.PSMFPartnerBuildItem(build_id=parent.id, component_id=component.id, code='TEST', title_ru='Review')
        else:
            parent = db.get(m.LiteratureMonitoringPlan, ids['plan'])
            row = db.get(m.LiteratureResult, ids['result'])
            row.publication_title = 'Updated publication'
        key = type(parent).__mapper__.version_id_col.key
        before = getattr(parent, key)
        db.add(row)
        db.commit()
        assert getattr(parent, key) > before


@pytest.mark.parametrize('action', ['create-icsr', 'link'])
def test_literature_action_rejects_stale_external_inputs(client, action):
    from app import models as m
    from app.database import SessionLocal

    ids = seed()
    url = f"/literature-monitoring/results/{ids['result']}/{action}"
    def form():
        data = Forms(client.get('/literature-monitoring?tab=results').text).forms[url]
        if action == 'link':
            data.update(target='rmp', target_id='Reviewed source')
        return data
    data = form()
    with SessionLocal() as db:
        db.get(m.Product, ids['product']).product_name = 'Catalog winner'
        db.commit()
    before = snapshot()
    response = client.post(url, data=data, follow_redirects=False)
    assert response.status_code == 409, response.text
    assert snapshot() == before
    assert client.post(url, data=form(), follow_redirects=False).status_code == 303


@pytest.mark.parametrize('failure', ['audit', 'commit'])
def test_task_completion_rolls_back_date_actor_and_audit(client, monkeypatch, failure):
    from app import crud
    from app.database import SessionLocal
    from sqlalchemy.orm import Session

    ids = seed()
    before = snapshot()
    if failure == 'audit':
        original = crud.log_field_changes
        def fail(db, *args, **kwargs):
            original(db, *args, **kwargs)
            db.flush()
            raise RuntimeError('Synthetic task audit failure')
        monkeypatch.setattr(crud, 'log_field_changes', fail)
    else:
        def fail(db):
            db.flush()
            raise RuntimeError('Synthetic task commit failure')
        monkeypatch.setattr(Session, 'commit', fail)
    with pytest.raises(RuntimeError, match='Synthetic task'):
        client.post(f"/tasks/{ids['task']}/status", data={
            'expected_version': version('Task', ids['task']), 'status_value': 'completed'}, follow_redirects=False)
    assert snapshot() == before


def test_separate_e2b_packages_cannot_create_same_case_from_old_previews(client):
    from tests.test_concurrent_e2b import form
    from tests.test_e2b_import import seed_product, e2b_xml, make_zip, upload_package, latest_batch
    from app.database import SessionLocal
    from app.models import Case, E2BImportBatch

    seed_product()
    batches = []
    for filename in ('first.xml', 'second.xml'):
        assert upload_package(client, make_zip({filename: e2b_xml()})).status_code == 303
        batches.append(latest_batch().id)
    forms = [form(client, identifier, 'confirm') for identifier in batches]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: client.post(pair[0], data=pair[1], follow_redirects=False), forms))
    assert sorted(response.status_code for response in results) == [303, 409]
    with SessionLocal() as db:
        assert db.query(Case).filter_by(is_deleted=False).count() == 1
        assert sum(db.get(E2BImportBatch, identifier).status == 'imported' for identifier in batches) == 1


def test_direct_reconciliation_creation_serializes_the_period(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import PartnerReconciliation
    from sqlalchemy.orm.exc import StaleDataError

    ids = seed()
    payload = schemas.PartnerReconciliationCreate(partner_id=ids['partner'], period_start='2026-10-01', period_end='2026-10-31')
    def save(_):
        with SessionLocal() as db:
            try:
                crud.create_partner_reconciliation(db, payload, [])
                return 'created'
            except StaleDataError:
                db.rollback()
                return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == ['conflict', 'created']
    with SessionLocal() as db:
        assert db.query(PartnerReconciliation).filter_by(partner_id=ids['partner']).count() == 1


def test_raw_psmf_import_rejects_stale_mapping_and_bumps_both_versions(client):
    from tools.psmf_import_from_docs import get_components, update_components
    from app.database import engine, SessionLocal
    from app.models import PSMFComponentVersion

    path = engine.url.database
    row = next(row for row in get_components(path) if row['current_version_id'])
    row.update(source='draft_v4', content='Synthetic imported text')
    with SessionLocal() as db:
        text = db.get(PSMFComponentVersion, row['current_version_id'])
        text.content = 'Concurrent winner'
        db.commit()
    before = snapshot()
    with pytest.raises(RuntimeError, match='changed'):
        update_components(path, [row], apply=True)
    assert snapshot() == before
    fresh = next(item for item in get_components(path) if item['id'] == row['id'])
    fresh.update(source='draft_v4', content='Synthetic imported text')
    update_components(path, [fresh], apply=True)
    result = next(item for item in get_components(path) if item['id'] == row['id'])
    assert result['expected_parent_version'] > fresh['expected_parent_version']
    assert result['expected_content_version'] > fresh['expected_content_version']
    assert result['current_content'] == 'Synthetic imported text'


@pytest.mark.parametrize('reset', [False, True])
def test_raw_import_late_conflict_rolls_back_earlier_components(client, reset):
    from tools.psmf_import_from_docs import get_components, update_components, reset_component_versions
    from app.database import engine, SessionLocal
    from app.models import PSMFComponentVersion

    path = engine.url.database
    mapping = [row for row in get_components(path) if row['current_version_id']][:2]
    assert len(mapping) == 2
    for row in mapping:
        row.update(source='draft_v4', content='Synthetic text', approved_content='Approved', draft_content='Draft')
    with SessionLocal() as db:
        db.get(PSMFComponentVersion, mapping[1]['current_version_id']).content = 'Concurrent winner'
        db.commit()
    before = snapshot()
    with pytest.raises(RuntimeError, match='changed'):
        (reset_component_versions if reset else update_components)(path, mapping, apply=True)
    assert snapshot() == before
