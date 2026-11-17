from pathlib import Path

import pytest

from tests.test_concurrent_forms import Forms
from tests.test_concurrent_reconciliation_actions import snapshot
from tests.test_mvp_controls import create_saved_reconciliation

ACTIONS = ['outlook-draft', 'outlook-send']


def setup(client, monkeypatch, tmp_path, action):
    from app import outlook_service
    row_id = create_saved_reconciliation(tmp_path, partner_suffix='mail', contact_email='to@synthetic.test')
    monkeypatch.setattr(outlook_service, 'is_configured', lambda: True)
    monkeypatch.setattr(outlook_service, 'is_authorized', lambda user_key: True)
    calls = []
    def draft(**kwargs):
        calls.append(('draft', kwargs))
        return {'id': 'synthetic-message'}
    def attach(**kwargs):
        calls.append(('attach', kwargs))
    def send(**kwargs):
        calls.append(('send', kwargs))
    monkeypatch.setattr(outlook_service, 'create_outlook_draft', draft)
    monkeypatch.setattr(outlook_service, 'add_attachment_to_draft', attach)
    monkeypatch.setattr(outlook_service, 'send_outlook_message', send)
    if action == 'outlook-send':
        draft_url = f'/partner-reconciliation/{row_id}/outlook-draft'
        draft_form = Forms(client.get(f'/partner-reconciliation?reconciliation_id={row_id}').text).forms[draft_url]
        draft_form.update(email_subject='Subject', email_body='Body')
        assert post(client, draft_url, draft_form).status_code == 303
        calls.clear()
    page = f'/partner-reconciliation?reconciliation_id={row_id}'
    url = f'/partner-reconciliation/{row_id}/{action}'
    form = Forms(client.get(page).text).forms[url]
    form.update(email_subject='Subject', email_body='Body')
    return row_id, page, url, form, calls


def post(client, url, form):
    return client.post(url, data=form, follow_redirects=False)


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('change', ['parent', 'contact', 'file'])
def test_stale_inputs_before_network(client, monkeypatch, tmp_path, action, change):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, ContractContact
    row_id, page, url, form, calls = setup(client, monkeypatch, tmp_path, action)
    with SessionLocal() as db:
        row = db.get(PartnerReconciliation, row_id)
        if change == 'parent':
            row.comments = 'Changed'
        elif change == 'contact':
            db.get(ContractContact, row.contact_id).email = 'changed@synthetic.test'
        else:
            Path(row.document_path).write_bytes(b'Changed document')
        db.commit()
    before = snapshot()
    assert post(client, url, form).status_code == 409
    assert not calls and snapshot() == before


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('key,value', [('expected_version', None), ('expected_version', '0'),
                                    ('expected_mail_version', None), ('expected_mail_version', 'invalid')])
def test_required_tokens(client, monkeypatch, tmp_path, action, key, value):
    _, _, url, form, calls = setup(client, monkeypatch, tmp_path, action)
    if value is None:
        form.pop(key)
    else:
        form[key] = value
    before = snapshot()
    assert post(client, url, form).status_code == 422
    assert not calls and snapshot() == before


@pytest.mark.parametrize('action', ACTIONS)
def test_success_and_repeat_even_after_reload(client, monkeypatch, tmp_path, action):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation
    row_id, page, url, form, calls = setup(client, monkeypatch, tmp_path, action)
    assert post(client, url, form).status_code == 303
    before = snapshot()
    assert post(client, url, form).status_code == 409
    fresh = Forms(client.get(page).text).forms[url]
    fresh.update(email_subject='Subject', email_body='Body')
    response = post(client, url, fresh)
    assert response.status_code == (409 if action == 'outlook-draft' else 303)
    assert snapshot() == before
    assert len(calls) == (2 if action == 'outlook-draft' else 1)
    with SessionLocal() as db:
        assert db.get(PartnerReconciliation, row_id).outlook_status == ('draft_created' if action == 'outlook-draft' else 'sent')


@pytest.mark.parametrize('change', ['file', 'contact', 'subject', 'owner', 'legacy'])
def test_send_reloaded_page_still_requires_original_draft_inputs(client, monkeypatch, tmp_path, change):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, ContractContact
    from app import crud
    from app.routers import partner_reconciliation as routes
    if change == 'legacy':
        original = crud.log_field_changes
        def legacy_log(db, **kwargs):
            if kwargs.get('action') == 'outlook_draft_reserved':
                kwargs['action'] = 'legacy_draft'
            return original(db, **kwargs)
        monkeypatch.setattr(crud, 'log_field_changes', legacy_log)
    row_id, page, url, form, calls = setup(client, monkeypatch, tmp_path, 'outlook-send')
    with SessionLocal() as db:
        row = db.get(PartnerReconciliation, row_id)
        if change == 'file':
            Path(row.document_path).write_bytes(b'Changed bytes')
        elif change == 'contact':
            db.get(ContractContact, row.contact_id).email = 'changed@synthetic.test'
        elif change == 'subject':
            row.email_subject = 'Changed subject'
        db.commit()
    if change == 'owner':
        monkeypatch.setattr(routes, 'outlook_user_key', lambda request: 'other-synthetic-user')
    fresh = Forms(client.get(page).text).forms[url]
    before = snapshot()
    assert post(client, url, fresh).status_code == 409
    assert not calls and snapshot() == before


def test_partial_attachment_leaves_durable_claim(client, monkeypatch, tmp_path):
    from app import outlook_service
    from app.database import SessionLocal
    from app.models import PartnerReconciliation
    row_id, page, url, form, calls = setup(client, monkeypatch, tmp_path, 'outlook-draft')
    def fail(**kwargs):
        raise outlook_service.OutlookGraphError('Attachment failed after draft creation')
    monkeypatch.setattr(outlook_service, 'add_attachment_to_draft', fail)
    assert post(client, url, form).status_code == 409
    assert len(calls) == 1
    with SessionLocal() as db:
        assert db.get(PartnerReconciliation, row_id).outlook_status == 'draft_pending'
    fresh = Forms(client.get(page).text).forms[url]
    fresh.update(email_subject='Subject', email_body='Body')
    assert post(client, url, fresh).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize('action', ACTIONS)
def test_reservation_commit_failure_before_network(client, monkeypatch, tmp_path, action):
    from sqlalchemy.orm import Session
    _, _, url, form, calls = setup(client, monkeypatch, tmp_path, action)
    before = snapshot()
    def fail(self):
        raise RuntimeError('Claim failure')
    monkeypatch.setattr(Session, 'commit', fail)
    with pytest.raises(RuntimeError, match='Claim failure'):
        post(client, url, form)
    assert not calls and snapshot() == before


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('failure', ['network', 'completion', 'late_conflict'])
def test_uncertain_outcome_blocks_fresh_retry(client, monkeypatch, tmp_path, action, failure):
    from app import outlook_service
    from app.database import SessionLocal
    from app.models import PartnerReconciliation
    from sqlalchemy.orm import Session
    row_id, page, url, form, calls = setup(client, monkeypatch, tmp_path, action)
    if failure == 'completion':
        original = Session.commit
        count = []
        def commit(self):
            count.append(1)
            if len(count) == 2:
                raise RuntimeError('Completion failure')
            return original(self)
        monkeypatch.setattr(Session, 'commit', commit)
    else:
        name = 'create_outlook_draft' if action == 'outlook-draft' else 'send_outlook_message'
        original = getattr(outlook_service, name)
        def network(**kwargs):
            result = original(**kwargs)
            if failure == 'network':
                raise outlook_service.OutlookGraphError('Uncertain network outcome')
            with SessionLocal() as db:
                db.get(PartnerReconciliation, row_id).comments = 'Concurrent winner'
                db.commit()
            return result
        monkeypatch.setattr(outlook_service, name, network)
    assert post(client, url, form).status_code == 409
    with SessionLocal() as db:
        row = db.get(PartnerReconciliation, row_id)
        assert row.outlook_status == ('draft_pending' if action == 'outlook-draft' else 'send_pending')
        assert 'Outlook' in row.outlook_error
        if failure == 'late_conflict':
            assert row.comments == 'Concurrent winner'
    before, called = snapshot(), list(calls)
    fresh = Forms(client.get(page).text).forms[url]
    fresh.update(email_subject='Subject', email_body='Body')
    assert post(client, url, fresh).status_code == 409
    assert snapshot() == before and calls == called


@pytest.mark.parametrize('action', ACTIONS)
def test_overlapping_request_before_network_completion(client, monkeypatch, tmp_path, action):
    from app import outlook_service
    from app.main import app
    from fastapi.testclient import TestClient
    _, page, url, form, calls = setup(client, monkeypatch, tmp_path, action)
    name = 'create_outlook_draft' if action == 'outlook-draft' else 'send_outlook_message'
    original = getattr(outlook_service, name)
    def overlapping(**kwargs):
        # Independent request/session while the first external call is in flight.
        with TestClient(app) as other:
            other.cookies.update(client.cookies)
            fresh = Forms(other.get(page).text).forms[url]
            fresh.update(email_subject='Subject', email_body='Body')
            assert post(other, url, fresh).status_code == 409
        return original(**kwargs)
    monkeypatch.setattr(outlook_service, name, overlapping)
    assert post(client, url, form).status_code == 303
    assert len(calls) == (2 if action == 'outlook-draft' else 1)
