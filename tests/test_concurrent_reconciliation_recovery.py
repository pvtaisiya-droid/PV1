"""Manual external verification must be explicit, audited and atomic."""
import json
from datetime import datetime

import pytest

from tests.test_concurrent_forms import Forms
from tests.test_concurrent_reconciliation_actions import snapshot
from tests.test_concurrent_reconciliation_mail import setup as mail_setup, post


def setup(client, monkeypatch, tmp_path, state='draft_pending'):
    from app import outlook_service
    from app.database import SessionLocal
    from app.models import PartnerReconciliation
    action = 'outlook-send' if state in {'send_pending', 'draft_created'} else 'outlook-draft'
    row_id, page, url, form, calls = mail_setup(client, monkeypatch, tmp_path, action)
    if state != 'draft_created':
        name = 'send_outlook_message' if action == 'outlook-send' else 'create_outlook_draft'
        def uncertain(**kwargs):
            raise outlook_service.OutlookGraphError('Synthetic uncertain result')
        monkeypatch.setattr(outlook_service, name, uncertain)
        assert post(client, url, form).status_code == 409
    recovery_url = f'/partner-reconciliation/{row_id}/outlook-recover'
    recovery = Forms(client.get(page).text).forms[recovery_url]
    recovery.update(outcome='cancelled', mailbox='owner@synthetic.test',
                    evidence='Checked mailbox after all workers stopped; no sending and drafts removed.',
                    operations_stopped='yes', result_verified='yes')
    with SessionLocal() as db:
        assert db.get(PartnerReconciliation, row_id).outlook_status == state
    calls.clear()
    return row_id, page, recovery_url, recovery, calls


@pytest.mark.parametrize('state', ['draft_pending', 'send_pending', 'draft_created'])
@pytest.mark.parametrize('outcome', ['sent', 'cancelled'])
def test_recovery_result_audit_and_repeat(client, monkeypatch, tmp_path, state, outcome):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, AuditTrail
    from app import outlook_service
    row_id, page, url, form, calls = setup(client, monkeypatch, tmp_path, state)
    form['outcome'] = outcome
    if outcome == 'sent':
        form.update(verified_message_id='verified-sent-id', verified_sent_at='2026-01-02T01:00:00+03:00')
    with SessionLocal() as db:
        previous = [(a.id, a.action, a.comment) for a in db.query(AuditTrail).all()]
    assert post(client, url, form).status_code == 303
    with SessionLocal() as db:
        row = db.get(PartnerReconciliation, row_id)
        assert row.version == int(form['expected_version']) + 1
        assert row.outlook_status == ('sent' if outcome == 'sent' else 'not_created')
        assert row.reconciliation_status == ('sent' if outcome == 'sent' else 'generated')
        assert row.outlook_error is None
        assert row.outlook_message_id == ('verified-sent-id' if outcome == 'sent' else None)
        if outcome == 'sent':
            assert row.sent_at == datetime(2026, 1, 1, 22)
            assert row.sent_date == row.sent_at.date()
        else:
            assert row.sent_at is None and row.sent_date is None and row.draft_created_at is None
        for audit_id, action, comment in previous:
            audit = db.get(AuditTrail, audit_id)
            assert (audit.action, audit.comment) == (action, comment)
        audits = db.query(AuditTrail).filter_by(entity_id=row_id, action='outlook_recovered').all()
        assert audits and all(a.user_id == row.updated_by for a in audits)
        assert json.loads(audits[0].comment)['evidence'] == form['evidence']
    before = snapshot()
    assert post(client, url, form).status_code == 409
    assert not calls and snapshot() == before
    assert url not in Forms(client.get(page).text).forms
    # Sending already confirmed must never call Graph again. Cancellation allows
    # a fresh draft only through the normal pre-network reservation.
    draft_url = f'/partner-reconciliation/{row_id}/outlook-draft'
    fresh = Forms(client.get(page).text).forms[draft_url]
    fresh.update(email_subject='Subject', email_body='Body')
    monkeypatch.setattr(outlook_service, 'create_outlook_draft', lambda **kw: calls.append('draft') or {'id': 'new-draft'})
    response = post(client, draft_url, fresh)
    assert response.status_code == (409 if outcome == 'sent' else 303)
    assert bool(calls) == (outcome == 'cancelled')


@pytest.mark.parametrize('key,value,status', [
    ('expected_version', None, 422), ('expected_version', '0', 422),
    ('csrf', None, 403), ('csrf', 'wrong', 403),
    ('operations_stopped', None, 422), ('operations_stopped', 'no', 422),
    ('result_verified', None, 422), ('result_verified', 'no', 422),
    ('evidence', ' ' * 20, 422), ('mailbox', '   ', 422),
    ('outcome', 'unknown', 422), ('outcome', 'sent', 422),
    ('verified_message_id', 'not-absent', 422),
])
def test_invalid_recovery_preserves_pending(client, monkeypatch, tmp_path, key, value, status):
    _, _, url, form, calls = setup(client, monkeypatch, tmp_path)
    if value is None:
        form.pop(key)
    else:
        form[key] = value
    before = snapshot()
    assert post(client, url, form).status_code == status
    assert snapshot() == before and not calls


@pytest.mark.parametrize('timestamp', ['2026-01-02T01:00:00', '2999-01-02T01:00:00+03:00'])
def test_sent_requires_valid_external_timestamp(client, monkeypatch, tmp_path, timestamp):
    _, _, url, form, _ = setup(client, monkeypatch, tmp_path)
    form.update(outcome='sent', verified_message_id='verified', verified_sent_at=timestamp)
    before = snapshot()
    assert post(client, url, form).status_code == 422
    assert snapshot() == before


@pytest.mark.parametrize('change', ['version', 'deleted', 'closed', 'ordinary'])
def test_stale_or_ineligible_recovery(client, monkeypatch, tmp_path, change):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation
    row_id, _, url, form, _ = setup(client, monkeypatch, tmp_path)
    with SessionLocal() as db:
        row = db.get(PartnerReconciliation, row_id)
        if change == 'version':
            row.comments = 'Winner'
        elif change == 'deleted':
            row.is_deleted = True
        elif change == 'closed':
            row.reconciliation_status = 'closed'
        else:
            row.outlook_status = 'not_created'
        db.commit()
        if change in {'closed', 'ordinary'}:
            form['expected_version'] = str(row.version)
    before = snapshot()
    assert post(client, url, form).status_code == (404 if change == 'deleted' else 409)
    assert snapshot() == before


@pytest.mark.parametrize('failure', ['audit', 'commit'])
def test_recovery_failure_rolls_back_everything(client, monkeypatch, tmp_path, failure):
    from app import crud
    from sqlalchemy.orm import Session
    _, _, url, form, _ = setup(client, monkeypatch, tmp_path)
    before = snapshot()
    def fail(*args, **kwargs):
        if failure == 'audit':
            args[0].flush()  # exercise rollback after writing the parent
        raise RuntimeError('Synthetic recovery failure')
    monkeypatch.setattr(crud if failure == 'audit' else Session,
                        'log_field_changes' if failure == 'audit' else 'commit', fail)
    with pytest.raises(RuntimeError, match='Synthetic recovery failure'):
        post(client, url, form)
    assert snapshot() == before


def test_business_permissions_cannot_recover(client, monkeypatch, tmp_path):
    from tests.test_access_control_matrix import set_permissions
    _, page, url, form, _ = setup(client, monkeypatch, tmp_path)
    set_permissions(['view', 'edit', 'approve', 'export', 'manage_users'])
    before = snapshot()
    assert post(client, url, form).status_code == 403
    assert url not in Forms(client.get(page).text).forms
    assert snapshot() == before


def test_legacy_draft_can_be_cancelled_without_editing_old_audit(client, monkeypatch, tmp_path):
    from app import crud
    original = crud.log_field_changes
    def legacy_log(db, **kwargs):
        if kwargs.get('action') == 'outlook_draft_reserved':
            kwargs['action'] = 'legacy_draft'
        return original(db, **kwargs)
    monkeypatch.setattr(crud, 'log_field_changes', legacy_log)
    _, _, url, form, calls = setup(client, monkeypatch, tmp_path, 'draft_created')
    assert post(client, url, form).status_code == 303
    assert not calls


def test_late_completion_cannot_overwrite_recovery(client, monkeypatch, tmp_path):
    from app import crud
    from app.database import SessionLocal
    from app.models import PartnerReconciliation
    from sqlalchemy.orm.exc import StaleDataError
    row_id, _, url, form, _ = setup(client, monkeypatch, tmp_path, 'send_pending')
    with SessionLocal() as delayed:
        stale = delayed.get(PartnerReconciliation, row_id)
        assert post(client, url, form).status_code == 303
        before = snapshot()
        with pytest.raises(StaleDataError):
            crud.mark_partner_reconciliation_outlook_sent(delayed, stale, changed_by_user_id=stale.updated_by)
        delayed.rollback()
    assert snapshot() == before
