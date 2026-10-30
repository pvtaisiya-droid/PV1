from pathlib import Path
import pytest
from sqlalchemy import text
from tests.test_concurrent_forms import Forms
from tests.test_concurrent_reconciliation import target

ACTIONS = ['generate-document', 'response', 'task']


def setup(client, action):
    row_id, _, page, _, _ = target(client, 'status')
    url = f'/partner-reconciliation/{row_id}/{action}'
    form = Forms(client.get(page).text).forms[url]
    form['document_format'] = 'xlsx'
    return row_id, page, url, form


def snapshot():
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, PartnerReconciliationItem, Attachment, DocumentVersion, Task, AuditTrail
    with SessionLocal() as db:
        return {m.__tablename__: db.execute(text(f'SELECT * FROM "{m.__tablename__}" ORDER BY "{list(m.__table__.primary_key)[0].name}"')).all()
                for m in (PartnerReconciliation, PartnerReconciliationItem, Attachment, DocumentVersion, Task, AuditTrail)}


def files():
    from app.config import get_settings
    root = get_settings().storage_root
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def post(client, action, url, form):
    kwargs = {'files': {'response_file': ('answer.txt', b'Synthetic partner answer', 'text/plain')}} if action == 'response' else {}
    return client.post(url, data=form, follow_redirects=False, **kwargs)


@pytest.mark.parametrize('action', ACTIONS)
def test_stale_then_reload_and_replay(client, action):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, Attachment, Task, DocumentVersion
    row_id, page, url, form = setup(client, action)
    with SessionLocal() as db:
        db.get(PartnerReconciliation, row_id).comments = 'Winner'
        db.commit()
    before, disk = snapshot(), files()
    response = post(client, action, url, form)
    assert response.status_code == 409, response.text
    assert 'Обновите страницу' in response.json()['detail']
    assert snapshot() == before and files() == disk
    form.update(Forms(client.get(page).text).forms[url])
    response = post(client, action, url, form)
    assert response.status_code == 303, response.text
    after, disk = snapshot(), files()
    assert post(client, action, url, form).status_code == 409
    assert snapshot() == after and files() == disk
    with SessionLocal() as db:
        row = db.get(PartnerReconciliation, row_id)
        if action == 'generate-document':
            assert Path(row.document_path).exists()
            assert row.reconciliation_status == 'generated'
        elif action == 'response':
            attachment = db.get(Attachment, row.document_id)
            assert Path(attachment.storage_path).read_bytes() == b'Synthetic partner answer'
            assert db.query(DocumentVersion).filter_by(attachment_id=attachment.id).count() == 1
            assert row.reconciliation_status == 'confirmed' and row.response_date
        else:
            assert db.query(Task).filter_by(related_entity_id=row_id).count() == 1


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('value', [None, '0', 'invalid'])
def test_valid_token_required(client, action, value):
    _, _, url, form = setup(client, action)
    if value is None:
        form.pop('expected_version')
    else:
        form['expected_version'] = value
    before, disk = snapshot(), files()
    assert post(client, action, url, form).status_code == 422
    assert snapshot() == before and files() == disk


@pytest.mark.parametrize('action', ACTIONS)
def test_deleted_parent(client, action):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation
    row_id, _, url, form = setup(client, action)
    with SessionLocal() as db:
        db.get(PartnerReconciliation, row_id).is_deleted = True
        db.commit()
    before, disk = snapshot(), files()
    assert post(client, action, url, form).status_code == 404
    assert snapshot() == before and files() == disk


@pytest.mark.parametrize('action', ACTIONS)
def test_late_parent_conflict(client, monkeypatch, action):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation
    from app.routers import partner_reconciliation as routes
    row_id, _, url, form = setup(client, action)
    original = routes.require_version
    captured = []
    disk = files()
    def racing_check(row, version):
        original(row, version)
        with SessionLocal() as db:
            db.get(PartnerReconciliation, row_id).comments = 'Late winner'
            db.commit()
        captured.append(snapshot())
    monkeypatch.setattr(routes, 'require_version', racing_check)
    assert post(client, action, url, form).status_code == 409
    assert snapshot() == captured[0] and files() == disk


@pytest.mark.parametrize('action', ACTIONS)
def test_commit_failure_rolls_back_everything(client, monkeypatch, action):
    from sqlalchemy.orm import Session
    _, _, url, form = setup(client, action)
    before, disk = snapshot(), files()
    def fail_commit(self):
        raise RuntimeError('Synthetic commit failure')
    monkeypatch.setattr(Session, 'commit', fail_commit)
    with pytest.raises(RuntimeError, match='Synthetic commit failure'):
        post(client, action, url, form)
    assert snapshot() == before and files() == disk


@pytest.mark.parametrize('action', ['generate-document', 'response'])
def test_partial_write_cleanup_keeps_previous_files(client, monkeypatch, action):
    from app.config import get_settings
    from app.routers import partner_reconciliation as routes
    _, _, url, form = setup(client, action)
    root = get_settings().storage_root
    root.mkdir(parents=True, exist_ok=True)
    (root / 'previous.txt').write_bytes(b'Previous document')
    before, disk = snapshot(), files()
    def partial(*args, created_paths, **kwargs):
        path = root / 'partial.txt'
        created_paths.append(path)
        path.write_bytes(b'Partial')
        raise OSError('Synthetic write failure')
    monkeypatch.setattr(routes, 'generate_reconciliation_document_file' if action == 'generate-document' else 'store_document_file', partial)
    with pytest.raises(OSError, match='Synthetic write failure'):
        post(client, action, url, form)
    assert snapshot() == before and files() == disk


def test_regeneration_keeps_old_document_bytes(client):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation
    row_id, page, url, form = setup(client, 'generate-document')
    assert post(client, 'generate-document', url, form).status_code == 303
    with SessionLocal() as db:
        old_path = Path(db.get(PartnerReconciliation, row_id).document_path)
    old_bytes = old_path.read_bytes()
    form.update(Forms(client.get(page).text).forms[url])
    form['document_format'] = 'pdf'
    assert post(client, 'generate-document', url, form).status_code == 303
    with SessionLocal() as db:
        new_path = Path(db.get(PartnerReconciliation, row_id).document_path)
    assert old_path != new_path and old_path.read_bytes() == old_bytes
    assert new_path.exists() and new_path.with_suffix('.docx').exists()


@pytest.mark.parametrize('action', ACTIONS)
def test_audit_failure_rolls_back_everything(client, monkeypatch, action):
    from app import crud
    _, _, url, form = setup(client, action)
    before, disk = snapshot(), files()
    def fail_audit(*args, **kwargs):
        raise RuntimeError('Synthetic audit failure')
    monkeypatch.setattr(crud, 'log_create_event' if action == 'task' else 'log_field_changes', fail_audit)
    with pytest.raises(RuntimeError, match='Synthetic audit failure'):
        post(client, action, url, form)
    assert snapshot() == before and files() == disk


def test_upload_read_error_removes_partial_file(client, monkeypatch):
    from app.routers import partner_reconciliation as routes
    _, _, url, form = setup(client, 'response')
    before, disk = snapshot(), files()
    original = routes.store_document_file
    class BrokenStream:
        def __init__(self):
            self.calls = 0
        def read(self, size):
            self.calls += 1
            if self.calls == 1:
                return b'Partial upload'
            raise OSError('Synthetic read failure')
    def broken_upload(upload, **kwargs):
        from starlette.datastructures import UploadFile
        return original(UploadFile(BrokenStream(), filename=upload.filename), **kwargs)
    monkeypatch.setattr(routes, 'store_document_file', broken_upload)
    with pytest.raises(OSError, match='Synthetic read failure'):
        post(client, 'response', url, form)
    assert snapshot() == before and files() == disk
