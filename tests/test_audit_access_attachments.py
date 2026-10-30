from contextlib import closing
import csv
import sqlite3

import pytest

from tools import audit_access_attachments as audit
from tools import backup_snapshot as backup
from tests.test_backup_snapshot import installation  # isolated DB/storage/Git fixture


@pytest.fixture()
def evidence(installation, tmp_path):
    root, database, storage = installation
    with closing(sqlite3.connect(database)) as db:
        db.executescript("""
            ALTER TABLE tblAttachments ADD COLUMN checksum_sha256 TEXT;
            CREATE TABLE access_import_id_map (
                source_table TEXT, source_id TEXT, target_table TEXT, target_id TEXT);
            INSERT INTO tblAttachments VALUES (3, 'access-migration/1', 1, NULL);
            INSERT INTO tblAttachments VALUES (4, 'access-migration/1', 1, NULL);
            INSERT INTO access_import_id_map VALUES ('A', '1', 'tblAttachments', '3');
            INSERT INTO access_import_id_map VALUES ('B', '1', 'tblAttachments', '4');
        """)
        db.commit()
    source_csv = tmp_path / "attachments.csv"
    fields = ["legacy_access_table", "legacy_access_id", *audit.FILE_METADATA]
    with source_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows([
            {"legacy_access_table": "A", "legacy_access_id": "1"},
            {"legacy_access_table": "B", "legacy_access_id": "1", "file_url": "source-hint"},
        ])
    snapshot = tmp_path / "snapshot"
    backup.create_snapshot(root, database, storage, snapshot, offline=True)
    return snapshot, source_csv


def test_mapping_requires_source_table_and_preserves_missing_status(evidence):
    snapshot, source_csv = evidence
    before = backup.inventory(snapshot)
    report = audit.analyze(snapshot, source_csv)
    assert report["explained_import_placeholders"] == 1
    assert report["unexplained_references"] == 1
    assert report["recovery_complete"] is False
    assert report["entries"][0]["csv_row"] == 2
    assert report["entries"][1]["classification"] == "unexplained"
    assert "source-hint" not in str(report)
    assert backup.inventory(snapshot) == before


def test_ambiguous_csv_identity_does_not_explain_placeholder(evidence):
    snapshot, source_csv = evidence
    with source_csv.open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow(["A", "1", *["" for _ in audit.FILE_METADATA]])
    report = audit.analyze(snapshot, source_csv)
    assert report["explained_import_placeholders"] == 0
    assert report["unexplained_references"] == 2


def test_rejects_incomplete_source_and_damaged_snapshot(evidence):
    snapshot, source_csv = evidence
    source_csv.write_text("legacy_access_id\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="required"):
        audit.analyze(snapshot, source_csv)
    (snapshot / "storage/document.bin").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="inventory/hash"):
        audit.analyze(snapshot, source_csv)


def test_checksum_and_unknown_record_remain_unexplained(evidence, installation, tmp_path):
    _, source_csv = evidence
    with closing(sqlite3.connect(installation[1])) as db:
        db.execute("UPDATE tblAttachments SET checksum_sha256='previous-file' WHERE id=3")
        db.execute("INSERT INTO tblAttachments VALUES (5, 'access-migration/2', 1, NULL)")
        db.commit()
    snapshot = tmp_path / "second-snapshot"
    backup.create_snapshot(*installation, snapshot, offline=True)
    report = audit.analyze(snapshot, source_csv)
    assert report["unexplained_references"] == 3
    assert report["explained_import_placeholders"] == 0


def test_cli_refuses_to_add_report_to_verified_snapshot(evidence, monkeypatch):
    snapshot, source_csv = evidence
    output = snapshot / "extra.json"
    monkeypatch.setattr("sys.argv", ["audit", str(snapshot), str(source_csv), str(output)])
    with pytest.raises(SystemExit) as error:
        audit.main()
    assert error.value.code == 2
    assert not output.exists()
