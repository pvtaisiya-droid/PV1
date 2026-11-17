from app import models
from app.concurrency import protect_record
from app.unit_of_work import atomic_write
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import (
    create_auth_session,
    normalize_login,
    require_permission,
    set_session_cookie,
    validate_login,
    validate_password,
)
from app.config import get_settings
from app.database import get_db
from app.models import Role, User
from app.templating import templates
from app.ui_helpers import active_filters, contains_search


router = APIRouter()


def current_user_id(request: Request) -> str | None:
    user = getattr(request.state, "current_user", None)
    return user.id if user else None


def redirect_with_message(
    message: str | None = None,
    error: str | None = None,
    validation: str | None = None,
) -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    if validation:
        params["validation"] = validation
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/users-roles{suffix}", status_code=status.HTTP_303_SEE_OTHER)


def safe_next_url(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


@router.post("/users-roles/switch-user", dependencies=[Depends(require_permission("switch_user"))])
def switch_user(
    request: Request,
    user_id: str = Form(...),
    next_url: str = Form("/"),
    db: Session = Depends(get_db),
):
    if not get_settings().user_switch_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User switch is disabled.",
        )
    permissions = getattr(request.state, "permission_codes", set())
    if "switch_user" not in permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission required: user switch",
        )
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    response = RedirectResponse(
        safe_next_url(next_url),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    raw_token = create_auth_session(db, user)
    set_session_cookie(response, raw_token)
    return response


@router.get(
    "/users-roles",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("manage_users"))],
)
def users_roles_page(request: Request, db: Session = Depends(get_db)):
    search = request.query_params.get("search")
    role_id = request.query_params.get("role_id")
    status_filter = request.query_params.get("status_filter")
    base_users = crud.list_users(db, include_deleted=status_filter == "archived")
    users = [
        user
        for user in base_users
        if contains_search(
            search,
            user.login,
            user.full_name,
            ", ".join(
                user_role.role.role_name
                for user_role in user.user_roles
                if not user_role.is_deleted and user_role.role
            ),
            ", ".join(
                user_role.role.role_code
                for user_role in user.user_roles
                if not user_role.is_deleted and user_role.role
            ),
        )
        and (
            not role_id
            or any(
                user_role.role_id == role_id and not user_role.is_deleted
                for user_role in user.user_roles
            )
        )
        and (
            not status_filter
            or (status_filter == "active" and user.is_active and not user.is_deleted)
            or (status_filter == "inactive" and not user.is_active and not user.is_deleted)
            or (status_filter == "archived" and user.is_deleted)
        )
    ]
    filters = {
        "search": search or "",
        "role_id": role_id or "",
        "status_filter": status_filter or "",
        "active": active_filters(search=search, role_id=role_id, status_filter=status_filter),
    }
    all_documents = crud.list_attachments(db)
    return templates.TemplateResponse(
        request,
        "users_roles.html",
        {
            "request": request,
            "active_page": "users_roles",
            "users": users,
            "roles": crud.list_roles(db),
            "permissions": crud.list_permissions(db),
            "filters": filters,
            "total_count": len(base_users),
            "documents_by_user": {
                user.id: [
                    document
                    for document in all_documents
                    if document.related_object_type == "Employee"
                    and document.related_object_id == user.id
                ]
                for user in users
            },
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/users-roles/users",
    dependencies=[Depends(require_permission("manage_users"))],
)
@atomic_write
def create_user_form(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    full_name: str | None = Form(None),
    role_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    login_error = validate_login(login)
    if login_error:
        return redirect_with_message(validation=login_error)
    password_error = validate_password(password)
    if password_error:
        return redirect_with_message(validation=password_error)
    normalized_login = normalize_login(login)
    if (
        db.query(User)
        .filter((User.login == normalized_login) | (User.email == normalized_login))
        .first()
    ):
        return redirect_with_message(error="User already exists.")
    if not role_ids:
        readonly_role = db.query(Role).filter(Role.role_code == "readonly_auditor").first()
        role_ids = [readonly_role.id] if readonly_role else []
    try:
        crud.create_app_user(
            db,
            login=normalized_login,
            password=password,
            full_name=full_name,
            role_ids=role_ids,
            created_by_user_id=current_user_id(request),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message(error="User could not be created.")
    return redirect_with_message(message="User created.")


@router.post(
    "/users-roles/users/{user_id}/edit",
    dependencies=[Depends(require_permission("manage_users"))],
)
@atomic_write
def edit_user_form(
    request: Request,
    user_id: str,
    login: str = Form(...),
    full_name: str | None = Form(None),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.User, user_id, expected_version)
    user = crud.get_user(db, user_id)
    if not user:
        return redirect_with_message(error="User not found.")
    login_error = validate_login(login)
    if login_error:
        return redirect_with_message(validation=login_error)
    normalized_login = normalize_login(login)
    existing = (
        db.query(User)
        .filter(
            (User.login == normalized_login) | (User.email == normalized_login),
            User.id != user_id,
        )
        .first()
    )
    if existing:
        return redirect_with_message(error="User already exists.")
    try:
        crud.update_app_user(
            db,
            user,
            login=normalized_login,
            full_name=full_name,
            changed_by_user_id=current_user_id(request),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message(error="User could not be saved.")
    return redirect_with_message(message="User saved.")


@router.post(
    "/users-roles/users/{user_id}/password",
    dependencies=[Depends(require_permission("manage_users"))],
)
@atomic_write
def reset_user_password_form(
    request: Request,
    user_id: str,
    password: str = Form(...),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.User, user_id, expected_version)
    user = crud.get_user(db, user_id)
    if not user:
        return redirect_with_message(error="User not found.")
    password_error = validate_password(password)
    if password_error:
        return redirect_with_message(validation=password_error)
    crud.set_user_password(
        db,
        user,
        password=password,
        changed_by_user_id=current_user_id(request),
    )
    return redirect_with_message(message="Password changed.")


@router.post(
    "/users-roles/users/{user_id}/roles",
    dependencies=[Depends(require_permission("manage_users"))],
)
@atomic_write
def assign_role_form(
    request: Request,
    user_id: str,
    role_id: str = Form(...),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.User, user_id, expected_version)
    if not crud.get_user(db, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    if not crud.get_role(db, role_id):
        raise HTTPException(status_code=404, detail="Role not found")
    crud.assign_user_role(
        db,
        user_id=user_id,
        role_id=role_id,
        assigned_by_user_id=current_user_id(request),
    )
    return redirect_with_message(message="Role assigned.")


@router.post(
    "/users-roles/users/{user_id}/roles/{role_id}/remove",
    dependencies=[Depends(require_permission("manage_users"))],
)
@atomic_write
def remove_role_form(
    request: Request,
    user_id: str,
    role_id: str,
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.User, user_id, expected_version)
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    active_roles = [user_role for user_role in user.user_roles if not user_role.is_deleted]
    if len(active_roles) <= 1:
        return redirect_with_message(error="User must keep at least one role.")
    crud.remove_user_role(
        db,
        user_id=user_id,
        role_id=role_id,
        removed_by_user_id=current_user_id(request),
    )
    return redirect_with_message(message="Role removed.")


@router.post(
    "/users-roles/users/{user_id}/archive",
    dependencies=[Depends(require_permission("manage_users")), Depends(require_permission("soft_delete"))],
)
@atomic_write
def archive_user_form(
    request: Request,
    user_id: str,
    delete_reason: str | None = Form(None),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.User, user_id, expected_version)
    if user_id == current_user_id(request):
        return redirect_with_message(error="Current user cannot archive self.")
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    crud.archive_user(
        db,
        user,
        deleted_by_user_id=current_user_id(request),
        delete_reason=delete_reason,
    )
    return redirect_with_message(message="User archived.")


@router.post(
    "/users-roles/roles/{role_id}/permissions",
    dependencies=[Depends(require_permission("manage_users"))],
)
@atomic_write
def update_role_permissions_form(
    request: Request,
    role_id: str,
    permission_ids: list[str] = Form(default=[]),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Role, role_id, expected_version)
    role = crud.get_role(db, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.role_code == "admin":
        permission_ids = [permission.id for permission in crud.list_permissions(db)]
    crud.update_role_permissions(
        db,
        role_id=role_id,
        permission_ids=permission_ids,
        changed_by_user_id=current_user_id(request),
    )
    return redirect_with_message(message="Role permissions updated.")


@router.get(
    "/api/users",
    response_model=list[schemas.UserRead],
    dependencies=[Depends(require_permission("manage_users"))],
)
def api_list_users(db: Session = Depends(get_db)):
    return crud.list_users(db)


@router.get(
    "/api/roles",
    response_model=list[schemas.RoleRead],
    dependencies=[Depends(require_permission("manage_users"))],
)
def api_list_roles(db: Session = Depends(get_db)):
    return crud.list_roles(db)


@router.get(
    "/api/permissions",
    response_model=list[schemas.PermissionRead],
    dependencies=[Depends(require_permission("manage_users"))],
)
def api_list_permissions(db: Session = Depends(get_db)):
    return crud.list_permissions(db)
