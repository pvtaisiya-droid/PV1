import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import warnings
from datetime import datetime

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def application_command(path, *arguments):
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{path.as_posix()}"
    environment["PV_STORAGE_ROOT"] = str(path.parent / (path.stem + "-storage"))
    return subprocess.run([sys.executable, *arguments], cwd=PROJECT_ROOT,
                          env=environment, capture_output=True, text=True, timeout=60)


def database_contents(path):
    """Include schema, revision and every row, not just the duplicate table."""
    with sqlite3.connect(path) as connection:
        schema = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        rows = {}
        for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            quoted = '"' + name.replace('"', '""') + '"'
            rows[name] = connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid").fetchall()
        return schema, rows


def assert_matches_current_models(path):
    # Import models in a fresh process using only this database/storage config.
    result = application_command(path, "-c", '''
import warnings
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app import models
from app.database import engine, Base
from migration_support.reconciliation_20260930 import comparison_metadata
target = comparison_metadata(Base.metadata)
with engine.connect() as connection:
    context = MigrationContext.configure(connection, opts={
        "compare_type": True,
        "include_object": lambda obj, name, kind, reflected, other:
            not (reflected and other is None and kind in {"table", "column"})
    })
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert compare_metadata(context, target) == []
    inspector = sa.inspect(connection)
    for table in target.tables.values():
        columns = {c["name"]: c for c in inspector.get_columns(table.name)}
        for column in table.columns:
            actual = columns[column.name]
            assert actual["nullable"] == column.nullable, (table.name, column.name)
            assert actual["type"]._type_affinity is column.type._type_affinity
        assert inspector.get_pk_constraint(table.name)["constrained_columns"] == list(table.primary_key.columns.keys())
        expected_fks = {(tuple(f.column_keys), f.referred_table.name,
                         tuple(e.column.name for e in f.elements))
                        for f in table.foreign_key_constraints}
        actual_fks = {(tuple(f["constrained_columns"]), f["referred_table"],
                       tuple(f["referred_columns"]))
                      for f in inspector.get_foreign_keys(table.name)}
        assert expected_fks == actual_fks, (table.name, expected_fks - actual_fks)
        expected_unique = {tuple(c.columns.keys()) for c in table.constraints
                           if isinstance(c, sa.UniqueConstraint)}
        actual_unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table.name)}
        assert expected_unique == actual_unique, table.name
        expected_indexes = {(i.name, tuple(i.columns.keys()), bool(i.unique)) for i in table.indexes}
        actual_indexes = {(i["name"], tuple(i["column_names"]), bool(i["unique"]))
                          for i in inspector.get_indexes(table.name)}
        # Former startup schema builders also created explicit unique indexes
        # with the same name/columns as an existing UniqueConstraint. Alembic
        # preserves these equivalent indexes; they add no extra data rule.
        constraint_indexes = {(c.name, tuple(c.columns.keys()), True) for c in table.constraints
                              if isinstance(c, sa.UniqueConstraint) and c.name}
        assert expected_indexes <= actual_indexes, table.name
        assert actual_indexes - expected_indexes <= constraint_indexes, table.name
    assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
engine.dispose()
''')
    assert result.returncode == 0, result.stdout + result.stderr


def migrate_test_database(path, revision="head"):
    return application_command(path, "-m", "alembic", "upgrade", revision)


def test_baseline_is_historical_not_current_models(tmp_path):
    path = tmp_path / "baseline.db"
    result = migrate_test_database(path, "20260616_0001")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "tblUsers" in tables
        assert {"tblAuthSessions", "psmf_build_documents", "outlook_oauth_requests"}.isdisjoint(tables)
        assert "login" not in {r[1] for r in connection.execute('PRAGMA table_info("tblUsers")')}


def test_reconciliation_preserves_legacy_data_and_matches_model_schema(tmp_path, database_template):
    import shutil
    path = tmp_path / "legacy.db"
    shutil.copy2(database_template, path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE alembic_version SET version_num='20260824_0025'")
        connection.execute("CREATE TABLE access_import_id_map (id TEXT PRIMARY KEY, source TEXT)")
        connection.execute("INSERT INTO access_import_id_map VALUES ('legacy', 'preserve')")
        connection.execute("ALTER TABLE tblCases ADD COLUMN legacy_note TEXT")
    result = migrate_test_database(path)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM access_import_id_map").fetchall() == [("legacy", "preserve")]
        assert "legacy_note" in {r[1] for r in connection.execute("PRAGMA table_info(tblCases)")}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    from sqlalchemy import create_engine
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from migration_support.schema_20260930 import metadata
    from migration_support.reconciliation_20260930 import comparison_metadata
    db_engine = create_engine(f"sqlite:///{path.as_posix()}")
    with db_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={
            "include_object": lambda obj, name, kind, reflected, other:
                not (reflected and other is None and kind in {"table", "column"})
        })
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert compare_metadata(context, comparison_metadata(metadata)) == []
    db_engine.dispose()
    assert_matches_current_models(path)


@pytest.mark.parametrize("equivalent", [True, False])
def test_model_schema_comparison_checks_legacy_unique_indexes(tmp_path, database_template, equivalent):
    import shutil
    path = tmp_path / "legacy-unique-index.db"
    shutil.copy2(database_template, path)
    with sqlite3.connect(path) as connection:
        if equivalent:
            connection.execute("""CREATE UNIQUE INDEX uq_product_registration_number
                ON tblProductRegistrationCertificates (product_id, registration_number)""")
        else:
            connection.execute("""CREATE UNIQUE INDEX unexpected_registration_number
                ON tblProductRegistrationCertificates (registration_number)""")
    if equivalent:
        assert_matches_current_models(path)
    else:
        with pytest.raises(AssertionError):
            assert_matches_current_models(path)


def test_duplicate_legacy_numbers_stop_upgrade_without_changing_records(tmp_path, database_template):
    import shutil
    path = tmp_path / "duplicates.db"
    shutil.copy2(database_template, path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE alembic_version SET version_num='20260824_0025'")
        connection.execute('DROP INDEX "ix_tblIncomingRequests_message_number"')
        for row_id in ("one", "two"):
            connection.execute('''INSERT INTO tblIncomingRequests
                (id, source_text, message_number, possible_icsr, human_confirmed,
                 status, created_at, updated_at, is_active, is_deleted, version)
                VALUES (?, 'original', '2023-001', 'no', 0, 'new',
                        '2026-01-01', '2026-01-01', 1, 0, 1)''', (row_id,))
        before = connection.execute("SELECT * FROM tblIncomingRequests ORDER BY id").fetchall()
    entire_database = database_contents(path)
    result = migrate_test_database(path)
    assert result.returncode != 0 and "Duplicate values in tblIncomingRequests" in result.stderr
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("20260824_0025",)
        assert connection.execute("SELECT * FROM tblIncomingRequests ORDER BY id").fetchall() == before
    assert database_contents(path) == entire_database


@pytest.mark.parametrize("failure", ["duplicates", "invalid_version"])
def test_failed_legacy_adoption_preserves_unversioned_schema(tmp_path, database_template, failure):
    import shutil
    path = tmp_path / "unversioned.db"
    shutil.copy2(database_template, path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE alembic_version")
        if failure == "duplicates":
            connection.execute('DROP INDEX "ix_tblIncomingRequests_message_number"')
            connection.executemany('''INSERT INTO tblIncomingRequests
                (id, source_text, message_number, possible_icsr, human_confirmed,
                 status, created_at, updated_at, is_active, is_deleted, version)
                VALUES (?, 'original', '2023-001', 'no', 0, 'new',
                        '2026-01-01', '2026-01-01', 1, 0, 1)''', [("one",), ("two",)])
        else:
            connection.execute("UPDATE dosage_forms SET row_version=0 WHERE id=1")
    before = database_contents(path)
    result = application_command(path, "-m", "app.manage", "adopt-legacy")
    expected = "Duplicate values in tblIncomingRequests" if failure == "duplicates" else "invalid row versions"
    assert result.returncode != 0 and expected in result.stderr, result.stdout + result.stderr
    assert database_contents(path) == before


def test_legacy_adoption_and_restart_preserve_data(tmp_path, database_template):
    import shutil
    path = tmp_path / "unversioned.db"
    shutil.copy2(database_template, path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE alembic_version")
    result = application_command(path, "-m", "app.manage", "adopt-legacy")
    assert result.returncode == 0, result.stdout + result.stderr
    before = database_contents(path)
    for arguments in [("upgrade-db",), ("check-db",), ("check-db",)]:
        result = application_command(path, "-m", "app.manage", *arguments)
        assert result.returncode == 0, result.stdout + result.stderr
        assert database_contents(path) == before
    result = application_command(path, "-m", "app.manage", "adopt-legacy")
    assert result.returncode != 0 and "already versioned" in result.stderr
    assert database_contents(path) == before
    assert_matches_current_models(path)


@pytest.mark.parametrize("missing_constraint", [False, True])
def test_orphan_links_stop_upgrade_and_roll_back_everything(tmp_path, database_template, missing_constraint):
    import shutil
    path = tmp_path / "orphan.db"
    shutil.copy2(database_template, path)
    if missing_constraint:
        # Reproduce an old table with an omitted FK. The violation will only
        # be found after adding the target FK, so this also tests DDL rollback.
        import sqlalchemy as sa
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        db_engine = sa.create_engine(f"sqlite:///{path.as_posix()}")
        with db_engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table("tblPatients", naming_convention={
                "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
            }) as batch:
                batch.drop_constraint("fk_tblPatients_case_id_tblCases", type_="foreignkey")
        db_engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE alembic_version SET version_num='20260824_0025'")
        connection.execute('''INSERT INTO tblPatients
            (id, case_id, patient_role, created_at, updated_at, is_active, is_deleted, version)
            VALUES ('orphan', 'absent-parent', 'patient', '2026-01-01', '2026-01-01', 1, 0, 1)''')
    before = database_contents(path)
    result = migrate_test_database(path)
    assert result.returncode != 0 and "foreign key violations" in result.stderr.lower(), result.stdout + result.stderr
    assert database_contents(path) == before


def test_cyclic_reconciliation_rebuilds_constraints_and_preserves_rows(tmp_path):
    import sqlalchemy as sa
    from migration_support.schema_20260930 import metadata
    from migration_support.reconciliation_20260930 import comparison_metadata
    path = tmp_path / "cyclic.db"
    legacy = comparison_metadata(metadata)
    batch_table = legacy.tables["tblE2BImportBatches"]
    for constraint in list(batch_table.foreign_key_constraints):
        if constraint.use_alter:
            batch_table.constraints.remove(constraint)
    db_engine = sa.create_engine(f"sqlite:///{path.as_posix()}")
    legacy.create_all(db_engine)
    timestamp = datetime(2026, 1, 1)
    with db_engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO alembic_version VALUES ('20260824_0025')")
        connection.exec_driver_sql("CREATE TABLE access_import_id_map (id TEXT PRIMARY KEY, source TEXT)")
        connection.exec_driver_sql("INSERT INTO access_import_id_map VALUES ('legacy', 'preserve')")
        connection.exec_driver_sql("ALTER TABLE tblCases ADD COLUMN legacy_note TEXT")
        def insert(name, **values):
            connection.execute(legacy.tables[name].insert().values(
                created_at=timestamp, updated_at=timestamp, **values))
        insert("tblE2BImportBatches", id="batch", source_filename="synthetic.zip",
               zip_checksum_sha256="a" * 64, uploaded_at=timestamp,
               created_case_id="case", created_incoming_request_id="incoming",
               created_safety_report_id="report")
        insert("tblE2BImportItems", id="item", batch_id="batch", original_path="case.xml",
               file_name="case.xml", file_role="xml")
        insert("tblCases", id="case", case_number="2026-001",
               e2b_import_batch_id="batch", safety_report_id="report")
        insert("tblSafetyReports", id="report", safety_report_number="SR-001",
               received_at=timestamp, import_batch_id="batch", import_item_id="item")
        insert("tblIncomingRequests", id="incoming", source_text="synthetic original",
               message_number="IN-001", case_id="case")
        insert("tblAuditTrail", id="audit", entity_type="Case", entity_id="case",
               action="original", timestamp=timestamp, old_value="demo MVP reporter")
    db_engine.dispose()
    before_rows = database_contents(path)[1]
    result = migrate_test_database(path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Cannot correctly sort tables" not in result.stderr
    after = database_contents(path)
    # Added row_version columns do not alter the original column values.
    for name, rows in before_rows.items():
        if name != "alembic_version":
            if rows:
                assert [r[:len(rows[0])] for r in after[1][name]] == rows
            else:
                assert after[1][name] == []
    assert_matches_current_models(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for column in ["created_case_id", "created_incoming_request_id", "created_safety_report_id"]:
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
                connection.execute(f'UPDATE tblE2BImportBatches SET {column}=? WHERE id=?', ("missing", "batch"))
            connection.rollback()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE tblAuditTrail SET old_value='changed' WHERE id='audit'")
    before_repeat = database_contents(path)
    result = migrate_test_database(path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert database_contents(path) == before_repeat


def test_valid_e2b_import_after_legacy_upgrade(tmp_path, database_template):
    import shutil
    path = tmp_path / "import-after-upgrade.db"
    shutil.copy2(database_template, path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE alembic_version SET version_num='20260824_0025'")
        connection.execute("ALTER TABLE tblCases ADD COLUMN legacy_note TEXT")
    result = migrate_test_database(path)
    assert result.returncode == 0, result.stdout + result.stderr
    result = application_command(path, "-c", '''
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.manage import bootstrap_admin
from app.config import get_settings
from app.database import engine, SessionLocal
from app.models import E2BImportBatch, Attachment
from tests.conftest import TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD
from tests.test_e2b_import import test_e2b_zip_preview_and_confirm_creates_safety_message_and_case
bootstrap_admin(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
with TestClient(app) as client:
    response = client.post("/login", data={"login": TEST_ADMIN_LOGIN,
        "password": TEST_ADMIN_PASSWORD, "next_url": "/"}, follow_redirects=False)
    assert response.status_code == 303
    test_e2b_zip_preview_and_confirm_creates_safety_message_and_case(client)
storage = get_settings().storage_root
with SessionLocal() as db:
    for batch in db.query(E2BImportBatch):
        assert Path(batch.storage_dir).resolve().is_relative_to(storage)
    for attachment in db.query(Attachment):
        if attachment.storage_path:
            assert Path(attachment.storage_path).resolve().is_relative_to(storage)
with engine.connect() as connection:
    assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
engine.dispose()
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_alembic_upgrades_empty_database_to_head(tmp_path):
    database_path = tmp_path / "alembic_empty.db"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    environment["PV_STORAGE_ROOT"] = str(tmp_path / "empty-storage")

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        contact_columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("tblContractContacts")')
        }
        safety_report_columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("tblSafetyReports")')
        }
        followup_columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("tblFollowUps")')
        }
        incoming_product_columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("tblIncomingRequestProducts")')
        }
        safety_product_columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("tblSafetyReportProducts")')
        }
        causality_assessment_columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("tblCaseProductReactionAssessments")'
            )
        }
        user_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("tblUsers")')
        }
        auth_session_columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("tblAuthSessions")')
        }
        case_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("tblCases")')
        }
        numbering_counter_columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("tblCaseNumberCounters")'
            )
        }
        case_identifier_columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("tblCaseIdentifiers")')
        }
        psmf_build_columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("psmf_partner_builds")')
        }
        attachment_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("tblAttachments")')
        }
        document_version_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("tblDocumentVersions")')
        }
        psmf_document_link_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("psmf_document_links")')
        }
        psmf_build_document_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("psmf_build_documents")')
        }

    assert revision == ("20261002_0028",)
    assert "is_pv_contact" in contact_columns
    assert "is_billing_contact" in contact_columns
    assert "message_direction" in safety_report_columns
    assert "message_direction" in followup_columns
    assert {"incoming_request_id", "product_id", "reported_product_name"} <= incoming_product_columns
    assert {"safety_report_id", "product_id", "reported_product_name"} <= safety_product_columns
    assert {
        "naranjo_score",
        "naranjo_answers_json",
        "assessor_role",
        "assessor_name",
        "assessor_organization",
        "assessment_date",
        "created_by_user_id",
    } <= causality_assessment_columns
    assert {"login", "password_hash"} <= user_columns
    assert {"user_id", "token_hash", "expires_at"} <= auth_session_columns
    assert {
        "numbering_partner_id",
        "numbering_scope",
        "numbering_year",
        "numbering_sequence",
        "number_is_provisional",
    } <= case_columns
    assert {"scope_key", "numbering_year", "last_value"} <= numbering_counter_columns
    assert {
        "case_id",
        "identifier_value",
        "partner_id",
        "identifier_type",
        "received_date",
        "is_primary",
    } <= case_identifier_columns
    assert {"pdf_file_path", "package_file_path"} <= psmf_build_columns
    assert {
        "valid_until",
        "is_signed",
        "page_count",
        "scope",
        "psmf_annex_code",
        "include_in_psmf",
        "inclusion_order",
    } <= attachment_columns
    assert {"valid_until", "is_signed", "page_count"} <= document_version_columns
    assert {"attachment_id", "document_version_id", "annex_code", "partner_id"} <= psmf_document_link_columns
    assert {"build_id", "attachment_id", "document_version_id", "annex_code"} <= psmf_build_document_columns
    assert_matches_current_models(database_path)


def test_case_numbering_migration_preserves_existing_numbers_and_seeds_counter(tmp_path):
    database_path = tmp_path / "alembic_existing_case.db"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    environment["PV_STORAGE_ROOT"] = str(tmp_path / "numbering-storage")

    before_result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260818_0021"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert before_result.returncode == 0, before_result.stdout + before_result.stderr

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            '''
            INSERT INTO "tblPartners"
                (id, partner_code, partner_name, partner_type,
                 reconciliation_frequency, created_at, updated_at,
                 is_active, is_deleted, version)
            VALUES
                ('partner-a', 'ABC', 'Partner A', 'fn',
                 'not_conducted', '2026-01-01', '2026-01-01', 1, 0, 1)
            '''
        )
        connection.execute(
            '''
            INSERT INTO "tblCases"
                (id, case_number, partner_id, case_version, workflow_status,
                 is_locked, created_at, updated_at,
                 is_active, is_deleted, version)
            VALUES
                ('case-57', '2026-057', 'partner-a', 1, 'new',
                 0, '2026-01-01', '2026-01-01', 1, 0, 1)
            '''
        )
        connection.commit()

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(database_path) as connection:
        case_row = connection.execute(
            '''
            SELECT case_number, numbering_partner_id, numbering_year,
                   numbering_sequence
            FROM "tblCases" WHERE id = 'case-57'
            '''
        ).fetchone()
        counter = connection.execute(
            '''
            SELECT last_value FROM "tblCaseNumberCounters"
            WHERE scope_key = 'PARTNER:partner-a' AND numbering_year = 2026
            '''
        ).fetchone()

    assert case_row == ("2026-057", "partner-a", 2026, 57)
    assert counter == (57,)

    next_case_result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app import crud, schemas; "
                "from app.database import SessionLocal; "
                "db=SessionLocal(); "
                "case=crud.create_case(db, schemas.CaseCreate(partner_id='partner-a')); "
                "print(case.case_number); db.close()"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert next_case_result.returncode == 0, next_case_result.stdout + next_case_result.stderr
    assert next_case_result.stdout.strip() == "2026-058"


def test_lallemand_numbers_move_to_additional_identifiers(tmp_path):
    database_path = tmp_path / "alembic_lallemand.db"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    environment["PV_STORAGE_ROOT"] = str(tmp_path / "identifiers-storage")

    before_result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260818_0022"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert before_result.returncode == 0, before_result.stdout + before_result.stderr

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            '''
            INSERT INTO "tblPartners"
                (id, partner_code, partner_name, partner_type,
                 reconciliation_frequency, created_at, updated_at,
                 is_active, is_deleted, version)
            VALUES
                ('lallemand', 'LAL', 'LALLEMAND PHARMA AG', 'fn',
                 'not_conducted', '2026-01-01', '2026-01-01', 1, 0, 1)
            '''
        )
        connection.executemany(
            '''
            INSERT INTO "tblCases"
                (id, case_number, partner_id, case_version, workflow_status,
                 is_locked, number_is_provisional, created_at, updated_at,
                 is_active, is_deleted, version)
            VALUES (?, ?, 'lallemand', 1, 'new', 0, 0,
                    '2026-01-01', '2026-01-01', 1, 0, 1)
            ''',
            [
                ("case-a", "2023-015; 2287; PV23ISM15"),
                (
                    "case-b",
                    "2022-025; PV22ISM18; 1755\r\nPV22ISM18;\r\n1755",
                ),
                ("case-clean", "2023-016"),
            ],
        )
        connection.commit()

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(database_path) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        cases = connection.execute(
            '''
            SELECT id, case_number, numbering_year, numbering_sequence
            FROM "tblCases" ORDER BY id
            '''
        ).fetchall()
        identifiers = connection.execute(
            '''
            SELECT case_id, identifier_value, identifier_type, partner_id,
                   organization_name, is_primary
            FROM "tblCaseIdentifiers"
            ORDER BY case_id, identifier_value
            '''
        ).fetchall()
        audit_count = connection.execute(
            '''
            SELECT COUNT(*) FROM "tblAuditTrail"
            WHERE action IN ('lallemand_number_normalized',
                             'lallemand_identifier_migrated')
            '''
        ).fetchone()[0]

    assert revision == ("20261002_0028",)
    assert cases == [
        ("case-a", "2023-015", 2023, 15),
        ("case-b", "2022-025", 2022, 25),
        ("case-clean", "2023-016", None, None),
    ]
    assert identifiers == [
        (
            "case-a",
            "2287",
            "partner_of_partner_case_number",
            None,
            "Партнёр LALLEMAND",
            0,
        ),
        ("case-a", "PV23ISM15", "partner_case_number", "lallemand", None, 1),
        (
            "case-b",
            "1755",
            "partner_of_partner_case_number",
            None,
            "Партнёр LALLEMAND",
            0,
        ),
        ("case-b", "PV22ISM18", "partner_case_number", "lallemand", None, 1),
    ]
    assert audit_count == 6
