import pytest
from sqlalchemy import text
from tests.test_concurrent_forms import Forms


def snapshot():
    from app.database import SessionLocal
    from app.models import SafetyReport, SafetyReportProduct, Case, CaseProduct, AuditTrail, CaseNumberCounter
    with SessionLocal() as db:
        return {model.__tablename__: db.execute(text(
            f'SELECT * FROM "{model.__tablename__}" ORDER BY id')).all()
            for model in (SafetyReport, SafetyReportProduct, Case, CaseProduct, AuditTrail, CaseNumberCounter)}


def setup(client):
    from app import crud, schemas
    from app.database import SessionLocal
    with SessionLocal() as db:
        product = crud.create_product(db, schemas.ProductCreate(product_name='Synthetic product'))
        report = crud.create_safety_report(db, schemas.SafetyReportCreate(
            raw_subject='Synthetic source', raw_text='Synthetic narrative',
            message_direction='partner_to_our_company', product_ids=[product.id]))
        return report.id


def submission(client, report_id, action, api):
    url = f'/safety-reports/{report_id}/{action}'
    if api:
        version = client.get(f'/api/safety-reports/{report_id}').json()['version']
        data = {'expected_version': version}
        if action == 'create-case':
            data['expected_inputs'] = client.get(f'/api/safety-reports/{report_id}').json()['input_snapshot']
        if action == 'triage': data.update(triage_status='in_triage', triage_comment='Updated')
        if action == 'products': data['product_ids'] = []
        return '/api' + url, data
    data = Forms(client.get(f'/safety-reports/{report_id}').text).forms[url]
    if action == 'direction': data['message_direction'] = 'our_company_to_partner'
    if action == 'triage': data.update(triage_status='in_triage', triage_comment='Updated')
    return url, data


def send(client, url, data, action, api):
    if api:
        if action == 'create-case': return client.post(url, params=data, follow_redirects=False)
        method = client.put if action == 'products' else client.patch
        return method(url, json=data, follow_redirects=False)
    return client.post(url, data=data, follow_redirects=False)


ACTIONS = [(action, False) for action in ('direction', 'products', 'triage', 'create-case')]
ACTIONS += [(action, True) for action in ('products', 'triage', 'create-case')]


@pytest.mark.parametrize('action,api', ACTIONS)
def test_stale_form_and_api_then_reload(client, action, api):
    from app.database import SessionLocal
    from app.models import SafetyReport
    report_id = setup(client)
    url, data = submission(client, report_id, action, api)
    with SessionLocal() as db:
        db.get(SafetyReport, report_id).raw_text = 'Winner'
        db.commit()
    before = snapshot()
    assert send(client, url, data, action, api).status_code == 409
    assert snapshot() == before
    url, data = submission(client, report_id, action, api)
    assert send(client, url, data, action, api).status_code in (200, 303)


@pytest.mark.parametrize('action,api', ACTIONS)
@pytest.mark.parametrize('token', [None, 0, -1])
def test_required_positive_version(client, action, api, token):
    report_id = setup(client)
    url, data = submission(client, report_id, action, api)
    if token is None: data.pop('expected_version')
    else: data['expected_version'] = token
    before = snapshot()
    assert send(client, url, data, action, api).status_code == 422
    assert snapshot() == before


@pytest.mark.parametrize('action,api', ACTIONS)
def test_late_conflict_rolls_back_children_numbering_and_audit(client, monkeypatch, action, api):
    from app.database import SessionLocal
    from app.models import SafetyReport
    from app.routers import safety_reports
    report_id = setup(client)
    url, data = submission(client, report_id, action, api)
    original = safety_reports.require_version
    winners = []
    def race(row, version):
        original(row, version)
        with SessionLocal() as db:
            db.get(SafetyReport, report_id).raw_text = 'Winner'
            db.commit()
        winners.append(snapshot())
    monkeypatch.setattr(safety_reports, 'require_version', race)
    assert send(client, url, data, action, api).status_code == 409
    assert snapshot() == winners[0]


@pytest.mark.parametrize('action,api', ACTIONS)
@pytest.mark.parametrize('failure', ['audit', 'commit'])
def test_failure_after_flush_rolls_back_everything(client, monkeypatch, action, api, failure):
    from app import crud
    from sqlalchemy.orm import Session
    report_id = setup(client)
    url, data = submission(client, report_id, action, api)
    before = snapshot()
    def fail(db, *args, **kwargs):
        db.flush()
        raise RuntimeError('Synthetic transaction failure')
    if failure == 'audit':
        monkeypatch.setattr(crud, 'log_audit', fail)
        monkeypatch.setattr(crud, 'log_create_event', fail)
    else:
        monkeypatch.setattr(Session, 'commit', fail)
    with pytest.raises(RuntimeError, match='Synthetic transaction failure'):
        send(client, url, data, action, api)
    assert snapshot() == before


@pytest.mark.parametrize('api', [False, True])
def test_repeated_creation_has_no_second_case_or_audit(client, api):
    report_id = setup(client)
    url, data = submission(client, report_id, 'create-case', api)
    assert send(client, url, data, 'create-case', api).status_code in (200, 303)
    before = snapshot()
    assert send(client, url, data, 'create-case', api).status_code == 409
    assert snapshot() == before
    url, data = submission(client, report_id, 'create-case', True)
    assert send(client, url, data, 'create-case', True).status_code == 200
    assert snapshot() == before


@pytest.mark.parametrize('action,api', ACTIONS)
def test_deleted_source_is_not_mutated(client, action, api):
    from app.database import SessionLocal
    from app.models import SafetyReport
    report_id = setup(client)
    url, data = submission(client, report_id, action, api)
    with SessionLocal() as db:
        db.get(SafetyReport, report_id).is_deleted = True
        db.commit()
    before = snapshot()
    assert send(client, url, data, action, api).status_code == 404
    assert snapshot() == before
