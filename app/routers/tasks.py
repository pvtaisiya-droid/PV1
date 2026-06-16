from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.database import get_db
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, redirect_with_message, unique_values


router = APIRouter()

TASK_STATUS_OPTIONS = [
    "new",
    "in_progress",
    "waiting_for_response",
    "completed",
    "overdue",
    "cancelled",
]
LEGACY_TASK_STATUS_OPTIONS = ["Open", "In Progress", "Completed", "Cancelled"]
TASK_PRIORITY_OPTIONS = ["low", "normal", "high", "critical"]


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    responsible_filter: str | None = None,
    db: Session = Depends(get_db),
):
    all_tasks = crud.list_tasks(db)
    tasks = [
        task
        for task in all_tasks
        if contains_search(
            search,
            task.title,
            task.description,
            task.comment,
            task.related_entity_type,
            task.related_entity_id,
            task.responsible_person,
            task.assigned_to.full_name if task.assigned_to else "",
            task.assigned_to.email if task.assigned_to else "",
        )
        and (not status_filter or task.status == status_filter)
        and (not responsible_filter or task_responsible(task) == responsible_filter)
    ]
    filters = {
        "search": search or "",
        "status_filter": status_filter or "",
        "responsible_filter": responsible_filter or "",
        "active": active_filters(
            search=search,
            status_filter=status_filter,
            responsible_filter=responsible_filter,
        ),
    }
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {
            "request": request,
            "active_page": "tasks",
            "tasks": tasks,
            "users": crud.list_users(db),
            "status_options": TASK_STATUS_OPTIONS,
            "legacy_status_options": LEGACY_TASK_STATUS_OPTIONS,
            "priority_options": TASK_PRIORITY_OPTIONS,
            "responsible_options": unique_values([task_responsible(task) for task in all_tasks]),
            "filters": filters,
            "total_count": len(all_tasks),
            "today": date.today(),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/tasks",
    dependencies=[Depends(require_any_permission("create", "create_related_requests", "comment"))],
)
def create_task_form(
    request: Request,
    title: str = Form(...),
    description: str | None = Form(None),
    status_value: str = Form("new"),
    priority: str = Form("normal"),
    due_date: str | None = Form(None),
    assigned_to_user_id: str | None = Form(None),
    responsible_person: str | None = Form(None),
    related_entity_type: str | None = Form(None),
    related_entity_id: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not title.strip():
        return redirect_with_message("/tasks", validation="Task title is required.")
    assigned_user = crud.get_user(db, assigned_to_user_id) if assigned_to_user_id else None
    current_user = getattr(request.state, "current_user", None)
    crud.create_task(
        db,
        schemas.TaskCreate(
            title=title.strip(),
            description=description,
            status=status_value,
            priority=priority,
            due_date=optional_date(due_date),
            assigned_to_user_id=assigned_to_user_id or None,
            responsible_person=(responsible_person or "").strip()
            or (assigned_user.full_name or assigned_user.email if assigned_user else None),
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
            comment=comment,
        ),
        created_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message("/tasks", message="Task saved.")


@router.post(
    "/tasks/{task_id}/status",
    dependencies=[Depends(require_any_permission("edit", "comment"))],
)
def update_task_status_form(
    task_id: str,
    request: Request,
    status_value: str = Form(...),
    db: Session = Depends(get_db),
):
    task = crud.get_task(db, task_id)
    if not task:
        return redirect_with_message("/tasks", error="Task not found.")
    allowed_statuses = set(TASK_STATUS_OPTIONS + LEGACY_TASK_STATUS_OPTIONS)
    if status_value not in allowed_statuses:
        return redirect_with_message("/tasks", validation="Invalid task status.")
    current_user = getattr(request.state, "current_user", None)
    crud.update_task_status(
        db,
        task,
        status=status_value,
        changed_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message("/tasks", message="Task status saved.")


def task_responsible(task) -> str:
    return (
        task.responsible_person
        or (task.assigned_to.full_name if task.assigned_to else None)
        or (task.assigned_to.email if task.assigned_to else None)
        or ""
    )


def optional_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)
