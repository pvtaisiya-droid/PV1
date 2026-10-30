from pathlib import Path

import pytest

from tests.test_concurrent_e2b import form, send, setup, snapshot
from tests.test_e2b_import import (e2b_r3_xml_with_attachment, latest_batch,
                                   e2b_xml, make_zip, seed_product, upload_package, upload_xml)


@pytest.mark.parametrize('failure', ('audit', 'commit', 'write'))
def test_embedded_file_rollback_preserves_previous_bytes(client, monkeypatch, failure):
    from app import e2b_import
    from sqlalchemy.orm import Session
    seed_product(name='PACLITAXEL', code='PACLITAXEL-001')
    xml, document = e2b_r3_xml_with_attachment()
    assert upload_xml(client, xml).status_code == 303
    batch = latest_batch()
    legacy = Path(batch.storage_dir) / 'embedded' / 'previous.pdf'
    legacy.parent.mkdir()
    legacy.write_bytes(b'Previous immutable bytes')
    url, data = form(client, batch.id, 'confirm')
    before = snapshot()
    written = []
    original_write = Path.write_bytes
    def write(path, content):
        result = original_write(path, content)
        if 'embedded-' in str(path):
            written.append(content)
            if failure == 'write':
                raise OSError('Synthetic partial write')
        return result
    monkeypatch.setattr(Path, 'write_bytes', write)
    def fail(db, *args, **kwargs):
        db.flush()
        assert document in written
        raise RuntimeError('Synthetic transaction failure')
    if failure == 'audit':
        monkeypatch.setattr(e2b_import, 'log_audit', fail)
    elif failure == 'commit':
        monkeypatch.setattr(Session, 'commit', fail)
    response = client.post(url, data=data, follow_redirects=False)
    assert response.status_code == 303 and 'error=' in response.headers['location']
    assert document in written
    assert snapshot() == before
    assert not list(Path(batch.storage_dir).glob('embedded-*'))


@pytest.mark.parametrize('failure', ('read', 'write', 'source_missing'))
def test_recheck_io_failure_preserves_previous_children_and_files(client, monkeypatch, failure):
    batch_id = setup(client)
    batch = latest_batch()
    url, data = form(client, batch_id, 'recheck')
    if failure == 'source_missing':
        Path(batch.source_zip_path).unlink()
    before = snapshot()
    original_write = Path.write_bytes
    original_read = Path.read_bytes
    def write(path, content):
        result = original_write(path, content)
        if 'files-' in str(path):
            raise OSError('Synthetic partial write')
        return result
    def read(path):
        if 'files-' in str(path):
            raise OSError('Synthetic read failure')
        return original_read(path)
    if failure == 'write':
        monkeypatch.setattr(Path, 'write_bytes', write)
    elif failure == 'read':
        monkeypatch.setattr(Path, 'read_bytes', read)
    if failure == 'source_missing':
        response = client.post(url, data=data, follow_redirects=False)
        assert response.status_code == 303 and 'validation=' in response.headers['location']
    else:
        with pytest.raises(OSError, match='Synthetic'):
            client.post(url, data=data, follow_redirects=False)
    assert snapshot() == before
    assert not list(Path(batch.storage_dir).glob('files-*'))


@pytest.mark.parametrize('final', ('confirm', 'mark-not-pv-ars'))
@pytest.mark.parametrize('action', ('recheck', 'cancel'))
def test_finalized_batch_keeps_files_and_links(client, final, action):
    batch_id = setup(client, final)
    assert send(client, batch_id, final).status_code == 303
    before = snapshot()
    response = send(client, batch_id, action)
    assert response.status_code == 303 and 'validation=' in response.headers['location']
    assert snapshot() == before


@pytest.mark.parametrize('action', ('confirm', 'mark-not-pv-ars'))
def test_cancelled_batch_cannot_be_imported_without_recheck(client, action):
    from app.database import SessionLocal
    from app.models import E2BImportBatch
    batch_id = setup(client, action)
    url, data = form(client, batch_id, action)
    assert send(client, batch_id, 'cancel').status_code == 303
    with SessionLocal() as db:
        data['expected_version'] = db.get(E2BImportBatch, batch_id).version
    before = snapshot()
    response = client.post(url, data=data, follow_redirects=False)
    assert response.status_code == 303 and 'validation=' in response.headers['location']
    assert snapshot() == before


def test_recheck_refuses_path_outside_storage(client, tmp_path):
    from app.database import SessionLocal
    from app.models import E2BImportBatch
    batch_id = setup(client)
    foreign = tmp_path / 'foreign-files'
    foreign.mkdir()
    sentinel = foreign / 'sentinel.bin'
    sentinel.write_bytes(b'Keep foreign file')
    with SessionLocal() as db:
        db.get(E2BImportBatch, batch_id).storage_dir = str(foreign)
        db.commit()
    before = snapshot()
    response = send(client, batch_id, 'recheck')
    assert response.status_code == 303 and 'validation=' in response.headers['location']
    assert sentinel.read_bytes() == b'Keep foreign file'
    assert list(foreign.iterdir()) == [sentinel]
    assert snapshot() == before


def test_recheck_duplicate_children_and_issues(client):
    from app.database import SessionLocal
    from app.models import E2BImportBatch, AuditTrail
    seed_product()
    xml = e2b_xml()
    package = make_zip({'message.xml': xml, 'duplicate.xml': xml,
                        'one.pdf': b'%PDF same bytes', 'two.pdf': b'%PDF same bytes'})
    assert upload_package(client, package).status_code == 303
    batch_id = latest_batch().id
    before_files = snapshot()[1]
    assert send(client, batch_id, 'recheck').status_code == 303
    with SessionLocal() as db:
        batch = db.get(E2BImportBatch, batch_id)
        assert len(batch.items) == 4
        assert sum(i.status == 'duplicate' for i in batch.items) == 2
        assert batch.status == 'requires_review'
        assert db.query(AuditTrail).filter_by(entity_id=batch_id, action='recheck').count() == 1
        for item in batch.items:
            assert Path(item.stored_path).is_file()
            if item.duplicate_of_item:
                assert item.stored_path == item.duplicate_of_item.stored_path
    assert all(snapshot()[1][path] == data for path, data in before_files.items())


def test_recheck_malformed_xml_replaces_diagnostics(client):
    from app.database import SessionLocal
    from app.models import E2BImportBatch, AuditTrail
    assert upload_xml(client, b'<broken xml').status_code == 303
    batch_id = latest_batch().id
    with SessionLocal() as db:
        batch = db.get(E2BImportBatch, batch_id)
        old_issues = {i.id for i in batch.issues}
        assert batch.error_count > 0
    assert send(client, batch_id, 'recheck').status_code == 303
    with SessionLocal() as db:
        batch = db.get(E2BImportBatch, batch_id)
        assert batch.status == 'error' and batch.error_count > 0
        assert batch.issues and not old_issues.intersection(i.id for i in batch.issues)
        assert any(i.issue_type == 'xml_parse_error' for i in batch.issues)
        assert db.query(AuditTrail).filter_by(entity_id=batch_id, action='recheck').count() == 1


def test_late_embedded_import_conflict_preserves_previous_files(client, monkeypatch):
    from app.database import SessionLocal
    from app.models import E2BImportBatch
    from app.routers import safety_reports
    seed_product(name='PACLITAXEL', code='PACLITAXEL-001')
    xml, document = e2b_r3_xml_with_attachment()
    assert upload_xml(client, xml).status_code == 303
    batch_id = latest_batch().id
    url, data = form(client, batch_id, 'confirm')
    original = safety_reports.require_version
    winners = []
    writes = []
    original_write = Path.write_bytes
    def write(path, content):
        writes.append(content)
        return original_write(path, content)
    def race(row, version):
        original(row, version)
        with SessionLocal() as db:
            db.get(E2BImportBatch, batch_id).source_filename = 'Winner.xml'
            db.commit()
        winners.append(snapshot())
    monkeypatch.setattr(Path, 'write_bytes', write)
    monkeypatch.setattr(safety_reports, 'require_version', race)
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert document in writes
    assert snapshot() == winners[0]
