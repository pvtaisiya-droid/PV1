from datetime import date

import pytest
from sqlalchemy import text

from tests.test_concurrent_forms import Forms


def setup(client):
    from app.database import SessionLocal
    from app.models import (Case, CaseProduct, Partner, Product, PSURCase, PSURPartnerRequest,
                            PSURPlan, PSURSection, Substance)
    with SessionLocal() as db:
        substance = Substance(substance_name='Synthetic substance')
        product = Product(product_code='PSUR-PRODUCT', product_name='Synthetic product')
        partners = [Partner(partner_code=f'PSUR{i}', partner_name=f'Synthetic partner {i}') for i in range(2)]
        db.add_all([substance, product, *partners])
        db.flush()
        plan = PSURPlan(active_substance_id=substance.id,
                        reporting_period_start=date(2026, 1, 1), reporting_period_end=date(2026, 6, 30),
                        data_lock_point=date(2026, 6, 30))
        cases = [Case(case_number=f'PSUR-CASE-{i}', initial_received_date=date(2026, 3, 1),
                      workflow_status='data_entry') for i in range(2)]
        db.add_all([plan, *cases])
        db.flush()
        db.add_all([CaseProduct(case_id=c.id,
                               active_substance_text=substance.substance_name) for c in cases])
        case_row = PSURCase(psur_plan_id=plan.id, case_id=cases[0].id)
        request_row = PSURPartnerRequest(psur_plan_id=plan.id, partner_id=partners[0].id)
        section = PSURSection(psur_plan_id=plan.id, section_code='1', section_title='Synthetic section')
        db.add_all([case_row, request_row, section])
        db.commit()
        return dict(plan=plan.id, product=product.id, partners=[p.id for p in partners],
                    case=case_row.id, request=request_row.id, section=section.id)


def snapshot():
    from app.database import SessionLocal
    from app.models import (AuditTrail, Case, CaseProduct, PSURCase, PSURDocument, PSURPartnerRequest,
                            PSURPlan, PSURProduct, PSURSection, Task)
    with SessionLocal() as db:
        return {m.__tablename__: db.execute(text(
            f'SELECT * FROM "{m.__tablename__}" ORDER BY "{m.__mapper__.primary_key[0].name}"')).all()
            for m in (AuditTrail, Case, CaseProduct, PSURCase, PSURDocument, PSURPartnerRequest,
                      PSURPlan, PSURProduct, PSURSection, Task)}


ACTIONS = ['edit', 'status', 'delete', 'product', 'find', 'case', 'requests', 'request',
           'section', 'generate', 'document', 'task']
CHILDREN = ['case', 'request', 'section', 'generate']


def request(client, ids, action):
    suffix = {'edit': '', 'status': '/status', 'delete': '/delete', 'product': '/products',
              'find': '/find-cases', 'case': f"/cases/{ids['case']}", 'requests': '/partner-requests',
              'request': f"/partner-requests/{ids['request']}", 'section': f"/sections/{ids['section']}",
              'generate': f"/sections/{ids['section']}/generate", 'document': '/documents', 'task': '/tasks'}[action]
    url = f"/psur/{ids['plan']}" + suffix
    page = '/psur' if action == 'delete' else f"/psur/{ids['plan']}"
    response = client.get(page)
    assert response.status_code == 200, response.text
    fields = Forms(response.text).forms[url]
    data = {k: v for k, v in fields.items() if k in ('expected_version', 'expected_row_version', 'csrf_token', 'expected_inputs')}
    data.update({
        'edit': dict(reporting_period_start='2026-01-01', reporting_period_end='2026-06-30',
                     data_lock_point='2026-06-30', frequency='annual'),
        'status': dict(status_value='Approved', change_reason='Synthetic review'),
        'delete': dict(delete_reason='Synthetic removal'),
        'product': dict(product_id=ids['product'], country='RU', included_in_report='true'),
        'find': {},
        'case': dict(case_included='true', assessment_comment='Updated assessment'),
        'requests': dict(partner_ids=ids['partners'], request_type='Cases'),
        'request': dict(partner_id=ids['partners'][0], response_summary='Updated response'),
        'section': dict(section_text='Updated section', human_confirmed='true'),
        'generate': {},
        'document': dict(file_name='Synthetic.docx', comment='Synthetic registry entry'),
        'task': dict(title='Synthetic PSUR task'),
    }[action])
    return url, data


def change(ids, target, deleted=False):
    from app.database import SessionLocal
    from app.models import PSURCase, PSURPartnerRequest, PSURPlan, PSURSection
    target = 'section' if target == 'generate' else target
    model, field = {'plan': (PSURPlan, 'frequency'), 'case': (PSURCase, 'assessment_comment'),
                    'request': (PSURPartnerRequest, 'response_summary'),
                    'section': (PSURSection, 'section_text')}[target]
    with SessionLocal() as db:
        row = db.get(model, ids[target])
        if deleted:
            row.is_deleted = True
        else:
            setattr(row, field, 'Winner')
        db.commit()


@pytest.mark.parametrize('action', ACTIONS)
def test_stale_plan_form_then_reload(client, action):
    ids = setup(client)
    url, data = request(client, ids, action)
    change(ids, 'plan')
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before
    url, data = request(client, ids, action)
    assert client.post(url, data=data, follow_redirects=False).status_code == 303


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('token', [None, 0])
def test_required_plan_version(client, action, token):
    ids = setup(client)
    url, data = request(client, ids, action)
    if token is None:
        data.pop('expected_version')
    else:
        data['expected_version'] = token
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 422
    assert snapshot() == before


@pytest.mark.parametrize('action', CHILDREN)
def test_stale_child_without_parent_change_then_reload(client, action):
    ids = setup(client)
    url, data = request(client, ids, action)
    change(ids, action)
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before
    url, data = request(client, ids, action)
    assert client.post(url, data=data, follow_redirects=False).status_code == 303


@pytest.mark.parametrize('action', CHILDREN)
@pytest.mark.parametrize('token', [None, -1])
def test_required_child_version(client, action, token):
    ids = setup(client)
    url, data = request(client, ids, action)
    if token is None:
        data.pop('expected_row_version')
    else:
        data['expected_row_version'] = token
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 422
    assert snapshot() == before


@pytest.mark.parametrize('action', ACTIONS)
def test_late_parent_conflict_rolls_back_children_and_audit(client, monkeypatch, action):
    from app.routers import psur
    ids = setup(client)
    url, data = request(client, ids, action)
    original = psur.require_version
    winners = []
    def race(row, version):
        original(row, version)
        if type(row).__name__ == 'PSURPlan':
            change(ids, 'plan')
            winners.append(snapshot())
    monkeypatch.setattr(psur, 'require_version', race)
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == winners[0]


@pytest.mark.parametrize('action', CHILDREN)
def test_late_child_conflict_rolls_back_parent_and_audit(client, monkeypatch, action):
    from app.routers import psur
    ids = setup(client)
    url, data = request(client, ids, action)
    original = psur.require_version
    winners = []
    def race(row, version):
        original(row, version)
        if type(row).__name__ != 'PSURPlan':
            change(ids, action)
            winners.append(snapshot())
    monkeypatch.setattr(psur, 'require_version', race)
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == winners[0]


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('failure', ['audit', 'commit'])
def test_failure_after_flush_rolls_back_all_data(client, monkeypatch, action, failure):
    from app import crud
    from sqlalchemy.orm import Session
    ids = setup(client)
    url, data = request(client, ids, action)
    before = snapshot()
    def fail(db, *args, **kwargs):
        db.flush()
        raise RuntimeError('Synthetic transaction failure')
    if failure == 'commit':
        monkeypatch.setattr(Session, 'commit', fail)
    else:
        monkeypatch.setattr(crud, 'log_audit', fail)
        monkeypatch.setattr(crud, 'log_create_event', fail)
        monkeypatch.setattr(crud, 'log_delete_event', fail)
    with pytest.raises(RuntimeError, match='Synthetic transaction failure'):
        client.post(url, data=data, follow_redirects=False)
    assert snapshot() == before


@pytest.mark.parametrize('action', ACTIONS)
def test_repeat_form_cannot_add_second_data_or_audit(client, action):
    ids = setup(client)
    url, data = request(client, ids, action)
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code in (409, 303)
    assert snapshot() == before


@pytest.mark.parametrize('action', ACTIONS)
def test_deleted_parent_is_not_changed(client, action):
    ids = setup(client)
    url, data = request(client, ids, action)
    change(ids, 'plan', deleted=True)
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code in (404, 303)
    assert snapshot() == before


@pytest.mark.parametrize('action', CHILDREN)
@pytest.mark.parametrize('deleted', [False, True])
def test_foreign_or_deleted_child_is_not_changed(client, action, deleted):
    from app.database import SessionLocal
    from app.models import PSURCase, PSURPartnerRequest, PSURPlan, PSURSection
    ids = setup(client)
    url, data = request(client, ids, action)
    if deleted:
        change(ids, action, deleted=True)
    else:
        with SessionLocal() as db:
            model = {'case': PSURCase, 'request': PSURPartnerRequest,
                     'section': PSURSection, 'generate': PSURSection}[action]
            plan = db.get(PSURPlan, ids['plan'])
            other = PSURPlan(active_substance_id=plan.active_substance_id,
                             reporting_period_start=plan.reporting_period_start,
                             reporting_period_end=plan.reporting_period_end, data_lock_point=plan.data_lock_point)
            db.add(other)
            db.flush()
            db.get(model, ids['section' if action == 'generate' else action]).psur_plan_id = other.id
            db.commit()
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    assert snapshot() == before


def test_batch_failure_on_second_partner_rolls_back_first_request_task_and_audit(client, monkeypatch):
    from app import crud
    ids = setup(client)
    url, data = request(client, ids, 'requests')
    before = snapshot()
    original = crud.create_psur_partner_request
    calls = []
    def fail_second(db, *args, **kwargs):
        row = original(db, *args, **kwargs)
        calls.append(row.id)
        if len(calls) == 2:
            db.flush()
            raise RuntimeError('Synthetic second partner failure')
        return row
    monkeypatch.setattr(crud, 'create_psur_partner_request', fail_second)
    with pytest.raises(RuntimeError, match='Synthetic second partner failure'):
        client.post(url, data=data)
    assert len(calls) == 2
    assert snapshot() == before


def test_batch_success_commits_once_and_deduplicates_partner_ids(client, monkeypatch):
    from app.database import SessionLocal
    from app.models import AuditTrail, PSURPartnerRequest, PSURPlan, Task
    from sqlalchemy.orm import Session
    ids = setup(client)
    url, data = request(client, ids, 'requests')
    data['partner_ids'] += [ids['partners'][0]]
    original = Session.commit
    commits = []
    def commit(db):
        commits.append(True)
        return original(db)
    monkeypatch.setattr(Session, 'commit', commit)
    with SessionLocal() as db:
        audit_count = db.query(AuditTrail).count()
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    assert len(commits) == 1
    with SessionLocal() as db:
        assert db.query(PSURPartnerRequest).count() == 3
        assert db.query(Task).filter(Task.related_entity_id == ids['plan']).count() == 2
        assert db.get(PSURPlan, ids['plan']).version > int(data['expected_version'])
        assert db.query(AuditTrail).count() == audit_count + 4


def test_new_submission_invalidates_open_psur_form_and_default_case_type_works(client):
    from app.database import SessionLocal
    from app.models import Case, Partner
    ids = setup(client)
    url, data = request(client, ids, 'status')
    response = client.post('/api/submissions', json=dict(submission_object_type='pbrer',
        pbrer_id=ids['plan'], expected_object_version=int(data['expected_version'])))
    assert response.status_code == 201, response.text
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before
    with SessionLocal() as db:
        partner = db.get(Partner, ids['partners'][0])
        case = db.query(Case).first()
        case.numbering_partner_id = partner.id
        case.number_is_provisional = False
        db.commit()
        case_id, version = case.id, case.version
    response = client.post('/api/submissions', json=dict(case_id=case_id, expected_object_version=version))
    assert response.status_code == 201, response.text


def test_foreign_document_cannot_be_linked_to_partner_request(client):
    from app.database import SessionLocal
    from app.models import PSURDocument, PSURPlan
    ids = setup(client)
    url, data = request(client, ids, 'request')
    with SessionLocal() as db:
        plan = db.get(PSURPlan, ids['plan'])
        other = PSURPlan(active_substance_id=plan.active_substance_id,
                         reporting_period_start=plan.reporting_period_start,
                         reporting_period_end=plan.reporting_period_end, data_lock_point=plan.data_lock_point)
        db.add(other)
        db.flush()
        document = PSURDocument(psur_plan_id=other.id, file_name='Other.docx')
        db.add(document)
        db.commit()
        data['document_id'] = document.id
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 400
    assert snapshot() == before


def test_review_then_generation_resets_confirmation_and_preserves_versions(client):
    import json
    from app.database import SessionLocal
    from app.models import PSURPlan, PSURSection
    ids = setup(client)
    url, data = request(client, ids, 'section')
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        section = db.get(PSURSection, ids['section'])
        assert section.section_text == 'Updated section'
        assert section.human_confirmed and section.confirmed_by and section.confirmed_at
        version = section.version
    url, data = request(client, ids, 'generate')
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        section = db.get(PSURSection, ids['section'])
        assert section.version > version
        assert section.section_status == 'Draft' and section.gpt_generated
        assert not section.human_confirmed and section.confirmed_by is None and section.confirmed_at is None
        assert 'Human review is required' in section.section_text
        assert json.loads(section.gpt_output_json)['included_case_numbers'] == ['PSUR-CASE-0']
        assert db.get(PSURPlan, ids['plan']).version > int(data['expected_version'])


def test_find_links_only_new_cases_and_generation_ignores_deleted_links(client):
    import json
    from app.database import SessionLocal
    from app.models import PSURCase, PSURSection
    ids = setup(client)
    url, data = request(client, ids, 'find')
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.query(PSURCase).filter(PSURCase.psur_plan_id == ids['plan']).count() == 2
        db.get(PSURCase, ids['case']).is_deleted = True
        db.commit()
    url, data = request(client, ids, 'generate')
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        section = db.get(PSURSection, ids['section'])
        assert json.loads(section.gpt_output_json)['included_case_numbers'] == ['PSUR-CASE-1']
