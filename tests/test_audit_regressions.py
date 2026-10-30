import hashlib
import hmac
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError


def create_case(client):
    response = client.post("/api/cases", json={"workflow_status": "new"})
    assert response.status_code == 201, response.text
    return response.json()


def test_revoked_permissions_apply_to_existing_session_and_survive_restart(client):
    from app import crud
    from app.auth import load_user
    from app.database import SessionLocal, init_db
    from app.models import Role, Permission
    from app.rbac import ensure_rbac_defaults, permission_codes_for_user
    with SessionLocal() as db:
        role = db.query(Role).filter_by(role_code="admin").one()
        view = db.query(Permission).filter_by(permission_code="view").one()
        crud.update_role_permissions(db, role_id=role.id, permission_ids=[view.id])
        ensure_rbac_defaults(db)
        db.expire_all()
        assert permission_codes_for_user(load_user(db, login="test-admin")) == {"view"}
    init_db()
    assert client.post("/api/cases", json={}).status_code == 403
    assert client.get("/api/cases").status_code == 200
    with SessionLocal() as db:
        crud.update_role_permissions(db, role_id=role.id, permission_ids=[])
        ensure_rbac_defaults(db)
    assert client.get("/api/cases").status_code == 403
    assert client.get("/cases").status_code == 403


def test_inactive_or_deleted_rbac_nodes_never_grant_access(client):
    from app.auth import load_user
    from app.database import SessionLocal
    from app.rbac import permission_codes_for_user, ensure_rbac_defaults
    with SessionLocal() as db:
        user = load_user(db, login="test-admin")
        link = user.user_roles[0]
        role = link.role
        grant = next(x for x in role.role_permissions if x.permission.permission_code == "create")
        for node in (user, link, role, grant, grant.permission):
            for attr, value in (("is_active", False), ("is_deleted", True)):
                old = getattr(node, attr)
                setattr(node, attr, value)
                assert "create" not in permission_codes_for_user(user)
                setattr(node, attr, old)
        link.is_deleted = True
        db.commit()
        ensure_rbac_defaults(db)
        db.expire_all()
        assert permission_codes_for_user(load_user(db, login="test-admin")) == set()


def test_startup_preserves_business_data_and_audit_history(client):
    from app.database import SessionLocal, engine, init_db
    from app.models import SafetyReport, AuditTrail
    with SessionLocal() as db:
        db.add(SafetyReport(safety_report_number="MVP-demo", reporter_email="reporter@real-synthetic-domain.org",
                           triage_comment="Reviewed MVP publication; no missing data"))
        db.add(AuditTrail(entity_type="SafetyReport", entity_id="synthetic", action="create",
                          old_value="example.test original value", comment="demo MVP"))
        db.commit()
    def snapshot():
        with engine.connect() as connection:
            return {name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY id').fetchall()
                    for name in ("tblSafetyReports", "tblAuditTrail", "tblRoles", "psmf_component_versions", "tblSOPs")}
    before = snapshot()
    init_db()
    init_db()
    assert snapshot() == before
    with engine.begin() as connection:
        with pytest.raises(IntegrityError, match="append-only"):
            connection.exec_driver_sql('UPDATE "tblAuditTrail" SET comment = \'rewritten\'')
        with pytest.raises(IntegrityError, match="append-only"):
            connection.exec_driver_sql('DELETE FROM "tblAuditTrail"')


def test_two_sessions_cannot_overwrite_a_closed_case(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import AuditTrail
    record = create_case(client)
    with SessionLocal() as first, SessionLocal() as second:
        a, b = crud.get_case(first, record["id"]), crud.get_case(second, record["id"])
        crud.update_case_status(first, a, schemas.CaseStatusUpdate(workflow_status="closed", expected_version=a.version))
        count = first.query(AuditTrail).count()
        with pytest.raises(StaleDataError):
            crud.update_case_status(second, b, schemas.CaseStatusUpdate(workflow_status="data_entry", expected_version=b.version))
        second.rollback()
        first.expire_all()
        saved = crud.get_case(first, record["id"])
        assert saved.workflow_status == "closed" and saved.is_locked
        assert first.query(AuditTrail).count() == count


def test_api_and_form_reject_stale_case_versions(client):
    record = create_case(client)
    url = f'/api/cases/{record["id"]}/status'
    payload = {"workflow_status": "data_entry", "expected_version": record["version"]}
    assert client.patch(url, json=payload).status_code == 200
    assert client.patch(url, json=payload).status_code == 409
    assert client.patch(url, json={"workflow_status": "closed"}).status_code == 422
    response = client.post(f'/cases/{record["id"]}/status', data=payload)
    assert response.status_code == 409


def test_child_write_conflicts_with_concurrent_case_closure(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import Patient
    record = create_case(client)
    with SessionLocal() as first, SessionLocal() as second:
        a, b = crud.get_case(first, record["id"]), crud.get_case(second, record["id"])
        crud.update_case_status(first, a, schemas.CaseStatusUpdate(workflow_status="closed", expected_version=a.version))
        with pytest.raises(StaleDataError):
            crud.add_patient(second, b, schemas.PatientCreate(patient_initials="AB"))
        second.rollback()
        assert first.query(Patient).filter_by(case_id=a.id).count() == 0


def test_foreign_keys_are_enabled_on_each_connection(client):
    from app.database import SessionLocal, engine
    from app.models import Patient
    engine.dispose()
    with engine.connect() as a, engine.connect() as b:
        assert a.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert b.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    with SessionLocal() as db:
        db.add(Patient(case_id="missing-parent", patient_initials="AB"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_case_collections_do_not_multiply_sql_rows_and_api_is_bounded(client):
    from app import crud
    from app.database import SessionLocal, engine
    from app.models import Patient, CaseProduct, Reaction, FollowUp, AuditTrail, Case
    record = create_case(client)
    with SessionLocal() as db:
        for model, count, values in ((Patient, 2, {}), (CaseProduct, 3, {"reported_product_name": "Тестовый препарат"}),
                                    (Reaction, 4, {"reported_term": "Реакция"}), (FollowUp, 5, {"follow_up_number": 1}),
                                    (AuditTrail, 6, {"entity_type": "Case", "entity_id": record["id"], "action": "review"})):
            db.add_all(model(case_id=record["id"], **values) for _ in range(count))
        db.add_all(Case(case_number=f"PAGE-{i}", narrative="ПАГИНАЦИЯ") for i in range(25))
        db.commit()
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with SessionLocal() as db:
            loaded = crud.get_case(db, record["id"])
            assert [len(loaded.patients), len(loaded.case_products), len(loaded.reactions), len(loaded.followups)] == [2, 3, 4, 5]
        primary, params = statements[0]
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    with engine.connect() as connection:
        assert len(connection.exec_driver_sql(primary, params).fetchall()) == 1
    assert 'tblAuditTrail' not in primary
    first = client.get("/api/cases?search=пагинация&per_page=1000").json()
    second = client.get("/api/cases?search=пагинация&per_page=20&page=2").json()
    assert len(first) == 20 and len(second) == 5
    assert not {r["id"] for r in first} & {r["id"] for r in second}


def test_e2b_get_is_read_only_and_recheck_requires_permission_and_csrf(client, monkeypatch):
    from app.database import SessionLocal
    from app.models import Role, UserRole, User
    from app.e2b_import import E2BPackageService
    from test_e2b_import import e2b_xml, upload_xml, latest_batch
    assert upload_xml(client, e2b_xml()).status_code == 303
    batch = latest_batch()
    calls = []
    monkeypatch.setattr(E2BPackageService, "recheck_batch", lambda *args, **kwargs: calls.append(batch.id))
    url = f"/safety-reports/e2b-imports/{batch.id}"
    assert client.get(url + "?recheck=1").status_code == 200
    assert calls == []
    assert client.post(url + "/recheck").status_code == 403
    token = hmac.new(client.cookies["pv_session"].encode(), b"pv-csrf-v1", hashlib.sha256).hexdigest()
    assert client.post(url + "/recheck", data={"csrf": token, "expected_version": batch.version}, follow_redirects=False).status_code == 303
    assert len(calls) == 1
    with SessionLocal() as db:
        user = db.query(User).filter_by(login="test-admin").one()
        for link in user.user_roles:
            link.is_deleted = True
        role = db.query(Role).filter_by(role_code="readonly_auditor").one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    assert client.get(url + "?recheck=1").status_code == 200
    assert client.post(url + "/recheck", data={"csrf": token}).status_code == 403
    assert len(calls) == 1


def test_oauth_state_is_session_bound_expiring_single_use_and_uses_pkce(client, monkeypatch):
    from app import outlook_service as outlook
    from app.auth import create_auth_session
    from app.database import SessionLocal
    from app.models import User, OutlookOAuthRequest, utcnow
    for key in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI"):
        monkeypatch.setenv("MICROSOFT_" + key, "https://example.test/callback" if key == "REDIRECT_URI" else "test")
    calls = []
    def token_exchange(url, **kwargs):
        calls.append(kwargs["data"])
        return httpx.Response(200, json={"access_token": "synthetic", "expires_in": 3600})
    monkeypatch.setattr(outlook.httpx, "post", token_exchange)
    with SessionLocal() as db:
        user = db.query(User).filter_by(login="test-admin").one()
        uid = user.id
        other_session = create_auth_session(db, user)
    token = client.cookies["pv_session"]
    def begin():
        return parse_qs(urlsplit(outlook.get_auth_url(user_key=uid, session_token=token, reconciliation_id="target")).query)
    def callback(state, session=token):
        return outlook.handle_callback(code="code", state=state, user_key=uid, session_token=session)
    for state in (None, "unissued-state"):
        with pytest.raises(outlook.OutlookAuthorizationError):
            callback(state)
    auth = begin()
    state = auth["state"][0]
    assert state != "target" and auth["code_challenge_method"] == ["S256"]
    with pytest.raises(outlook.OutlookAuthorizationError):
        callback(state, other_session)
    assert calls == []
    assert callback(state)["reconciliation_id"] == "target"
    import base64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(calls[0]["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert challenge == auth["code_challenge"][0]
    with pytest.raises(outlook.OutlookAuthorizationError):
        callback(state)
    expired = begin()["state"][0]
    with SessionLocal() as db:
        row = db.get(OutlookOAuthRequest, hashlib.sha256(expired.encode()).hexdigest())
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    with pytest.raises(outlook.OutlookAuthorizationError):
        callback(expired)
    assert len(calls) == 1


def test_upload_limits_and_storage_are_isolated(client, tmp_path, monkeypatch):
    from app.config import get_settings
    from app import e2b_import, psmf, psmf_export, reconciliation_documents
    from app.routers import placeholders
    from test_e2b_import import e2b_xml, upload_xml, latest_batch
    for path in (e2b_import.UPLOAD_ROOT, psmf.PSMF_DOCUMENT_DIR, psmf_export.DOCUMENT_UPLOAD_ROOT,
                 reconciliation_documents.RECONCILIATION_DOCUMENT_DIR, placeholders.UPLOAD_ROOT):
        assert path.resolve().is_relative_to(tmp_path)
    assert upload_xml(client, e2b_xml()).status_code == 303
    assert Path(latest_batch().source_zip_path).resolve().is_relative_to(tmp_path)
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1024)
    assert upload_xml(client, b"x" * 1025).status_code == 413
    assert client.post("/safety-reports/e2b-imports", content=b"x" * (1024 * 1024 + 1025)).status_code == 413


def test_bootstrap_refuses_password_reset_and_explicit_recovery_is_audited(client):
    from app.manage import bootstrap_admin
    from app.database import SessionLocal
    from app.models import AuditTrail
    with pytest.raises(ValueError, match="will not reset"):
        bootstrap_admin("test-admin", "Different-password-2026!")
    bootstrap_admin("test-admin", "Different-password-2026!", recover=True)
    assert client.get("/api/cases").status_code == 401
    with SessionLocal() as db:
        assert db.query(AuditTrail).filter_by(action="admin_recovery").count() == 1


def test_report_period_is_explicit_for_current_and_historical_dates(client):
    from app.accounting_activity_report import describe_period
    assert describe_period(date(2026, 7, 1), date(2026, 8, 24), date(2026, 8, 24))[1]
    assert describe_period(date(2026, 7, 1), date(2026, 8, 24), date(2026, 9, 21))[1] is None


def test_documents_and_reference_data_reject_concurrent_writes(client):
    from app.database import SessionLocal
    from app.models import SOP, Product
    with SessionLocal() as db:
        product = Product(product_code="CONCURRENT", product_name="Original")
        db.add(product)
        db.commit()
        targets = [(Product, product.id, "product_name"), (SOP, db.query(SOP).first().id, "title")]
    for model, row_id, field in targets:
        with SessionLocal() as first, SessionLocal() as second:
            a, b = first.get(model, row_id), second.get(model, row_id)
            setattr(a, field, "First change")
            first.commit()
            setattr(b, field, "Stale change")
            with pytest.raises(StaleDataError):
                second.commit()
            second.rollback()
            first.expire_all()
            assert getattr(first.get(model, row_id), field) == "First change"


def test_chunked_request_is_rejected_before_downstream_processing(client, monkeypatch):
    import asyncio
    from app.config import get_settings
    from app.security import RequestSizeLimitMiddleware
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1024)
    calls, sent = [], []
    chunks = [b"x" * (1024 * 1024), b"x" * 1025, b"never-read"]
    async def application(scope, receive, send):
        calls.append("application")
    async def receive():
        return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}
    async def send(message):
        sent.append(message)
    asyncio.run(RequestSizeLimitMiddleware(application)({"type": "http", "method": "POST", "headers": []}, receive, send))
    assert sent[0]["status"] == 413 and calls == [] and chunks == [b"never-read"]
