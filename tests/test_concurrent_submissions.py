import re
from datetime import date

import pytest
from sqlalchemy import text

from tests.test_concurrent_forms import Forms


def setup(client):
    from app.database import SessionLocal
    from app.models import Case, Partner, PSURPlan, Submission, Substance
    with SessionLocal() as db:
        partner = Partner(partner_code='SUBTEST', partner_name='Synthetic recipient')
        substance = Substance(substance_name='Synthetic substance')
        db.add_all([partner, substance])
        db.flush()
        case = Case(case_number='SUB-CASE', numbering_partner_id=partner.id,
                    partner_id=partner.id, number_is_provisional=False,
                    workflow_status='data_entry')
        plan = PSURPlan(active_substance_id=substance.id, reporting_period_start=date(2026, 1, 1),
                        reporting_period_end=date(2026, 6, 30), data_lock_point=date(2026, 6, 30))
        db.add_all([case, plan])
        db.flush()
        submission = Submission(submission_number='SUB-EXISTING', case_id=case.id)
        db.add(submission)
        db.commit()
        return dict(case=case.id, pbrer=plan.id, status=submission.id)


def snapshot():
    from app.database import SessionLocal
    from app.models import AuditTrail, Case, PSURPlan, Submission
    with SessionLocal() as db:
        return {m.__tablename__: db.execute(text(
            f'SELECT * FROM "{m.__tablename__}" ORDER BY "{m.__mapper__.primary_key[0].name}"')).all()
            for m in (Case, PSURPlan, Submission, AuditTrail)}


ROUTES = [('status', False, False), ('status', True, False),
          ('case', False, False), ('case', True, False),
          ('case', False, True), ('case', True, True),
          ('pbrer', False, False), ('pbrer', True, False)]


def request(client, ids, kind, api, nested):
    from app.database import SessionLocal
    from app.models import Case, PSURPlan, Submission
    model = {'case': Case, 'pbrer': PSURPlan, 'status': Submission}[kind]
    if kind == 'status':
        url = f'/submissions/{ids[kind]}/status'
        data = dict(submission_status='acknowledged', acknowledgement_code='RECEIPT')
        if not api:
            data.update(Forms(client.get('/submissions').text).forms[url])
        else:
            with SessionLocal() as db:
                data['expected_version'] = db.get(model, ids[kind]).version
    else:
        url = f'/cases/{ids[kind]}/submissions' if nested else '/submissions'
        data = dict(submission_object_type=kind, submission_number='SUB-NEW',
                    **{('case_id' if kind == 'case' else 'pbrer_id'): ids[kind]})
        if api:
            with SessionLocal() as db:
                data['expected_object_version'] = db.get(model, ids[kind]).version
        else:
            page = f'/cases/{ids[kind]}?tab=documents' if nested else '/submissions'
            html = client.get(page)
            assert html.status_code == 200
            fields = Forms(html.text).forms[url]
            data.update({k: v for k, v in fields.items() if k in ('csrf_token', 'expected_object_version')})
            if not nested:
                # Simulate selecting the rendered option, as the page's change listener does.
                match = re.search(rf'<option value="{ids[kind]}" data-version="(\d+)"', html.text)
                assert match
                data['expected_object_version'] = match[1]
    return ('/api' + url if api else url), data


def send(client, kind, api, url, data):
    method = client.patch if api and kind == 'status' else client.post
    return method(url, **({'json': data} if api else {'data': data}), follow_redirects=False)


def change(ids, kind, state='edit'):
    from app.database import SessionLocal
    from app.models import Case, PSURPlan, Submission
    model = {'case': Case, 'pbrer': PSURPlan, 'status': Submission}[kind]
    with SessionLocal() as db:
        row = db.get(model, ids[kind])
        if state == 'delete':
            row.is_deleted = True
        elif kind == 'case':
            if state == 'close':
                row.workflow_status, row.is_locked = 'closed', True
            elif state == 'provisional':
                row.number_is_provisional = True
            else:
                row.narrative = 'Winner'
        elif kind == 'pbrer':
            row.status = 'Submitted'
        else:
            row.error_message = 'Winner'
        db.commit()


@pytest.mark.parametrize('kind,api,nested', ROUTES)
def test_stale_request_then_reload(client, kind, api, nested):
    ids = setup(client)
    url, data = request(client, ids, kind, api, nested)
    change(ids, kind)
    before = snapshot()
    assert send(client, kind, api, url, data).status_code == 409
    assert snapshot() == before
    url, data = request(client, ids, kind, api, nested)
    response = send(client, kind, api, url, data)
    assert response.status_code in (200, 201, 303), response.text
    if api:
        assert response.json()['version'] > 0


@pytest.mark.parametrize('kind,api,nested', ROUTES)
@pytest.mark.parametrize('token', [None, 0, -1])
def test_required_positive_token(client, kind, api, nested, token):
    ids = setup(client)
    url, data = request(client, ids, kind, api, nested)
    field = 'expected_version' if kind == 'status' else 'expected_object_version'
    if token is None:
        data.pop(field)
    else:
        data[field] = token
    before = snapshot()
    assert send(client, kind, api, url, data).status_code == 422
    assert snapshot() == before


@pytest.mark.parametrize('kind,api,nested', ROUTES)
def test_late_conflict_rolls_back_submission_parent_and_audit(client, monkeypatch, kind, api, nested):
    from app import crud
    ids = setup(client)
    url, data = request(client, ids, kind, api, nested)
    original = crud.require_version
    winners = []
    def race(row, version):
        original(row, version)
        change(ids, kind, 'close' if kind == 'case' else 'edit')
        winners.append(snapshot())
    monkeypatch.setattr(crud, 'require_version', race)
    assert send(client, kind, api, url, data).status_code == 409
    assert snapshot() == winners[0]


@pytest.mark.parametrize('kind,api,nested', ROUTES)
@pytest.mark.parametrize('failure', ['audit', 'commit'])
def test_failure_after_flush_rolls_back_everything(client, monkeypatch, kind, api, nested, failure):
    from app import crud
    from sqlalchemy.orm import Session
    ids = setup(client)
    url, data = request(client, ids, kind, api, nested)
    before = snapshot()
    def fail(db, *args, **kwargs):
        db.flush()
        raise RuntimeError('Synthetic transaction failure')
    if failure == 'commit':
        monkeypatch.setattr(Session, 'commit', fail)
    elif kind == 'status':
        monkeypatch.setattr(crud, 'log_audit', fail)
    else:
        monkeypatch.setattr(crud, 'log_create_event', fail)
    with pytest.raises(RuntimeError, match='Synthetic transaction failure'):
        send(client, kind, api, url, data)
    assert snapshot() == before


@pytest.mark.parametrize('kind,api,nested', ROUTES)
def test_repeat_old_request_has_no_extra_submission_or_audit(client, kind, api, nested):
    ids = setup(client)
    url, data = request(client, ids, kind, api, nested)
    assert send(client, kind, api, url, data).status_code in (200, 201, 303)
    before = snapshot()
    assert send(client, kind, api, url, data).status_code == 409
    assert snapshot() == before


@pytest.mark.parametrize('kind,api,nested', ROUTES)
def test_deleted_record_is_rejected(client, kind, api, nested):
    ids = setup(client)
    url, data = request(client, ids, kind, api, nested)
    change(ids, kind, 'delete')
    before = snapshot()
    assert send(client, kind, api, url, data).status_code in (400, 404, 303)
    assert snapshot() == before


@pytest.mark.parametrize('api,nested', [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize('state', ['close', 'provisional'])
def test_current_version_does_not_bypass_case_state(client, api, nested, state):
    ids = setup(client)
    change(ids, 'case', state)
    url, data = request(client, ids, 'case', api, nested)
    before = snapshot()
    assert send(client, 'case', api, url, data).status_code in (400, 303)
    assert snapshot() == before


@pytest.mark.parametrize('kind', ['case', 'pbrer'])
def test_missing_source_and_mismatched_type_do_not_create_or_audit(client, kind):
    ids = setup(client)
    url, data = request(client, ids, kind, True, False)
    before = snapshot()
    data['submission_object_type'] = 'pbrer' if kind == 'case' else 'case'
    assert send(client, kind, True, url, data).status_code == 400
    assert snapshot() == before
    data['submission_object_type'] = kind
    data['case_id' if kind == 'case' else 'pbrer_id'] = 'missing'
    assert send(client, kind, True, url, data).status_code == 400
    assert snapshot() == before


def test_api_and_html_expose_versions(client):
    ids = setup(client)
    assert client.get(f"/api/cases/{ids['case']}").json()['version'] > 0
    assert client.get(f"/api/psur/{ids['pbrer']}").json()['version'] > 0
    rows = client.get('/api/submissions').json()
    assert next(r for r in rows if r['id'] == ids['status'])['version'] > 0
    html = client.get('/submissions').text
    assert 'caseSelect.addEventListener' in html and 'psurSelect.addEventListener' in html


def test_duplicate_number_update_is_controlled_and_atomic(client):
    from app.database import SessionLocal
    from app.models import Submission
    ids = setup(client)
    with SessionLocal() as db:
        db.add(Submission(submission_number='TAKEN', case_id=ids['case']))
        db.commit()
    url, data = request(client, ids, 'status', True, False)
    data['submission_number'] = 'TAKEN'
    before = snapshot()
    assert send(client, 'status', True, url, data).status_code == 400
    assert snapshot() == before


@pytest.mark.parametrize('kind', ['case', 'pbrer'])
def test_duplicate_number_creation_rolls_back_parent_version(client, kind):
    ids = setup(client)
    url, data = request(client, ids, kind, True, False)
    data['submission_number'] = 'SUB-EXISTING'
    before = snapshot()
    assert send(client, kind, True, url, data).status_code == 422
    assert snapshot() == before


@pytest.mark.parametrize('kind', ['case', 'pbrer'])
def test_late_parent_deletion_rolls_back_submission(client, monkeypatch, kind):
    from app import crud
    ids = setup(client)
    url, data = request(client, ids, kind, True, False)
    original = crud.require_version
    winners = []
    def race(row, version):
        original(row, version)
        change(ids, kind, 'delete')
        winners.append(snapshot())
    monkeypatch.setattr(crud, 'require_version', race)
    assert send(client, kind, True, url, data).status_code == 409
    assert snapshot() == winners[0]


def test_unversioned_rmp_http_request_is_not_accepted(client):
    setup(client)
    before = snapshot()
    response = client.post('/api/submissions', json=dict(
        submission_object_type='rmp', rmp_id='missing', expected_object_version=1))
    assert response.status_code == 400
    assert snapshot() == before
