from collections.abc import Callable
from contextvars import ContextVar

from fastapi import HTTPException, Request, status
from jinja2 import pass_context
from sqlalchemy.orm import joinedload

from app.audit import reset_audit_context, set_audit_context
from app.config import get_settings
from app.database import SessionLocal
from app.models import Role, RolePermission, User, UserRole
from app.rbac import permission_codes_for_user, role_names_for_user, user_state


USER_COOKIE = "pv_user_id"
CURRENT_USER_ID: ContextVar[str | None] = ContextVar("pv_current_user_id", default=None)
SETTINGS = get_settings()
DEMO_USER_SWITCH_ENABLED = SETTINGS.demo_user_switch_enabled
ALLOW_QUERY_USER_SWITCH = SETTINGS.allow_query_user_switch


def get_request_user_id() -> str | None:
    return CURRENT_USER_ID.get()


def load_user(db, user_id: str | None = None, email: str | None = None) -> User | None:
    query = db.query(User).options(
        joinedload(User.user_roles)
        .joinedload(UserRole.role)
        .joinedload(Role.role_permissions)
        .joinedload(RolePermission.permission)
    )
    query = query.filter(User.is_deleted.is_(False), User.is_active.is_(True))
    if user_id:
        return query.filter(User.id == user_id).first()
    if email:
        return query.filter(User.email == email).first()
    return query.order_by(User.created_at.asc()).first()


async def access_middleware(request: Request, call_next: Callable):
    requested_user = (
        request.query_params.get("as_user")
        if DEMO_USER_SWITCH_ENABLED and ALLOW_QUERY_USER_SWITCH
        else None
    )
    selected_user_id = request.cookies.get(USER_COOKIE) if DEMO_USER_SWITCH_ENABLED else None
    selected_user = None
    user_token = None
    audit_tokens = set_audit_context(
        ip_address=request.client.host if request.client else None,
        correlation_id=request.headers.get("x-request-id"),
    )

    if not request.url.path.startswith("/static"):
        with SessionLocal() as db:
            if requested_user:
                selected_user = load_user(db, requested_user) or load_user(db, email=requested_user)
            if not selected_user and selected_user_id:
                selected_user = load_user(db, selected_user_id)
            if not selected_user:
                selected_user = load_user(db)

            permission_codes = permission_codes_for_user(selected_user)
            demo_switch_available = (
                DEMO_USER_SWITCH_ENABLED and "switch_demo_user" in permission_codes
            )
            request.state.current_user = user_state(selected_user)
            request.state.current_role_names = role_names_for_user(selected_user)
            request.state.permission_codes = permission_codes
            request.state.app_mode = SETTINGS.app_mode
            request.state.demo_user_switch_enabled = demo_switch_available
            request.state.available_users = (
                [
                    user_state(user)
                    for user in db.query(User)
                    .filter(User.is_deleted.is_(False), User.is_active.is_(True))
                    .order_by(User.full_name, User.email)
                    .all()
                ]
                if demo_switch_available
                else []
            )
            user_token = CURRENT_USER_ID.set(selected_user.id if selected_user else None)
    else:
        request.state.current_user = None
        request.state.current_role_names = []
        request.state.permission_codes = set()
        request.state.app_mode = SETTINGS.app_mode
        request.state.demo_user_switch_enabled = DEMO_USER_SWITCH_ENABLED
        request.state.available_users = []
        user_token = CURRENT_USER_ID.set(None)

    try:
        response = await call_next(request)
        if requested_user and request.state.current_user:
            response.set_cookie(
                USER_COOKIE,
                request.state.current_user.id,
                max_age=60 * 60 * 24 * 30,
                samesite="lax",
            )
        return response
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
