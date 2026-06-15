from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import USER_COOKIE, require_permission
from app.database import get_db
from app.models import Role, User
from app.templating import templates


router = APIRouter()


def current_user_id(request: Request) -> str | None:
    user = getattr(request.state, "current_user", None)
    return user.id if user else None


def redirect_with_message(message: str | None = None, error: str | None = None) -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/users-roles{suffix}", status_code=status.HTTP_303_SEE_OTHER)


def safe_next_url(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


@router.post("/users-roles/switch-user")
def switch_user(
    user_id: str = Form(...),
    next_url: str = Form("/"),
    db: Session = Depends(get_db),
):
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    response = RedirectResponse(
        safe_next_url(next_url),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        USER_COOKIE,
        user.id,
        max_age=60 * 60 * 24 * 30,
        samesite="lax",
    )
    return response


@router.get(
    "/users-roles",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("manage_users"))],
)
def users_roles_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "users_roles.html",
        {
            "request": request,
            "active_page": "users_roles",
            "users": crud.list_users(db),
            "roles": crud.list_roles(db),
            "permissions": crud.list_permissions(db),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.post(
    "/users-roles/users",
    dependencies=[Depends(require_permission("manage_users"))],
)
def create_user_form(
    request: Request,
    email: str = Form(...),
    full_name: str | None = Form(None),
    role_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.email == email).first():
        return redirect_with_message(error="User already exists.")
    if not role_ids:
        readonly_role = db.query(Role).filter(Role.role_code == "readonly_auditor").first()
        role_ids = [readonly_role.id] if readonly_role else []
    try:
        crud.create_app_user(
            db,
            email=email,
            full_name=full_name,
            role_ids=role_ids,
            created_by_user_id=current_user_id(request),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message(error="User could not be created.")
    return redirect_with_message(message="User created.")


@router.post(
    "/users-roles/users/{user_id}/roles",
    dependencies=[Depends(require_permission("manage_users"))],
)
def assign_role_form(
    request: Request,
    user_id: str,
    role_id: str = Form(...),
    db: Session = Depends(get_db),
):
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
def remove_role_form(
    request: Request,
    user_id: str,
    role_id: str,
    db: Session = Depends(get_db),
):
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
    dependencies=[Depends(require_permission("soft_delete"))],
)
def archive_user_form(
    request: Request,
    user_id: str,
    delete_reason: str | None = Form(None),
    db: Session = Depends(get_db),
):
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
def update_role_permissions_form(
    request: Request,
    role_id: str,
    permission_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
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
