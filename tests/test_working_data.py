from pathlib import Path
import sqlite3
import json

import pytest

from tools.working_data import compare, prepare, relocated, relocated_field


def test_transfer_compares_all_old_fields_and_only_allows_storage_relocation(tmp_path):
    source_root = tmp_path / 'source'
    source_root.mkdir()
    storage = source_root / 'uploads'
    target_storage = tmp_path / 'target' / 'storage'
    source, target = source_root / 'db.sqlite3', tmp_path / 'target.sqlite3'
    for path in (source, target):
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE records(id TEXT, storage_path TEXT, history TEXT, number TEXT, password_hash BLOB)')
            db.execute('CREATE TABLE alembic_version(version_num TEXT)')
            db.execute('INSERT INTO alembic_version VALUES (?)', (path.name,))
            db.execute('INSERT INTO records VALUES (?,?,?,?,?)', ('1', 'uploads/a.pdf', 'past', 'MSG-01', b'preserved'))
    with sqlite3.connect(target) as db:
        db.execute('UPDATE records SET storage_path=?', (str(target_storage / 'a.pdf'),))
        db.execute('ALTER TABLE records ADD COLUMN new_field TEXT')
    assert compare(source, target, source_root, storage, target_storage)['records']['preserved']
    with sqlite3.connect(target) as db:
        db.execute("UPDATE records SET history='lost'")
    with pytest.raises(ValueError, match='Old fields changed: records'):
        compare(source, target, source_root, storage, target_storage)


def test_transfer_keeps_preexisting_external_reference_and_maps_relative_and_absolute(tmp_path):
    root = tmp_path / 'source'
    storage = root / 'uploads'
    target = tmp_path / 'target' / 'storage'
    assert relocated('uploads/a.pdf', root, storage, target) == str(target / 'a.pdf')
    assert relocated(str(storage / 'a.pdf'), root, storage, target) == str(target / 'a.pdf')
    assert relocated('access-migration/old.pdf', root, storage, target) == 'access-migration/old.pdf'
    assert relocated(None, root, storage, target) is None


def test_transfer_refuses_existing_destination_without_creating_more_copies(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    source = root / 'existing.db'
    source.write_bytes(b'original')
    storage = root / 'uploads'
    storage.mkdir()
    destination = tmp_path / 'target'
    destination.mkdir()
    with pytest.raises(ValueError, match='Destination already exists'):
        prepare(root, source, storage, destination, tmp_path / 'reports', offline=True)
    assert source.read_bytes() == b'original'
    assert not (tmp_path / 'reports').exists()


def test_transfer_relocates_manifest_disk_paths_and_keeps_source_evidence(tmp_path):
    root = tmp_path / 'source'
    storage, target = root / 'uploads', tmp_path / 'target' / 'storage'
    raw = json.dumps([dict(stored_path=str(storage / 'a.xml'),
                           original_path='archive.zip!/a.xml', checksum='preserved',
                           evidence=str(storage / 'a.xml'))])
    value = relocated_field('tblE2BImportBatches', 'file_manifest_json', raw, root, storage, target)
    actual, original = json.loads(value), json.loads(raw)
    assert actual[0].pop('stored_path') == str(target / 'a.xml')
    original[0].pop('stored_path')
    assert actual == original
    assert relocated_field('tblAuditTrail', 'new_value', raw, root, storage, target) == raw
