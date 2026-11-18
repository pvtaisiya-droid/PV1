import copy
import hashlib
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tests.test_migrations import (database_contents, migrate_test_database,
                                   assert_matches_current_models, legacy_database_template)
from tools.incoming_number_repair import prepare_plan, apply_plan, ACTION


@pytest.fixture()
def duplicate_database(tmp_path, legacy_database_template):
    path = tmp_path / "review.db"
    shutil.copy2(legacy_database_template, path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE alembic_version SET version_num='20260824_0025'")
        # Reproduce the reviewed pre-R3 database, with the optional history
        # column used by the repair scenarios; do not relabel a current schema.
        db.execute('ALTER TABLE tblIncomingRequests ADD COLUMN legacy_message_number VARCHAR(50)')
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


def test_suffix_plan_retains_original_numbers_and_migrates_without_history_loss(duplicate_database):
    before_schema, before_rows = database_contents(duplicate_database)
    plan = prepare_plan(duplicate_database, preserve_legacy=True)
    assert plan["format"] == 2
    assert [row["new_message_number"] for row in plan["changes"]] == ["2023-001-02", "2023-002-02"]
    assert database_contents(duplicate_database) == (before_schema, before_rows)
    result = repair(duplicate_database, plan)
    assert result["preserved_original_numbers"] == 4
    assert result["appended_audit_events"] == 6
    migration = migrate_test_database(duplicate_database)
    assert migration.returncode == 0, migration.stdout + migration.stderr
    assert_matches_current_models(duplicate_database)
    with sqlite3.connect(duplicate_database) as db:
        actual = dict(db.execute("SELECT id, legacy_message_number FROM tblIncomingRequests"))
        assert actual == {"early-a": "2023-001", "late-a": "2023-001", "early-b": "2023-002", "late-b": "2023-002", "unique": None}
        assert db.execute("SELECT COUNT(*) FROM tblAuditTrail").fetchone()[0] == len(before_rows["tblAuditTrail"]) + 6


def test_current_registration_plan_preserves_history_and_matches_application(duplicate_database):
    with sqlite3.connect(duplicate_database) as db:
        for identifier, number, deleted in [("current-1", "MSG-2031-0001", 1), ("current-3", "MSG-2031-0003", 0)]:
            db.execute('''INSERT INTO tblIncomingRequests
                (id,source_text,message_number,possible_icsr,human_confirmed,status,
                 created_at,updated_at,is_active,is_deleted,version)
                VALUES (?,'synthetic existing',?,'no',0,'new','2031-01-01','2031-01-01',1,?,1)''',
                (identifier, number, deleted))
    before = database_contents(duplicate_database)
    plan = prepare_plan(duplicate_database, numbering_policy="current", numbering_year=2031)
    assert plan["format"] == 3
    assert [c["new_message_number"] for c in plan["changes"]] == ["MSG-2031-0004", "MSG-2031-0005"]
    assert database_contents(duplicate_database) == before
    # Compare with the actual application generator on this isolated database.
    # Flush each simulated registration, then roll the whole transaction back.
    import os
    environment = dict(os.environ, DATABASE_URL="sqlite:///" + duplicate_database.as_posix(),
                       PV_STORAGE_ROOT=str(duplicate_database.parent / "generator-storage"))
    command = '''
from datetime import date
from unittest.mock import patch
from app.database import SessionLocal
from app.crud import generate_number
from app.models import IncomingRequest
with SessionLocal() as db, patch('app.crud.date') as clock:
    clock.today.return_value = date(2031, 1, 1)
    for identifier, expected in [('late-a','MSG-2031-0004'),('late-b','MSG-2031-0005')]:
        actual = generate_number(db, IncomingRequest, 'message_number', 'MSG')
        assert actual == expected
        db.get(IncomingRequest, identifier).message_number = actual
        db.flush()
    db.rollback()
'''
    result = subprocess.run([sys.executable, "-c", command], env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert database_contents(duplicate_database) == before
    repaired = repair(duplicate_database, plan)
    assert repaired["changed_records"] == 2
    assert repaired["preserved_original_numbers"] == 4
    assert repaired["appended_audit_events"] == 6
    migration = migrate_test_database(duplicate_database)
    assert migration.returncode == 0, migration.stdout + migration.stderr
    assert_matches_current_models(duplicate_database)
    with sqlite3.connect(duplicate_database) as db:
        assert db.execute("SELECT message_number,legacy_message_number FROM tblIncomingRequests WHERE id='late-a'").fetchone() == ("MSG-2031-0004", "2023-001")


@pytest.mark.parametrize("violation", ["missing_year", "changed_number", "changed_year", "missing_original"])
def test_current_registration_refuses_incomplete_or_changed_plans(duplicate_database, violation):
    before = database_contents(duplicate_database)
    if violation == "missing_year":
        with pytest.raises(ValueError, match="explicit numbering year"):
            prepare_plan(duplicate_database, numbering_policy="current")
    else:
        plan = prepare_plan(duplicate_database, numbering_policy="current", numbering_year=2031)
        if violation == "changed_number":
            plan["changes"][0]["new_message_number"] = "2023-999"
        elif violation == "changed_year":
            plan["numbering_year"] = 2032
        else:
            plan["legacy_originals"].pop()
        with pytest.raises(ValueError):
            repair(duplicate_database, plan)
    assert database_contents(duplicate_database) == before


def test_current_registration_uses_chronological_order_and_rolls_back_audit_failure(duplicate_database):
    with sqlite3.connect(duplicate_database) as db:
        db.execute("UPDATE tblIncomingRequests SET received_date='2022-12-31' WHERE id='late-b'")
        db.execute("UPDATE tblIncomingRequests SET received_date='2022-12-01' WHERE id='early-b'")
        db.execute("ALTER TABLE tblIncomingRequests DROP COLUMN legacy_message_number")
        db.execute('''CREATE TRIGGER reject_current_audit BEFORE INSERT ON tblAuditTrail
            WHEN NEW.action='message_number_repair'
            BEGIN SELECT RAISE(ABORT,'Synthetic current-number failure'); END''')
    plan = prepare_plan(duplicate_database, numbering_policy="current", numbering_year=2031)
    assert [c["id"] for c in plan["changes"]] == ["late-b", "late-a"]
    before = database_contents(duplicate_database)
    with pytest.raises(sqlite3.IntegrityError):
        repair(duplicate_database, plan)
    assert database_contents(duplicate_database) == before


def test_suffix_repair_and_new_historical_column_roll_back_together(duplicate_database):
    with sqlite3.connect(duplicate_database) as db:
        db.execute("ALTER TABLE tblIncomingRequests DROP COLUMN legacy_message_number")
        db.execute("""CREATE TRIGGER reject_legacy_audit BEFORE INSERT ON tblAuditTrail
            WHEN NEW.action='message_number_repair'
            BEGIN SELECT RAISE(ABORT, 'Synthetic repair failure'); END""")
    plan = prepare_plan(duplicate_database, preserve_legacy=True)
    before = database_contents(duplicate_database)
    with pytest.raises(sqlite3.IntegrityError):
        repair(duplicate_database, plan)
    assert database_contents(duplicate_database) == before


@pytest.mark.parametrize("violation", ["missing_original", "wrong_original", "existing_history"])
def test_suffix_plan_refuses_incomplete_or_conflicting_history(duplicate_database, violation):
    if violation == "existing_history":
        with sqlite3.connect(duplicate_database) as db:
            db.execute("UPDATE tblIncomingRequests SET legacy_message_number='preserve me' WHERE id='late-a'")
    plan = prepare_plan(duplicate_database, preserve_legacy=True)
    if violation == "missing_original":
        plan["legacy_originals"].pop()
    elif violation == "wrong_original":
        plan["legacy_originals"][0]["message_number"] = "wrong"
    before = database_contents(duplicate_database)
    with pytest.raises(ValueError):
        repair(duplicate_database, plan)
    assert database_contents(duplicate_database) == before


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


def test_historical_number_is_visible_searchable_and_exported(client):
    import csv
    import io
    from app.database import SessionLocal
    from app.models import IncomingRequest

    with SessionLocal() as db:
        row = IncomingRequest(message_number="MSG-2031-0070", legacy_message_number="2023-001",
                              source_text="Synthetic history lookup", status="confirmed")
        db.add(row)
        db.commit()
        identifier = row.id
    page = client.get("/incoming-requests", params={"search": "2023-001"})
    assert page.status_code == 200
    assert "MSG-2031-0070" in page.text
    assert "Другие номера: 2023-001 (исторический)" in page.text
    absent = client.get("/incoming-requests", params={"search": "2023-999"})
    assert absent.status_code == 200 and "MSG-2031-0070" not in absent.text
    response = client.get("/api/incoming-requests")
    assert response.status_code == 200
    api_row = next(row for row in response.json() if row["id"] == identifier)
    assert api_row["message_number"] == "MSG-2031-0070"
    assert api_row["legacy_message_number"] == "2023-001"
    response = client.get("/incoming-requests/export.csv")
    assert response.status_code == 200
    records = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    exported = next(row for row in records[1:] if "MSG-2031-0070" in row)
    assert exported[-1] == "2023-001"
