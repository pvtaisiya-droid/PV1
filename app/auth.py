import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from jinja2 import pass_context
from sqlalchemy.orm import joinedload, selectinload

from app.audit import reset_audit_context, set_audit_context
from app.config import get_settings
from app.database import SessionLocal
from app.models import AuthSession, Role, RolePermission, User, UserRole, utcnow
from app.rbac import permission_codes_for_user, role_names_for_user, user_state


SESSION_COOKIE = "pv_session"
# Backward-compatible import name used by the development user switch route.
USER_COOKIE = SESSION_COOKIE
CURRENT_USER_ID: ContextVar[str | None] = ContextVar("pv_current_user_id", default=None)
SETTINGS = get_settings()
USER_SWITCH_ENABLED = SETTINGS.user_switch_enabled

LOGIN_PATTERN = re.compile(r"^[\w.-]+$", re.UNICODE)
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
_login_failures: dict[str, list[float]] = {}
_login_failures_lock = threading.Lock()


def get_request_user_id() -> str | None:
    return CURRENT_USER_ID.get()


def normalize_login(value: str) -> str:
    return value.strip().casefold()


def validate_login(value: str) -> str | None:
    normalized = normalize_login(value)
    if not 3 <= len(normalized) <= 64:
        return "Login must contain 3 to 64 characters."
    if not LOGIN_PATTERN.fullmatch(normalized):
        return "Login may contain letters, numbers, dots, underscores and hyphens."
    return None


def validate_password(value: str) -> str | None:
    if len(value) < PASSWORD_MIN_LENGTH:
        return f"Password must contain at least {PASSWORD_MIN_LENGTH} characters."
    if len(value) > PASSWORD_MAX_LENGTH:
        return f"Password must contain no more than {PASSWORD_MAX_LENGTH} characters."
    return None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, encoded_hash: str | None) -> bool:
    if not encoded_hash:
        return False
    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_derived = encoded_hash.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(raw_derived.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def load_user(
    db,
    user_id: str | None = None,
    login: str | None = None,
) -> User | None:
    query = db.query(User).options(
        selectinload(User.user_roles)
        .joinedload(UserRole.role)
        .selectinload(Role.role_permissions)
        .joinedload(RolePermission.permission)
    )
    query = query.filter(User.is_deleted.is_(False), User.is_active.is_(True))
    if user_id:
        return query.filter(User.id == user_id).first()
    if login:
        return query.filter(User.login == normalize_login(login)).first()
    return None


def authenticate_user(db, login: str, password: str) -> User | None:
    user = load_user(db, login=login)
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def create_auth_session(db, user: User) -> str:
    raw_token = secrets.token_urlsafe(48)
    session = AuthSession(
        user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=utcnow() + timedelta(hours=SETTINGS.auth_session_hours),
    )
    db.add(session)
    db.commit()
    return raw_token


def revoke_auth_session(db, raw_token: str | None) -> None:
    if not raw_token:
        return
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    session = (
        db.query(AuthSession)
        .filter(
            AuthSession.token_hash == token_hash,
            AuthSession.is_deleted.is_(False),
        )
        .first()
    )
    if session:
        session.is_active = False
        session.is_deleted = True
        session.deleted_at = utcnow()
        db.commit()


def load_session_user(db, raw_token: str | None) -> User | None:
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    session = (
        db.query(AuthSession)
        .filter(
            AuthSession.token_hash == token_hash,
            AuthSession.is_deleted.is_(False),
            AuthSession.is_active.is_(True),
            AuthSession.expires_at > utcnow(),
        )
        .first()
    )
    if not session:
        return None
    return load_user(db, user_id=session.user_id)


def set_session_cookie(response, raw_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=SETTINGS.auth_session_hours * 60 * 60,
        httponly=True,
        secure=SETTINGS.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=SETTINGS.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def _failure_key(request: Request, login: str) -> str:
    ip_address = request.client.host if request.client else "unknown"
    return f"{ip_address}:{normalize_login(login)}"


def login_is_rate_limited(request: Request, login: str) -> bool:
    key = _failure_key(request, login)
    cutoff = time.monotonic() - LOGIN_FAILURE_WINDOW_SECONDS
    with _login_failures_lock:
        recent = [attempt for attempt in _login_failures.get(key, []) if attempt >= cutoff]
        if recent:
            _login_failures[key] = recent
        else:
            _login_failures.pop(key, None)
        return len(recent) >= LOGIN_FAILURE_LIMIT


def record_login_failure(request: Request, login: str) -> None:
    key = _failure_key(request, login)
    cutoff = time.monotonic() - LOGIN_FAILURE_WINDOW_SECONDS
    with _login_failures_lock:
        recent = [attempt for attempt in _login_failures.get(key, []) if attempt >= cutoff]
        recent.append(time.monotonic())
        _login_failures[key] = recent


def clear_login_failures(request: Request, login: str) -> None:
    with _login_failures_lock:
        _login_failures.pop(_failure_key(request, login), None)


def _is_public_path(path: str) -> bool:
    if path == "/login" or path == "/health" or path.startswith("/static/"):
        return True
    # In production these routes do not exist; let FastAPI return the intended
    # 404 instead of replacing it with the authentication screen.
    return SETTINGS.is_prod and path in {"/docs", "/redoc", "/openapi.json"}


def _unauthenticated_response(request: Request):
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"detail": "Authentication required."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(
        f"/login?{urlencode({'next': target})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def access_middleware(request: Request, call_next: Callable):
    selected_user = None
    user_token = None
    audit_tokens = set_audit_context(
        ip_address=request.client.host if request.client else None,
        correlation_id=request.headers.get("x-request-id"),
    )

    if not request.url.path.startswith("/static/"):
        with SessionLocal() as db:
            selected_user = load_session_user(db, request.cookies.get(SESSION_COOKIE))
            permission_codes = permission_codes_for_user(selected_user)
            user_switch_available = (
                selected_user is not None
                and USER_SWITCH_ENABLED
                and "switch_user" in permission_codes
            )
            request.state.current_user = user_state(selected_user)
            request.state.current_role_names = role_names_for_user(selected_user)
            request.state.permission_codes = permission_codes
            request.state.app_mode = SETTINGS.app_mode
            request.state.user_switch_enabled = user_switch_available
            request.state.available_users = (
                [
                    user_state(user)
                    for user in db.query(User)
                    .filter(User.is_deleted.is_(False), User.is_active.is_(True))
                    .order_by(User.full_name, User.login)
                    .all()
                ]
                if user_switch_available
                else []
            )
            user_token = CURRENT_USER_ID.set(selected_user.id if selected_user else None)
    else:
        request.state.current_user = None
        request.state.current_role_names = []
        request.state.permission_codes = set()
        request.state.app_mode = SETTINGS.app_mode
        request.state.user_switch_enabled = False
        request.state.available_users = []
        user_token = CURRENT_USER_ID.set(None)

    try:
        if not selected_user and not _is_public_path(request.url.path):
            return _unauthenticated_response(request)
        if (request.method in {"GET", "HEAD"}
                and not _is_public_path(request.url.path)
                and "view" not in request.state.permission_codes):
            return JSONResponse({"detail": "Permission required: view"}, status_code=403)
        return await call_next(request)
    finally:
        reset_audit_context(audit_tokens)
        if user_token is not None:
            CURRENT_USER_ID.reset(user_token)


def require_permission(permission_code: str):
    def dependency(request: Request) -> None:
        permissions = getattr(request.state, "permission_codes", set())
        if permission_code not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission_code}",
            )

    return dependency


def require_any_permission(*permission_codes: str):
    def dependency(request: Request) -> None:
        permissions = getattr(request.state, "permission_codes", set())
        if not any(permission_code in permissions for permission_code in permission_codes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {' or '.join(permission_codes)}",
            )

    return dependency


@pass_context
def template_has_permission(context, permission_code: str) -> bool:
    request = context.get("request")
    permissions = getattr(getattr(request, "state", None), "permission_codes", set())
    return permission_code in permissions


@pass_context
def template_current_user(context):
    request = context.get("request")
    return getattr(getattr(request, "state", None), "current_user", None)


@pass_context
def template_current_roles(context) -> list[str]:
    request = context.get("request")
    return getattr(getattr(request, "state", None), "current_role_names", [])


@pass_context
def template_available_users(context) -> list:
    request = context.get("request")
    return getattr(getattr(request, "state", None), "available_users", [])
