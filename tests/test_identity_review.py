"""Only stage 2.2 risks; all files and rows are synthetic and isolated."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from tests.test_r3_identity_dates import source

REASON = "Сверены оба ID с ранее связанным исходным R3; исторические назначения перепутаны."


def setup_review(client):
    from app.database import SessionLocal
    from app.models import Case, E2BTransmission
    from app.e2b_import import E2BPackageService
    with SessionLocal() as db:
        service = E2BPackageService()
        old = service.prepare_batch(db, filename="old.xml", content=source(), uploaded_by_user_id=None)
        item = next(i for i in old.items if i.file_role == "xml")
        case = Case(case_number="SYNTH-LEGACY", worldwide_case_id="Sender-Case-X",
                    local_sender_case_id="ZZ-SYNTH-0001")
        db.add(case); db.flush()
        tx = E2BTransmission(batch_id=old.id, import_item_id=item.id, case_id=case.id,
            worldwide_case_id="Sender-Case-X", local_sender_case_id="ZZ-SYNTH-0001",
            xml_checksum_sha256=item.checksum_sha256, normalized_json='{"historical":"untouched"}')
        db.add(tx); db.commit()
        new = service.prepare_batch(db, filename="new.xml",
            content=source(**{"Message-NotVersion-99":"Message-NotVersion-100"}), uploaded_by_user_id=None)
        return case.id, tx.id, new.id, item.normalized_json, item.stored_path


def form(client, batch_id):
    from tests.test_e2b_import import Forms
    url = f"/safety-reports/e2b-imports/{batch_id}/identity-review"
    response = client.get(url)
    assert response.status_code == 200, response.text
    data = dict(Forms(response.text).forms[url])
    data["reason"] = REASON
    return url, data, response.text


def test_review_history(client):
    from app.database import SessionLocal
    from app.models import Case, CaseIdentityDecision, E2BTransmission, E2BImportBatch, AuditTrail
    from app.e2b_import import E2BImportService, E2BParser
    from app.identity_review import MANUAL_PROFILE
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    case_id, tx_id, batch_id, normalized, source_path = setup_review(client)
    url, data, html = form(client, batch_id)
    assert "историческим случаем" in html and "Историческое назначение ID не проверено" in html
    downloaded = client.get(url+f"/sources/{tx_id}")
    assert downloaded.status_code == 200 and downloaded.content == source()
    response = client.post(url, data=data, follow_redirects=False)
    assert response.status_code == 303 and "message=" in response.headers["location"], response.text
    with SessionLocal() as db:
        case = db.get(Case,case_id); row = db.query(CaseIdentityDecision).one()
        assert case.e2b_identity_profile == MANUAL_PROFILE
        assert case.worldwide_case_id == "ZZ-SYNTH-0001" and case.local_sender_case_id == "Sender-Case-X"
        assert json.loads(row.before_json)["worldwide_case_id"] == "Sender-Case-X"
        assert json.loads(row.before_json)["e2b_identity_profile"] is None
        assert row.author_id and row.decided_at and row.case_version_before == int(data["expected_case_version"])
        assert row.reason == REASON and row.evidence_transmission_id == tx_id
        assert db.query(AuditTrail).filter_by(entity_type="CaseIdentityDecision", entity_id=row.id).count() == 1
        tx = db.get(E2BTransmission,tx_id)
        assert tx.normalized_json == '{"historical":"untouched"}' and tx.item.normalized_json == normalized
        assert Path(source_path).read_bytes() == source()
        assert E2BImportService().preview_case(db,db.get(E2BImportBatch,batch_id)).id == case_id
        assert db.get(E2BImportBatch,batch_id).status != "imported" and db.query(E2BTransmission).count() == 1
        first_id = row.id
        # The DB, not just the UI, rejects history mutation and deletion.
        for statement in ['UPDATE "tblCaseIdentityDecisions" SET reason=\'changed\'',
                          'DELETE FROM "tblCaseIdentityDecisions"']:
            with pytest.raises(IntegrityError, match="append-only"):
                db.execute(text(statement))
            db.rollback()
    # Repeated form is stale; no second decision or audit entry.
    assert client.post(url,data=data,follow_redirects=False).status_code == 409
    url, fresh, _ = form(client,batch_id)
    fresh["reason"] = "Уточнение основания: сопоставлены назначения обоих ID и пространство отправителя в том же исходнике."
    assert fresh["supersedes_id"] == first_id
    assert client.post(url,data=fresh,follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        rows = db.query(CaseIdentityDecision).order_by(CaseIdentityDecision.case_version_before).all()
        assert len(rows) == 2 and rows[0].reason == REASON and rows[1].supersedes_id == first_id
        # A later source change blocks matching instead of trusting the manual flag.
        Path(source_path).write_bytes(b"changed synthetic source")
        with pytest.raises(ValueError, match="сумма"):
            E2BImportService().find_existing_case(db,E2BParser().parse(source()).message)


def test_review_rejections(client):
    from app.database import SessionLocal
    from app.models import Case, CaseIdentityDecision, E2BTransmission
    from app.e2b_r3_identity import PROFILE
    from app.e2b_import import E2BPackageService
    case_id, tx_id, batch_id, _, source_path = setup_review(client)
    url, data, _ = form(client,batch_id)
    short = dict(data,reason="проверено")
    assert "validation=" in client.post(url,data=short,follow_redirects=False).headers["location"]
    Path(source_path).write_bytes(b"no evidence")
    assert "validation=" in client.post(url,data=data,follow_redirects=False).headers["location"]
    Path(source_path).write_bytes(source())
    # A different, readable historical source already linked to this case
    # cannot be silently ignored in favour of the selected source.
    with SessionLocal() as db:
        batch = E2BPackageService().prepare_batch(db, filename="conflict.xml",
            content=source(**{"ZZ-SYNTH-0001":"CONFLICT-WW"}), uploaded_by_user_id=None)
        item = next(i for i in batch.items if i.file_role == "xml")
        conflicting = E2BTransmission(batch_id=batch.id,import_item_id=item.id,case_id=case_id,
            xml_checksum_sha256=item.checksum_sha256,normalized_json='{}')
        db.add(conflicting); db.commit(); conflicting_id = conflicting.id
    _, fresh, _ = form(client,batch_id)
    fresh["transmission_id"] = tx_id
    response = client.post(url,data=fresh,follow_redirects=False)
    assert "Другой связанный источник" in client.get(response.headers["location"]).text
    with SessionLocal() as db:
        db.get(E2BTransmission,conflicting_id).is_deleted = True
        db.commit()
    with SessionLocal() as db:
        db.add(Case(case_number="CONFIRMED",worldwide_case_id="ZZ-SYNTH-0001",
            local_sender_case_id="OTHER",e2b_identity_profile=PROFILE,e2b_sender_scope='["other"]'))
        db.commit()
    url, fresh, _ = form(client,batch_id)
    fresh["transmission_id"] = tx_id
    # The page has only one reviewable legacy case; confirmed case has no form.
    response = client.post(url,data=fresh,follow_redirects=False)
    assert "validation=" in response.headers["location"]
    assert "заняты другим случаем" in client.get(response.headers["location"]).text
    with SessionLocal() as db:
        assert db.query(CaseIdentityDecision).count() == 0
        assert db.get(Case,case_id).worldwide_case_id == "Sender-Case-X"
        assert db.get(E2BTransmission,tx_id).case_id == case_id


def test_review_access_and_stale(client):
    from app.database import SessionLocal
    from app.models import Case, CaseIdentityDecision, UserRole, Role, User
    case_id, tx_id, batch_id, _, _ = setup_review(client)
    url, data, _ = form(client,batch_id)
    with SessionLocal() as db:
        case = db.get(Case,case_id); case.is_locked = True; db.commit()
    assert client.post(url,data=data,follow_redirects=False).status_code == 409
    _, locked, _ = form(client,batch_id)
    response = client.post(url,data=locked,follow_redirects=False)
    assert "validation=" in response.headers["location"]
    with SessionLocal() as db:
        user = db.query(User).filter_by(login="test-admin").one()
        db.query(UserRole).filter_by(user_id=user.id).delete()
        viewer = db.query(Role).filter_by(role_code="readonly_auditor").one()
        user.role = "viewer"
        db.add(UserRole(user_id=user.id,role_id=viewer.id)); db.commit()
    assert client.post(url,data=locked,follow_redirects=False).status_code == 403
    assert client.get(url).status_code == 403
    assert client.get(url+f"/sources/{tx_id}").status_code == 403
    with SessionLocal() as db:
        assert db.query(CaseIdentityDecision).count() == 0
        assert db.get(Case,case_id).e2b_identity_profile is None


def test_upgrade_0029(tmp_path):
    import sqlalchemy as sa
    from tests.test_migrations import migrate_test_database, assert_matches_current_models
    from migration_support.schema_20260930 import metadata
    path = tmp_path / "upgrade.db"
    result = migrate_test_database(path,"20261110_0029")
    assert result.returncode == 0, result.stdout+result.stderr
    engine = sa.create_engine(f"sqlite:///{path.as_posix()}")
    common = dict(created_at=datetime(2025,1,2),updated_at=datetime(2025,1,2),is_active=True,is_deleted=False,version=1)
    with engine.begin() as conn:
        for i in range(2):
            conn.execute(metadata.tables["tblCases"].insert().values(**common,id=f"case{i}",case_number=f"OLD-{i}",
                worldwide_case_id=f"W-{i}",local_sender_case_id=f"L-{i}",number_is_provisional=False,
                case_version=3,workflow_status="new",is_locked=False))
            conn.execute(metadata.tables["tblPatients"].insert().values(**common,id=f"p{i}",case_id=f"case{i}",
                patient_initials="SY",patient_role="patient"))
    engine.dispose()
    with sqlite3.connect(path) as conn:
        columns={t:[r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')] for t in ['tblCases','tblPatients']}
        before={t:conn.execute('SELECT '+','.join(chr(34)+c+chr(34) for c in columns[t])+f' FROM "{t}" ORDER BY id').fetchall() for t in columns}
    result = migrate_test_database(path)
    assert result.returncode == 0,result.stdout+result.stderr
    with sqlite3.connect(path) as conn:
        for table,rows in before.items():
            assert conn.execute('SELECT '+','.join(chr(34)+c+chr(34) for c in columns[table])+f' FROM "{table}" ORDER BY id').fetchall() == rows
        assert conn.execute('SELECT count(*) FROM tblCaseIdentityDecisions').fetchone()[0] == 0
        assert conn.execute('SELECT e2b_identity_profile,e2b_sender_scope FROM tblCases').fetchall() == [(None,None)]*2
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '20261117_0037'
    assert_matches_current_models(path)
