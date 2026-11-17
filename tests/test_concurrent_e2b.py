from pathlib import Path

import pytest
from sqlalchemy import text

from tests.test_concurrent_forms import Forms
from tests.test_e2b_import import e2b_xml, latest_batch, make_zip, seed_product, upload_package


ACTIONS = ('recheck', 'confirm', 'mark-not-pv-ars', 'cancel')


def setup(client, action='confirm'):
    seed_product(company=action != 'mark-not-pv-ars')
    package = make_zip({'case.xml': e2b_xml(), 'CIOMS.pdf': b'%PDF-1.4 synthetic'})
    assert upload_package(client, package).status_code == 303
    return latest_batch().id


def form(client, batch_id, action):
    url = f'/safety-reports/e2b-imports/{batch_id}'
    return url + '/' + action, Forms(client.get(url).text).forms[url + '/' + action]


def send(client, batch_id, action):
    url, data = form(client, batch_id, action)
    return client.post(url, data=data, follow_redirects=False)


def snapshot():
    from app.database import SessionLocal
    from app.models import (E2BImportBatch, E2BImportItem, E2BImportIssue, E2BTransmission,
                            Case, CaseNumberCounter, Patient, Reaction, CaseProduct,
                            CaseNarrative, CaseDrugSubstance, CaseLiteratureReference,
                            SafetyReport, SafetyReportProduct, IncomingRequest,
                            IncomingRequestProduct, IncomingRequestCaseLink, Attachment, AuditTrail)
    models = (E2BImportBatch, E2BImportItem, E2BImportIssue, E2BTransmission,
              Case, CaseNumberCounter, Patient, Reaction, CaseProduct,
              CaseNarrative, CaseDrugSubstance, CaseLiteratureReference, SafetyReport,
              SafetyReportProduct, IncomingRequest, IncomingRequestProduct,
              IncomingRequestCaseLink, Attachment, AuditTrail)
    with SessionLocal() as db:
        rows = {m.__tablename__: db.execute(text(f'SELECT * FROM "{m.__tablename__}" ORDER BY id')).all()
                for m in models}
    from app.e2b_import import UPLOAD_ROOT
    files = {p.relative_to(UPLOAD_ROOT).as_posix(): p.read_bytes()
             for p in UPLOAD_ROOT.rglob('*') if p.is_file()}
    return rows, files


@pytest.mark.parametrize('action', ACTIONS)
def test_stale_preview_then_reload(client, action):
    from app.database import SessionLocal
    from app.models import E2BImportBatch
    batch_id = setup(client, action)
    url, data = form(client, batch_id, action)
    with SessionLocal() as db:
        db.get(E2BImportBatch, batch_id).source_filename = 'Winner.zip'
        db.commit()
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before
    assert send(client, batch_id, action).status_code == 303
    with SessionLocal() as db:
        expected = {'confirm': 'imported', 'recheck': 'requires_review',
                    'mark-not-pv-ars': 'not_pv_ars', 'cancel': 'cancelled'}[action]
        assert db.get(E2BImportBatch, batch_id).status == expected


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('token', [None, 0, -1])
def test_required_positive_version(client, action, token):
    batch_id = setup(client, action)
    url, data = form(client, batch_id, action)
    if token is None:
        data.pop('expected_version')
    else:
        data['expected_version'] = token
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 422
    assert snapshot() == before


@pytest.mark.parametrize('action', ACTIONS)
def test_late_batch_conflict(client, monkeypatch, action):
    from app.database import SessionLocal
    from app.models import E2BImportBatch
    from app.routers import safety_reports
    batch_id = setup(client, action)
    url, data = form(client, batch_id, action)
    original = safety_reports.require_version
    winners = []
    def race(row, version):
        original(row, version)
        with SessionLocal() as db:
            db.get(E2BImportBatch, batch_id).source_filename = 'Winner.zip'
            db.commit()
        winners.append(snapshot())
    monkeypatch.setattr(safety_reports, 'require_version', race)
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == winners[0]


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('failure', ['audit', 'commit'])
def test_failure_after_flush_preserves_data_and_files(client, monkeypatch, action, failure):
    from app import e2b_import
    from app.routers import safety_reports
    from sqlalchemy.orm import Session
    batch_id = setup(client, action)
    url, data = form(client, batch_id, action)
    before = snapshot()
    def fail(db, *args, **kwargs):
        db.flush()
        raise RuntimeError('Synthetic transaction failure')
    if failure == 'audit':
        monkeypatch.setattr(e2b_import, 'log_audit', fail)
        monkeypatch.setattr(safety_reports, 'log_audit', fail)
    else:
        monkeypatch.setattr(Session, 'commit', fail)
    if action in ('confirm', 'mark-not-pv-ars'):
        response = client.post(url, data=data, follow_redirects=False)
        assert response.status_code == 303 and 'error=' in response.headers['location']
    else:
        with pytest.raises(RuntimeError, match='Synthetic transaction failure'):
            client.post(url, data=data, follow_redirects=False)
    assert snapshot() == before


@pytest.mark.parametrize('action', ACTIONS)
def test_deleted_batch_returns_404(client, action):
    from app.database import SessionLocal
    from app.models import E2BImportBatch
    batch_id = setup(client, action)
    url, data = form(client, batch_id, action)
    with SessionLocal() as db:
        db.get(E2BImportBatch, batch_id).is_deleted = True
        db.commit()
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 404
    assert snapshot() == before


@pytest.mark.parametrize('action', ('confirm', 'mark-not-pv-ars', 'cancel'))
def test_repeat_with_old_then_current_version(client, action):
    batch_id = setup(client, action)
    url, data = form(client, batch_id, action)
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before
    # Finalized mark form is hidden, but a fresh version can be submitted safely.
    from app.database import SessionLocal
    from app.models import E2BImportBatch
    with SessionLocal() as db:
        data['expected_version'] = db.get(E2BImportBatch, batch_id).version
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    assert snapshot() == before


def test_recheck_preserves_original_files_and_replaces_children(client):
    from app.database import SessionLocal
    from app.models import E2BImportBatch, E2BImportItem
    batch_id = setup(client)
    with SessionLocal() as db:
        old_ids = {i.id for i in db.get(E2BImportBatch, batch_id).items}
    before_files = snapshot()[1]
    assert send(client, batch_id, 'recheck').status_code == 303
    after_files = snapshot()[1]
    assert all(after_files[path] == data for path, data in before_files.items())
    assert len(after_files) > len(before_files)
    with SessionLocal() as db:
        batch = db.get(E2BImportBatch, batch_id)
        assert batch.items and not old_ids.intersection(i.id for i in batch.items)
        assert not db.query(E2BImportItem).filter(E2BImportItem.id.in_(old_ids)).count()
        assert all(Path(i.stored_path).is_file() for i in batch.items)
    assert send(client, batch_id, 'confirm').status_code == 303


def existing_case(client):
    from app.database import SessionLocal
    from app.models import Case, E2BImportBatch
    first = setup(client)
    assert send(client, first, 'confirm').status_code == 303
    with SessionLocal() as db:
        case_id = db.get(E2BImportBatch, first).created_case_id
    package = make_zip({'followup.xml': e2b_xml(message_id='FOLLOWUP', version='2')})
    assert upload_package(client, package).status_code == 303
    return latest_batch().id, case_id


@pytest.mark.parametrize('change', ('edit', 'close', 'delete'))
def test_stale_related_case(client, change):
    from app.database import SessionLocal
    from app.models import Case
    batch_id, case_id = existing_case(client)
    url, data = form(client, batch_id, 'confirm')
    assert data['expected_case_token'].startswith(case_id + ':')
    with SessionLocal() as db:
        case = db.get(Case, case_id)
        if change == 'close':
            case.is_locked = True
            case.workflow_status = 'closed'
        elif change == 'delete':
            case.is_deleted = True
        else:
            case.narrative = 'Winner'
        db.commit()
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before
    if change == 'close':
        response = send(client, batch_id, 'confirm')
        assert response.status_code == 303 and 'validation=' in response.headers['location']
        assert snapshot() == before
    elif change == 'edit':
        assert send(client, batch_id, 'confirm').headers['location'].startswith('/cases/')


def test_case_appearing_after_preview_is_conflict(client):
    from app.database import SessionLocal
    from app.models import Case
    batch_id = setup(client)
    url, data = form(client, batch_id, 'confirm')
    assert data['expected_case_token'] == 'none'
    with SessionLocal() as db:
        case = Case(case_number='Synthetic-existing', worldwide_case_id='WW-CASE-001',
                    workflow_status='data_entry')
        db.add(case)
        db.commit()
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before


def test_late_case_closure_rolls_back_import(client, monkeypatch):
    from app import e2b_import
    from app.database import SessionLocal
    from app.models import Case
    batch_id, case_id = existing_case(client)
    url, data = form(client, batch_id, 'confirm')
    original = e2b_import.assert_case_editable
    winners = []
    def race(case):
        original(case)
        with SessionLocal() as db:
            row = db.get(Case, case_id)
            row.is_locked = True
            row.workflow_status = 'closed'
            db.commit()
        winners.append(snapshot())
    monkeypatch.setattr(e2b_import, 'assert_case_editable', race)
    # HTTP snapshot writes now reserve SQLite before reading their inputs.
    # Exercise the service's independent ORM defence without that reservation.
    from app.models import E2BImportBatch
    from sqlalchemy.orm.exc import StaleDataError
    with SessionLocal() as db:
        batch = db.get(E2BImportBatch, batch_id)
        with pytest.raises(StaleDataError):
            e2b_import.E2BImportService().confirm_batch(db, batch,
                confirmed_by_user_id=None, expected_case_token=data['expected_case_token'])
        db.rollback()
    assert snapshot() == winners[0]


def test_confirm_requires_case_token(client):
    batch_id = setup(client)
    url, data = form(client, batch_id, 'confirm')
    data.pop('expected_case_token')
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 422
    assert snapshot() == before
