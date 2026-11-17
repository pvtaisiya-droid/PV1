"""Exercise the reviewed route policy against real HTTP authorization."""
import inspect
import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request


MATRIX = json.loads((Path(__file__).resolve().parents[1] / "docs/access-control-matrix.json").read_text(encoding="utf-8"))


def route_url(path):
    return re.sub(r"\{[^}]+\}", "1", path)


def set_permissions(codes):
    from app import crud
    from app.database import SessionLocal
    from app.models import Permission, Role
    with SessionLocal() as db:
        role = db.query(Role).filter_by(role_code="admin").one()
        ids = [p.id for p in db.query(Permission).filter(Permission.permission_code.in_(codes))]
        crud.update_role_permissions(db, role_id=role.id, permission_ids=ids)


def permission_guards(dependant):
    guards = []
    for dependency in dependant.dependencies:
        if dependency.call:
            values = inspect.getclosurevars(dependency.call).nonlocals
            if "permission_code" in values:
                guards.append(([values["permission_code"]], dependency.call))
            elif "permission_codes" in values:
                guards.append((sorted(values["permission_codes"]), dependency.call))
        guards.extend(permission_guards(dependency))
    return guards


def test_reviewed_matrix_matches_registered_routes_and_each_permission_guard(client):
    from app.main import app
    actual = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        guards = permission_guards(route.dependant)
        for method in route.methods:
            actual[(method, route.path)] = [codes for codes, _ in guards]
        # Check every OR alternative and denial of each AND group separately.
        for codes, guard in guards:
            request = Request({"type": "http", "method": "POST", "path": route.path, "headers": []})
            request.state.permission_codes = set()
            with pytest.raises(HTTPException) as denied:
                guard(request)
            assert denied.value.status_code == 403
            for code in codes:
                request.state.permission_codes = {code}
                guard(request)
    expected = {(row["method"], row["path"]): row["permission_groups"] for row in MATRIX["routes"]}
    assert actual == expected
    for row in MATRIX["routes"]:
        if row["method"] not in {"GET", "HEAD"} and not row["public"]:
            assert row["permission_groups"] or row["path"] == "/logout"


def test_every_protected_route_rejects_unauthenticated_requests(client):
    client.cookies.clear()
    for row in MATRIX["routes"]:
        if row["public"]:
            continue
        response = client.request(row["method"], route_url(row["path"]), follow_redirects=False)
        assert response.status_code == (401 if row["path"].startswith("/api/") else 303), row
    assert client.get("/health").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    assert client.post("/login").status_code == 422


def test_every_protected_read_and_action_rejects_session_without_permissions(client):
    set_permissions([])
    for row in MATRIX["routes"]:
        if row["public"] or row["path"] == "/logout":
            continue
        response = client.request(row["method"], route_url(row["path"]), follow_redirects=False)
        assert response.status_code == 403, (row, response.text)
        if row["method"] == "GET":
            assert client.head(route_url(row["path"])).status_code == 403, row
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 403
    assert client.get("/health").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    assert client.post("/logout", follow_redirects=False).status_code == 303


def test_view_alone_cannot_mutate_export_connect_outlook_or_manage_users(client):
    set_permissions(["view"])
    for row in MATRIX["routes"]:
        if not row["permission_groups"] or all("view" in group for group in row["permission_groups"]):
            continue
        response = client.request(row["method"], route_url(row["path"]), follow_redirects=False)
        assert response.status_code == 403, (row, response.text)
    assert client.get("/api/cases").status_code == 200
    assert client.get("/cases").status_code == 200
    assert client.get("/psmf?tab=audit").status_code == 403
    assert '/incoming-requests/export.xlsx' not in client.get("/incoming-requests").text
    assert '/accounting-data/export' not in client.get("/accounting-data").text
    assert client.post("/incoming-requests/analyze", data={"source_text": "Synthetic text"}).status_code == 200


def test_export_requires_view_and_user_archive_requires_both_permissions(client):
    set_permissions(["export"])
    assert client.get("/accounting-data/export").status_code == 403
    set_permissions(["view", "export"])
    assert client.get("/accounting-data/export").status_code == 200
    assert client.get("/incoming-requests/export.csv").status_code == 200
    assert client.get("/api/psur/missing/audit/export.csv").status_code == 403
    set_permissions(["view", "export", "audit_view"])
    assert client.get("/api/psur/missing/audit/export.csv").status_code == 404
    assert client.get("/psmf?tab=audit").status_code == 200
    for codes in (["view", "soft_delete"], ["view", "manage_users"], ["upload"]):
        set_permissions(codes)
        assert client.post("/users-roles/users/missing/archive").status_code == 403
        assert client.post("/partner-onboarding/missing/step/1").status_code == 403


def test_legacy_role_never_restores_missing_or_revoked_assignments(client):
    from app.auth import load_user
    from app.database import SessionLocal, init_db
    from app.models import UserRole
    from app.rbac import ensure_rbac_defaults, permission_codes_for_user
    with SessionLocal() as db:
        user = load_user(db, login="test-admin")
        assert user.role == "admin"
        for link in user.user_roles:
            link.is_active = False
        db.commit()
        ensure_rbac_defaults(db)
        db.expire_all()
        assert permission_codes_for_user(load_user(db, login="test-admin")) == set()
    assert client.get("/api/cases").status_code == 403
    with SessionLocal() as db:
        user = load_user(db, login="test-admin")
        for link in user.user_roles:
            link.is_deleted = True
        db.commit()
        ensure_rbac_defaults(db)
        db.expire_all()
        assert permission_codes_for_user(load_user(db, login="test-admin")) == set()
        # Even a physically absent relationship must not trigger fallback.
        db.query(UserRole).filter_by(user_id=user.id).delete(synchronize_session=False)
        db.commit()
        ensure_rbac_defaults(db)
        db.expire_all()
        assert permission_codes_for_user(load_user(db, login="test-admin")) == set()
    init_db()
    assert client.get("/api/cases").status_code == 403
    assert client.post("/api/cases", json={}).status_code == 403


def test_production_public_exceptions_and_disabled_docs(tmp_path, monkeypatch, database_template):
    from conftest import clear_app_modules, TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD
    database = tmp_path / "production.db"
    shutil.copy2(database_template, database)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    monkeypatch.setenv("PV_STORAGE_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("PV_APP_MODE", "prod")
    monkeypatch.delenv("PV_USER_SWITCH_ENABLED", raising=False)
    clear_app_modules()
    from app.main import app
    from app.database import engine
    from app.manage import bootstrap_admin
    bootstrap_admin(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
    try:
        with TestClient(app) as production:
            assert production.get("/", follow_redirects=False).status_code == 303
            assert production.get("/api/cases").status_code == 401
            for path in ("/docs", "/redoc", "/openapi.json"):
                assert production.get(path).status_code == 404
            assert production.get("/login").status_code == 200
            assert production.get("/health").status_code == 200
            assert production.get("/static/style.css").status_code == 200
            assert production.post("/login", data={"login": TEST_ADMIN_LOGIN, "password": TEST_ADMIN_PASSWORD},
                                   follow_redirects=False).status_code == 303
            assert production.get("/api/cases").status_code == 200
            assert production.post("/users-roles/switch-user", data={"user_id": "missing"}).status_code == 403
    finally:
        engine.dispose()
        clear_app_modules()
