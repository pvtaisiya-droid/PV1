import copy
import hashlib
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tests.test_migrations import database_contents, migrate_test_database, assert_matches_current_models
from tools.incoming_number_repair import prepare_plan, apply_plan, ACTION


@pytest.fixture()
def duplicate_database(tmp_path, database_template):
    path = tmp_path / "review.db"
    shutil.copy2(database_template, path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE alembic_version SET version_num='20260824_0025'")
        db.execute('DROP INDEX "ix_tblIncomingRequests_message_number"')
        db.executemany('''INSERT INTO tblIncomingRequests
            (id, source_text, message_number, possible_icsr, human_confirmed,
             status, created_at, updated_at, is_active, is_deleted, version, received_date)
            VALUES (?, 'synthetic original', ?, 'no', 0, 'new',
                    '2026-01-01', '2026-01-01', 1, 0, 1, ?)''', [
            ("late-a", "2023-001", "2023-02-01"),
            ("early-a", "2023-001", "2023-01-01"),
            ("late-b", "2023-002", "2023-03-01"),
            ("early-b", "2023-002", "2023-01-02"),
            ("unique", "2023-017", "2023-01-03"),
        ])
    return path


def actor(database):
    with sqlite3.connect(database) as db:
        return db.execute("SELECT id, login FROM tblUsers WHERE is_active=1 AND is_deleted=0 AND login IS NOT NULL LIMIT 1").fetchone()


def repair(database, plan, **options):
    return apply_plan(database, plan, actor_login=actor(database)[1],
                      reason="Reviewed synthetic migration rehearsal", offline=True, **options)


def test_prepare_is_read_only_deterministic_and_retains_earliest_numbers(duplicate_database):
    before = database_contents(duplicate_database)
    digest = hashlib.sha256(duplicate_database.read_bytes()).hexdigest()
    plan = prepare_plan(duplicate_database)
    assert prepare_plan(duplicate_database) == plan
    assert [r["id"] for r in plan["retained"]] == ["early-a", "early-b"]
    assert [(r["id"], r["new_message_number"]) for r in plan["changes"]] == [
        ("late-a", "2023-018"), ("late-b", "2023-019")]
    assert database_contents(duplicate_database) == before
    assert hashlib.sha256(duplicate_database.read_bytes()).hexdigest() == digest


def test_repair_preserves_original_fields_history_and_enables_migration(duplicate_database):
    plan = prepare_plan(duplicate_database)
    before_schema, before_rows = database_contents(duplicate_database)
    result = repair(duplicate_database, plan)
    after_schema, after_rows = database_contents(duplicate_database)
    assert result["changed_records"] == result["appended_audit_events"] == 2
    assert after_schema == before_schema
    assert before_rows["tblAuditTrail"] == after_rows["tblAuditTrail"][:len(before_rows["tblAuditTrail"])]
    with sqlite3.connect(duplicate_database) as db:
        db.row_factory = sqlite3.Row
        columns = [r[1] for r in db.execute("PRAGMA table_info(tblIncomingRequests)")]
        original = {r[columns.index("id")]: dict(zip(columns, r)) for r in before_rows["tblIncomingRequests"]}
        changes = {c["id"]: c for c in plan["changes"]}
        for row in db.execute("SELECT * FROM tblIncomingRequests"):
            expected = original[row["id"]]
            if row["id"] in changes:
                change = changes[row["id"]]
                assert row["message_number"] == change["new_message_number"]
                assert row["version"] == expected["version"] + 1
                assert row["updated_by"] == actor(duplicate_database)[0]
                for field in ["message_number", "version", "updated_at", "updated_by"]:
                    expected[field] = row[field]
            assert dict(row) == expected
        audit = db.execute("SELECT * FROM tblAuditTrail WHERE action=? ORDER BY new_value", (ACTION,)).fetchall()
        assert len(audit) == 2
        for event, change in zip(audit, plan["changes"]):
            assert event["entity_id"] == change["id"]
            assert event["old_value"] == change["expected_message_number"]
            assert event["new_value"] == change["new_message_number"]
            assert event["user_id"] == event["changed_by"] == actor(duplicate_database)[0]
            assert event["correlation_id"] == result["correlation_id"]
    for name, records in before_rows.items():
        if name not in {"tblIncomingRequests", "tblAuditTrail"}:
            assert after_rows[name] == records
    migration = migrate_test_database(duplicate_database)
    assert migration.returncode == 0, migration.stdout + migration.stderr
    assert_matches_current_models(duplicate_database)
    with sqlite3.connect(duplicate_database) as db:
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            db.execute("UPDATE tblIncomingRequests SET message_number='2023-001' WHERE id='late-a'")


@pytest.mark.parametrize("error", ["stale", "changed_text", "repeat_id", "new_duplicate", "missing_change", "wrong_original", "unique_record", "empty_number", "inactive_actor", "wrong_retained"])
def test_invalid_or_stale_review_is_rejected_without_any_change(duplicate_database, error):
    plan = prepare_plan(duplicate_database)
    login = actor(duplicate_database)[1]
    if error == "stale":
        with sqlite3.connect(duplicate_database) as db:
            db.execute("UPDATE tblIncomingRequests SET version=2 WHERE id='late-a'")
    elif error == "changed_text":
        with sqlite3.connect(duplicate_database) as db:
            db.execute("UPDATE tblIncomingRequests SET source_text='changed without version' WHERE id='late-a'")
    elif error == "repeat_id":
        plan["changes"].append(copy.deepcopy(plan["changes"][0]))
    elif error == "new_duplicate":
        plan["changes"][0]["new_message_number"] = "2023-017"
    elif error == "missing_change":
        plan["changes"].pop()
    elif error == "wrong_original":
        plan["changes"][0]["expected_message_number"] = "wrong"
    elif error == "unique_record":
        plan["changes"][0]["id"] = "unique"
    elif error == "empty_number":
        plan["changes"][0]["new_message_number"] = ""
    elif error == "wrong_retained":
        plan["retained"][0]["id"] = "late-a"
    else:
        with sqlite3.connect(duplicate_database) as db:
            db.execute("UPDATE tblUsers SET is_active=0 WHERE login=?", (login,))
    before = database_contents(duplicate_database)
    with pytest.raises(ValueError):
        apply_plan(duplicate_database, plan, actor_login=login, reason="Synthetic review", offline=True)
    assert database_contents(duplicate_database) == before


def test_late_audit_failure_rolls_back_all_numbers_and_events(duplicate_database):
    plan = prepare_plan(duplicate_database)
    with sqlite3.connect(duplicate_database) as db:
        db.execute("""CREATE TRIGGER reject_second_repair BEFORE INSERT ON tblAuditTrail
            WHEN NEW.entity_id='late-b' AND NEW.action='message_number_repair'
            BEGIN SELECT RAISE(ABORT, 'synthetic late failure'); END""")
    before = database_contents(duplicate_database)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic late failure"):
        repair(duplicate_database, plan)
    assert database_contents(duplicate_database) == before


def test_repeated_application_and_missing_offline_or_reason_are_rejected(duplicate_database):
    plan = prepare_plan(duplicate_database)
    before = database_contents(duplicate_database)
    with pytest.raises(ValueError, match="offline"):
        apply_plan(duplicate_database, plan, actor_login=actor(duplicate_database)[1], reason="Review")
    with pytest.raises(ValueError, match="reason"):
        apply_plan(duplicate_database, plan, actor_login=actor(duplicate_database)[1], reason="", offline=True)
    assert database_contents(duplicate_database) == before
    repair(duplicate_database, plan)
    after = database_contents(duplicate_database)
    with pytest.raises(ValueError, match="changed"):
        repair(duplicate_database, plan)
    assert database_contents(duplicate_database) == after


def test_missing_database_and_existing_review_file_are_never_created_or_overwritten(tmp_path, duplicate_database):
    absent = tmp_path / "absent.db"
    with pytest.raises(sqlite3.OperationalError):
        prepare_plan(absent)
    assert not absent.exists()
    target = tmp_path / "review.json"
    target.write_text("keep reviewed file", encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "tools.incoming_number_repair", "prepare",
        "--database", str(duplicate_database), "--plan", str(target)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and "FileExistsError" in result.stderr
    assert target.read_text(encoding="utf-8") == "keep reviewed file"


@pytest.mark.parametrize("invalid_shape", ["document", "retained_id"])
def test_malformed_manual_review_is_a_controlled_refusal(duplicate_database, invalid_shape):
    plan = prepare_plan(duplicate_database)
    if invalid_shape == "document":
        plan = []
    else:
        plan["retained"][0]["id"] = []
    before = database_contents(duplicate_database)
    with pytest.raises(ValueError):
        repair(duplicate_database, plan)
    assert database_contents(duplicate_database) == before
