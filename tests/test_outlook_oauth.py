"""OAuth binding through the real HTTP routes; Microsoft is replaced at transport."""

import base64
import hashlib
import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def oauth_flow(client, monkeypatch, tmp_path):
    from app import crud, outlook_service
    from app.auth import hash_password
    from app.config import get_settings
    from app.database import SessionLocal, engine
    from app.models import User

    # Assert the actual configuration before adding any data or making requests.
    assert engine.url.get_backend_name() == "sqlite"
    from pathlib import Path
    assert Path(engine.url.database).resolve().is_relative_to(tmp_path)
    assert get_settings().storage_root.is_relative_to(tmp_path)
    with SessionLocal() as db:
        assert db.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert db.connection().exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "20261002_0028"
        first_id = db.query(User).filter_by(login="test-admin").one().id
        second_user = crud.create_user(db, "oauth-second", "Second OAuth user", role="admin")
        second_user.password_hash = hash_password("Synthetic-OAuth-2026!")
        db.commit()
        second_id = second_user.id

    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "synthetic-secret")
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "synthetic-tenant")
    monkeypatch.setenv("MICROSOFT_REDIRECT_URI", "http://testserver/outlook/callback")
    monkeypatch.setenv("MICROSOFT_SCOPES", "offline_access User.Read Mail.ReadWrite Mail.Send")
    calls = []

    def exchange(url, **kwargs):
        data = kwargs["data"]
        assert url == "https://login.microsoftonline.com/synthetic-tenant/oauth2/v2.0/token"
        assert data["grant_type"] == "authorization_code"
        assert data["redirect_uri"] == "http://testserver/outlook/callback"
        # Check durable consumption at the transport boundary, before any response.
        with SessionLocal() as db:
            from app.models import OutlookOAuthRequest
            assert not db.query(OutlookOAuthRequest).filter_by(verifier=data["code_verifier"]).count()
        calls.append(dict(data))
        return httpx.Response(200, json={
            "access_token": "synthetic-access-" + data["code"],
            "refresh_token": "synthetic-refresh-" + data["code"],
            "expires_in": 3600,
        })

    monkeypatch.setattr(outlook_service.httpx, "post", exchange)
    outlook_service._TOKEN_CACHE.clear()
    second = TestClient(client.app)
    login = second.post("/login", data={
        "login": "oauth-second", "password": "Synthetic-OAuth-2026!",
    }, follow_redirects=False)
    assert login.status_code == 303
    yield client, second, first_id, second_id, calls
    second.close()
    outlook_service._TOKEN_CACHE.clear()


def begin(browser, target=None):
    params = {"reconciliation_id": target} if target is not None else {}
    response = browser.get("/outlook/auth", params=params, follow_redirects=False)
    assert response.status_code == 307
    url = urlsplit(response.headers["location"])
    assert url.scheme == "https"
    assert url.netloc == "login.microsoftonline.com"
    assert url.path == "/synthetic-tenant/oauth2/v2.0/authorize"
    auth = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert auth["response_type"] == "code"
    assert auth["response_mode"] == "query"
    assert auth["code_challenge_method"] == "S256"
    assert len(auth["state"]) >= 43
    return auth


def callback(browser, state, **params):
    if state is not None:
        params["state"] = state
    return browser.get("/outlook/callback", params=params, follow_redirects=False)


def pending(state):
    from app.database import SessionLocal
    from app.models import OutlookOAuthRequest
    with SessionLocal() as db:
        return db.get(OutlookOAuthRequest, hashlib.sha256(state.encode()).hexdigest())


def assert_success(response, target):
    assert response.status_code == 303
    url = urlsplit(response.headers["location"])
    assert url.path == "/partner-reconciliation"
    params = parse_qs(url.query)
    assert params["message"] == ["Microsoft Outlook authorization saved."]
    assert params.get("reconciliation_id") == ([target] if target is not None else None)


def assert_pkce(auth, exchange):
    verifier = exchange["code_verifier"]
    assert 43 <= len(verifier) <= 128
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == auth["code_challenge"]


def test_two_users_cannot_swap_callbacks_and_authorize_independently(oauth_flow):
    from app import outlook_service
    from app.auth import SESSION_COOKIE
    first, second, first_id, second_id, calls = oauth_flow
    # A query-looking target also verifies exact round-trip encoding.
    first_target, second_target = "first&message=other", "second-target"
    first_auth, second_auth = begin(first, first_target), begin(second, second_target)
    assert first_auth["state"] != second_auth["state"]
    assert first_auth["code_challenge"] != second_auth["code_challenge"]
    assert first_auth["state"] != first_target
    for browser, uid, auth in ((first, first_id, first_auth), (second, second_id, second_auth)):
        row = pending(auth["state"])
        assert row.user_id == uid
        with outlook_service.SessionLocal() as db:
            session = outlook_service._session(db, uid, browser.cookies[SESSION_COOKIE])
            assert row.session_id == session.id

    assert callback(first, second_auth["state"], code="second").status_code == 400
    assert callback(second, first_auth["state"], code="first").status_code == 400
    assert calls == []
    assert outlook_service._TOKEN_CACHE == {}
    assert pending(first_auth["state"]) and pending(second_auth["state"])

    # Complete in reverse order to catch shared/default cache keys.
    assert_success(callback(second, second_auth["state"], code="second"), second_target)
    assert_pkce(second_auth, calls[0])
    assert set(outlook_service._TOKEN_CACHE) == {second_id}
    assert_success(callback(first, first_auth["state"], code="first"), first_target)
    assert_pkce(first_auth, calls[1])
    assert outlook_service.get_access_token(first_id) == "synthetic-access-first"
    assert outlook_service.get_access_token(second_id) == "synthetic-access-second"
    assert set(outlook_service._TOKEN_CACHE) == {first_id, second_id}
    assert not pending(first_auth["state"]) and not pending(second_auth["state"])
    before = {uid: dict(tokens) for uid, tokens in outlook_service._TOKEN_CACHE.items()}
    for browser, auth in ((first, first_auth), (second, second_auth)):
        assert callback(browser, auth["state"], code="replay").status_code == 400
    assert len(calls) == 2
    assert outlook_service._TOKEN_CACHE == before


@pytest.mark.parametrize("invalid", ["missing", "unknown", "expired"])
def test_invalid_state_never_exchanges_or_replaces_existing_tokens(oauth_flow, invalid):
    from app import outlook_service
    from app.database import SessionLocal
    from app.models import OutlookOAuthRequest, utcnow
    first, _, uid, _, calls = oauth_flow
    assert_success(callback(first, begin(first)["state"], code="original"), None)
    before = dict(outlook_service._TOKEN_CACHE[uid])
    state = {"missing": None, "unknown": "unissued-state"}.get(invalid)
    if invalid == "expired":
        state = begin(first)["state"]
        with SessionLocal() as db:
            row = db.get(OutlookOAuthRequest, hashlib.sha256(state.encode()).hexdigest())
            row.expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
    assert callback(first, state, code="attacker").status_code == 400
    assert len(calls) == 1
    assert outlook_service._TOKEN_CACHE[uid] == before


def test_new_session_of_same_user_cannot_use_old_session_state(oauth_flow):
    from app.auth import SESSION_COOKIE
    from tests.conftest import TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD
    first, _, _, _, calls = oauth_flow
    auth = begin(first)
    original_cookie = first.cookies[SESSION_COOKIE]
    with TestClient(first.app) as other:
        response = other.post("/login", data={"login": TEST_ADMIN_LOGIN, "password": TEST_ADMIN_PASSWORD}, follow_redirects=False)
        assert response.status_code == 303
        assert other.cookies[SESSION_COOKIE] != original_cookie
        assert callback(other, auth["state"], code="other-session").status_code == 400
    assert calls == []
    assert pending(auth["state"])
    assert_success(callback(first, auth["state"], code="original-session"), None)


@pytest.mark.parametrize("invalid_session", ["logout", "expired", "disabled_user"])
def test_invalid_app_session_cannot_start_or_finish_oauth(oauth_flow, invalid_session):
    from app.auth import SESSION_COOKIE
    from app.database import SessionLocal
    from app.models import AuthSession, User, utcnow
    first, _, uid, _, calls = oauth_flow
    auth = begin(first)
    if invalid_session == "logout":
        assert first.post("/logout", follow_redirects=False).status_code == 303
    else:
        with SessionLocal() as db:
            if invalid_session == "expired":
                session = db.query(AuthSession).filter_by(token_hash=hashlib.sha256(first.cookies[SESSION_COOKIE].encode()).hexdigest()).one()
                session.expires_at = utcnow() - timedelta(seconds=1)
            else:
                db.get(User, uid).is_active = False
            db.commit()
    for path in ("/outlook/auth", "/outlook/callback"):
        response = first.get(path, params={"state": auth["state"], "code": "denied"}, follow_redirects=False)
        assert response.status_code == 303
        assert urlsplit(response.headers["location"]).path == "/login"
    assert calls == []
    assert pending(auth["state"])


def test_permission_revocation_blocks_both_routes_without_consuming_state(oauth_flow):
    from app import crud
    from app.database import SessionLocal
    from app.models import Permission, Role
    first, second, _, _, calls = oauth_flow
    auth = begin(first)
    with SessionLocal() as db:
        role = db.query(Role).filter_by(role_code="admin").one()
        view = db.query(Permission).filter_by(permission_code="view").one()
        original = [link.permission_id for link in role.role_permissions if link.is_active and not link.is_deleted]
        crud.update_role_permissions(db, role_id=role.id, permission_ids=[view.id])
        role_id = role.id
    for browser in (first, second):
        assert browser.get("/outlook/auth", follow_redirects=False).status_code == 403
        assert callback(browser, auth["state"], code="denied").status_code == 403
    assert calls == []
    assert pending(auth["state"])
    with SessionLocal() as db:
        crud.update_role_permissions(db, role_id=role_id, permission_ids=original)
    assert_success(callback(first, auth["state"], code="allowed"), None)


@pytest.mark.parametrize("params", [
    {},
    {"error": "access_denied"},
    {"error": "access_denied", "error_description": "Synthetic denial", "code": "must-not-exchange"},
])
def test_provider_error_or_missing_code_consumes_state_without_network(oauth_flow, params):
    from app import outlook_service
    first, _, _, _, calls = oauth_flow
    state = begin(first)["state"]
    assert callback(first, state, **params).status_code == 400
    assert not pending(state)
    assert callback(first, state, code="replay").status_code == 400
    assert calls == []
    assert outlook_service._TOKEN_CACHE == {}


@pytest.mark.parametrize("failure", ["provider", "transport"])
def test_failed_exchange_preserves_old_account_and_cannot_be_retried(oauth_flow, monkeypatch, failure):
    from app import outlook_service
    first, _, uid, _, calls = oauth_flow
    assert_success(callback(first, begin(first)["state"], code="original"), None)
    before = dict(outlook_service._TOKEN_CACHE[uid])
    state = begin(first)["state"]
    attempts = []

    def fail(url, **kwargs):
        assert not pending(state)
        attempts.append(url)
        if failure == "provider":
            return httpx.Response(400, json={"error": "invalid_grant"})
        raise httpx.ConnectTimeout("Synthetic transport failure")

    monkeypatch.setattr(outlook_service.httpx, "post", fail)
    response = callback(first, state, code="new-account")
    assert response.status_code == 400
    if failure == "transport":
        assert "Synthetic transport failure" not in response.text
    assert callback(first, state, code="replay").status_code == 400
    assert len(attempts) == 1
    assert len(calls) == 1
    assert outlook_service._TOKEN_CACHE[uid] == before


def test_pending_request_survives_service_reload(oauth_flow):
    from app import outlook_service
    first, _, _, _, calls = oauth_flow
    auth = begin(first, "persistent-target")
    importlib.reload(outlook_service)
    assert_success(callback(first, auth["state"], code="after-reload"), "persistent-target")
    assert_pkce(auth, calls[0])
    assert not pending(auth["state"])


def test_parallel_replay_is_rejected_while_first_exchange_is_pending(oauth_flow, monkeypatch):
    from app import outlook_service
    first, _, uid, _, calls = oauth_flow
    state = begin(first)["state"]
    entered, release = Event(), Event()
    exchange = outlook_service.httpx.post

    def delayed(url, **kwargs):
        assert not pending(state)
        entered.set()
        assert release.wait(10), "Test exchange was not released"
        return exchange(url, **kwargs)

    monkeypatch.setattr(outlook_service.httpx, "post", delayed)
    # Independent HTTP clients send the same application session cookie.
    with TestClient(first.app) as replay, ThreadPoolExecutor(max_workers=1) as pool:
        replay.cookies.update(first.cookies)
        request = pool.submit(callback, first, state, code="winner")
        try:
            assert entered.wait(10), "First callback did not reach transport"
            assert callback(replay, state, code="contender").status_code == 400
            assert calls == []
            assert uid not in outlook_service._TOKEN_CACHE
        finally:
            release.set()
        assert_success(request.result(timeout=10), None)
    assert len(calls) == 1
    assert outlook_service.get_access_token(uid) == "synthetic-access-winner"
