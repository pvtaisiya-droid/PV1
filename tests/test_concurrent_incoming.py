import pytest
from sqlalchemy import text
from tests.test_concurrent_forms import Forms


def snapshot():
    from app.database import SessionLocal
    from app.models import IncomingRequest, IncomingRequestProduct, IncomingRequestCaseLink, Case, Patient, Reaction, CaseProduct, FollowUp, AuditTrail
    with SessionLocal() as db:
        return {m.__tablename__: db.execute(text(f'SELECT * FROM "{m.__tablename__}" ORDER BY id')).all()
                for m in (IncomingRequest, IncomingRequestProduct, IncomingRequestCaseLink, Case, Patient, Reaction, CaseProduct, FollowUp, AuditTrail)}


def setup(client):
    from app import crud, schemas
    from app.database import SessionLocal
    with SessionLocal() as db:
        row = crud.create_incoming_request(db, schemas.IncomingRequestCreate(source_text='Synthetic message', possible_icsr='yes'))
        crud.save_incoming_request_triage(db, row, schemas.IncomingRequestTriageUpdate(
            triage_status='completed', has_identifiable_patient=True, has_identifiable_reporter=True,
            has_suspect_product=True, has_adverse_event=True))
        case = crud.create_case(db, schemas.CaseCreate(case_type='spontaneous', workflow_status='data_entry'))
        return row.id, case.id


def submission(client, row_id, case_id, action, api):
    from app.database import SessionLocal
    from app.models import IncomingRequest, Case
    with SessionLocal() as db:
        rv, cv = db.get(IncomingRequest, row_id).version, db.get(Case, case_id).version
    if api:
        route, method = {'products': ('products', 'put'), 'triage': ('triage', 'patch'),
                         'create-case': ('create-case', 'post'), 'link-case': ('case-links', 'post')}[action]
        data = {'expected_version': rv}
        if action == 'create-case':
            data['expected_inputs'] = next(row['input_snapshot'] for row in client.get('/api/incoming-requests').json() if row['id'] == row_id)
        if action == 'link-case': data.update(case_id=case_id, expected_case_version=cv, link_type='follow_up')
        return f'/api/incoming-requests/{row_id}/{route}', method, data
    page = f'/incoming-requests/{row_id}/case-preview' if action == 'create-case' else '/incoming-requests'
    url = f'/incoming-requests/{row_id}/{action}'
    data = Forms(client.get(page).text).forms[url]
    if action == 'status': data['status_value'] = 'awaiting_information'
    if action == 'link-case': data.update(case_id=case_id, link_type='follow_up')
    return url, 'post', data


def send(client, url, method, data, api):
    return getattr(client, method)(url, **({'json': data} if api else {'data': data}), follow_redirects=False)


ACTIONS = [(a, False) for a in ('products', 'triage', 'status', 'create-case', 'link-case')] + [(a, True) for a in ('products', 'triage', 'create-case', 'link-case')]


@pytest.mark.parametrize('action,api', ACTIONS)
def test_stale_message_form_or_api_has_no_side_effects(client, action, api):
    from app.database import SessionLocal
    from app.models import IncomingRequest
    row_id, case_id = setup(client)
    url, method, data = submission(client, row_id, case_id, action, api)
    with SessionLocal() as db:
        db.get(IncomingRequest, row_id).source_text = 'Winner'
        db.commit()
    before = snapshot()
    assert send(client, url, method, data, api).status_code == 409
    assert snapshot() == before
    url, method, data = submission(client, row_id, case_id, action, api)
    assert send(client, url, method, data, api).status_code in (200, 201, 303)


@pytest.mark.parametrize('action,api', ACTIONS)
@pytest.mark.parametrize('token', [None, 0])
def test_required_positive_version(client, action, api, token):
    row_id, case_id = setup(client)
    url, method, data = submission(client, row_id, case_id, action, api)
    if token is None: data.pop('expected_version')
    else: data['expected_version'] = token
    before = snapshot()
    assert send(client, url, method, data, api).status_code == 422
    assert snapshot() == before


@pytest.mark.parametrize('api', [False, True])
@pytest.mark.parametrize('change', ['edit', 'close', 'delete'])
def test_link_checks_case_version_and_state(client, api, change):
    from app.database import SessionLocal
    from app.models import Case
    row_id, case_id = setup(client)
    url, method, data = submission(client, row_id, case_id, 'link-case', api)
    with SessionLocal() as db:
        case = db.get(Case, case_id)
        case.narrative = 'Winner'
        if change == 'close': case.workflow_status, case.is_locked = 'closed', True
        if change == 'delete': case.is_deleted = True
        db.commit()
    before = snapshot()
    result = send(client, url, method, data, api)
    assert result.status_code in (409, 404, 303)
    assert snapshot() == before
    if change == 'close':
        url, method, data = submission(client, row_id, case_id, 'link-case', api)
        assert send(client, url, method, data, api).status_code in (400, 303)
        assert snapshot() == before


@pytest.mark.parametrize('action,api', ACTIONS)
def test_late_message_conflict_rolls_back_children_and_audit(client, monkeypatch, action, api):
    from app.database import SessionLocal
    from app.models import IncomingRequest
    from app.routers import incoming_requests
    row_id, case_id = setup(client)
    url, method, data = submission(client, row_id, case_id, action, api)
    original = incoming_requests.require_version
    winners = []
    def race(row, version):
        original(row, version)
        if type(row).__name__ == 'IncomingRequest':
            with SessionLocal() as db:
                db.get(IncomingRequest, row_id).source_text = 'Winner'
                db.commit()
            winners.append(snapshot())
    monkeypatch.setattr(incoming_requests, 'require_version', race)
    assert send(client, url, method, data, api).status_code == 409
    assert snapshot() == winners[0]

@pytest.mark.parametrize('api', [False, True])
def test_late_case_conflict_rolls_back_link_follow_up_and_audit(client, monkeypatch, api):
    from app.database import SessionLocal
    from app.models import Case
    from app.routers import incoming_requests
    row_id, case_id = setup(client)
    url, method, data = submission(client, row_id, case_id, 'link-case', api)
    original = incoming_requests.require_version
    winners = []
    def race(row, version):
        original(row, version)
        if type(row).__name__ == 'Case':
            with SessionLocal() as db:
                case = db.get(Case, case_id)
                case.workflow_status, case.is_locked = 'closed', True
                db.commit()
            winners.append(snapshot())
    monkeypatch.setattr(incoming_requests, 'require_version', race)
    assert send(client, url, method, data, api).status_code == 409
    assert snapshot() == winners[0]


@pytest.mark.parametrize('action', ['create-case', 'link-case', 'products', 'triage', 'status'])
@pytest.mark.parametrize('failure', ['audit', 'commit'])
def test_failure_after_flush_rolls_back_transaction(client, monkeypatch, action, failure):
    from app import crud
    from sqlalchemy.orm import Session
    row_id, case_id = setup(client)
    url, method, data = submission(client, row_id, case_id, action, False)
    before = snapshot()
    def fail(db, *args, **kwargs):
        db.flush()
        raise RuntimeError('Synthetic transaction failure')
    if failure == 'audit':
        monkeypatch.setattr(crud, 'log_audit', fail)
        monkeypatch.setattr(crud, 'log_create_event', fail)
        monkeypatch.setattr(crud, 'log_field_changes', fail)
    else:
        monkeypatch.setattr(Session, 'commit', fail)
    with pytest.raises(RuntimeError, match='Synthetic transaction failure'):
        send(client, url, method, data, False)
    assert snapshot() == before


@pytest.mark.parametrize('api', [False, True])
def test_duplicate_creation_from_same_preview_is_rejected(client, api):
    row_id, case_id = setup(client)
    url, method, data = submission(client, row_id, case_id, 'create-case', api)
    assert send(client, url, method, data, api).status_code in (201, 303)
    before = snapshot()
    assert send(client, url, method, data, api).status_code == 409
    assert snapshot() == before

@pytest.mark.parametrize('api', [False, True])
@pytest.mark.parametrize('value', [None, 'invalid', 0])
def test_link_requires_selected_case_version(client, api, value):
    row_id, case_id = setup(client)
    url, method, data = submission(client, row_id, case_id, 'link-case', api)
    field = 'expected_case_version' if api else 'expected_case_versions'
    if value is None: data.pop(field)
    elif api: data[field] = value
    else: data[field] = '{"' + case_id + '":0}' if value == 0 else value
    before = snapshot()
    assert send(client, url, method, data, api).status_code == 422
    assert snapshot() == before


def test_api_exposes_current_message_version(client):
    from app.database import SessionLocal
    from app.models import IncomingRequest
    row_id, _ = setup(client)
    rows = client.get('/api/incoming-requests').json()
    row = next(row for row in rows if row['id'] == row_id)
    with SessionLocal() as db:
        assert row['version'] == db.get(IncomingRequest, row_id).version
