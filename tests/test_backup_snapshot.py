from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess

import pytest

from tools import backup_snapshot as backup


@pytest.fixture()
def installation(tmp_path):
    root = tmp_path / "installation"
    root.mkdir()
    storage = root / "uploads"
    storage.mkdir()
    (storage / "document.bin").write_bytes(b"synthetic document")
    (storage / "empty-batch").mkdir()
    database = root / "working.db"
    with closing(sqlite3.connect(database)) as db:
        db.executescript('''
            CREATE TABLE parent (id INTEGER PRIMARY KEY);
            CREATE TABLE tblAttachments (id INTEGER PRIMARY KEY, storage_path TEXT,
                parent_id INTEGER REFERENCES parent(id));
            CREATE TABLE tblE2BImportBatches (id INTEGER PRIMARY KEY, storage_dir TEXT);
            INSERT INTO parent VALUES (1);
        ''')
        db.execute("INSERT INTO tblAttachments VALUES (1, ?, 1)", (str(storage / "document.bin"),))
        db.execute("INSERT INTO tblAttachments VALUES (2, 'uploads/document.bin', 1)")
        db.execute("INSERT INTO tblE2BImportBatches VALUES (1, 'uploads/empty-batch')")
        db.commit()
    (root / "app").mkdir()
    (root / "app/main.py").write_text("# synthetic source\n")
    (root / "requirements.lock.txt").write_text("# synthetic lock\n")
    (root / "runtime.log").write_text("do not package")
    (root / ".env").write_text("SECRET=do-not-package")
    (root / "migration_access").mkdir()
    (root / "migration_access/export.csv").write_text("do not package")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "app/main.py"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Snapshot test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture"], cwd=root, check=True)
    return root, database, storage


def create(installation, destination):
    return backup.create_snapshot(*installation, destination, offline=True)


def test_restore_uses_only_copy_preserves_wal_and_untracked_sources(installation, tmp_path):
    root, database, storage = installation
    snapshot = tmp_path / "snapshot"
    # Keep WAL open to prove the SQLite backup includes committed WAL pages.
    with closing(sqlite3.connect(database)) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("INSERT INTO parent VALUES (2)")
        live.commit()
        report = create(installation, snapshot)
        assert report["recovery_complete"]
        assert report["row_counts"]["parent"] == 2
        assert len(report["references"]) == 3
    assert (snapshot / "source/requirements.lock.txt").is_file()
    assert not (snapshot / "source/.env").exists()
    assert not (snapshot / "source/runtime.log").exists()
    assert not (snapshot / "source/migration_access/export.csv").exists()
    # Remove access to the originals; successful verification must use the copy.
    storage.rename(root / "unavailable-storage")
    database.rename(root / "unavailable.db")
    restored = tmp_path / "restored"
    assert backup.restore_snapshot(snapshot, restored) == report
    assert backup.digest(restored / "database.sqlite3") == backup.digest(snapshot / "database.sqlite3")
    assert (restored / "storage/empty-batch").is_dir()
    with pytest.raises(FileExistsError):
        backup.restore_snapshot(snapshot, restored)


def test_reports_missing_external_files_and_broken_foreign_keys(installation, tmp_path):
    root, database, storage = installation
    with closing(sqlite3.connect(database)) as db:
        db.execute("INSERT INTO tblAttachments VALUES (3, 'uploads/missing.bin', 999)")
        db.execute("INSERT INTO tblAttachments VALUES (4, 'elsewhere/missing.bin', 1)")
        db.commit()
    report = create(installation, tmp_path / "snapshot")
    assert not report["recovery_complete"]
    assert report["unresolved_references"] == 2
    assert len(report["foreign_key_violations"]) == 1
    assert {r["status"] for r in report["references"]} == {
        "present", "missing_in_storage", "outside_storage",
    }
    assert backup.verify_snapshot(tmp_path / "snapshot") == report


@pytest.mark.parametrize("damage", ["modify", "remove", "extra"])
def test_verification_rejects_changed_inventory(installation, tmp_path, damage):
    snapshot = tmp_path / "snapshot"
    create(installation, snapshot)
    target = snapshot / "storage/document.bin"
    if damage == "modify":
        target.write_bytes(b"damaged")
    elif damage == "remove":
        target.unlink()
    else:
        (snapshot / "storage/extra.bin").write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="inventory/hash"):
        backup.restore_snapshot(snapshot, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_source_mutation_does_not_publish_manifest(installation, tmp_path, monkeypatch):
    root, database, storage = installation
    original = backup.shutil.copytree

    def change_after_copy(source, destination, *args, **kwargs):
        result = original(source, destination, *args, **kwargs)
        if Path(source) == storage:
            (storage / "document.bin").write_bytes(b"concurrent change")
        return result

    monkeypatch.setattr(backup.shutil, "copytree", change_after_copy)
    snapshot = tmp_path / "snapshot"
    with pytest.raises(ValueError, match="Sources changed"):
        create(installation, snapshot)
    assert not (snapshot / "manifest.json").exists()


def test_requires_offline_existing_inputs_and_new_destination(installation, tmp_path):
    root, database, storage = installation
    with pytest.raises(ValueError, match="Stop all"):
        backup.create_snapshot(*installation, tmp_path / "snapshot")
    with pytest.raises(ValueError, match="overlaps"):
        create(installation, storage / "snapshot")
    with pytest.raises(ValueError, match="must exist"):
        backup.create_snapshot(root, root / "missing.db", storage, tmp_path / "snapshot", offline=True)
    assert not (root / "missing.db").exists()
    create(installation, tmp_path / "snapshot")
    with pytest.raises(FileExistsError):
        create(installation, tmp_path / "snapshot")
