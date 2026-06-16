from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app import crud
from app import psmf as psmf_service
from app.auth import require_any_permission, require_permission
from app.database import get_db
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, in_date_range, redirect_with_message, unique_values


router = APIRouter()


def current_user_id(request: Request) -> str | None:
    user = getattr(request.state, "current_user", None)
    return user.id if user else None


def current_actor_name(request: Request) -> str:
    return psmf_service.actor_name(getattr(request.state, "current_user", None))


@router.get("/psmf", response_class=HTMLResponse)
def psmf_page(
    request: Request,
    tab: str = "overview",
    search: str | None = None,
    component_type: str | None = None,
    scope: str | None = None,
    status_filter: str | None = None,
    partner_filter: str | None = None,
    component_id: str | None = None,
    partner_id: str | None = None,
    preview: bool = False,
    audit_module: str | None = "PSMF",
    audit_action: str | None = None,
    audit_date_from: date | None = None,
    audit_date_to: date | None = None,
    db: Session = Depends(get_db),
):
    all_components = psmf_service.list_psmf_components(db)
    components = [
        component
        for component in all_components
        if contains_search(
            search,
            component.code,
            component.title,
            component.description,
            component.component_type,
            component.scope,
            component.status,
            component.partner.partner_name if component.partner else "",
        )
        and (not component_type or component.component_type == component_type)
        and (not scope or component.scope == scope)
        and (not status_filter or component.status == status_filter)
        and (not partner_filter or component.partner_id == partner_filter)
    ]
    selected_component = (
        psmf_service.get_psmf_component(db, component_id) if component_id else None
    )
    selected_version = (
        psmf_service.current_version(selected_component) if selected_component else None
    )

    partner_preview = psmf_service.build_partner_psmf(db, partner_id) if partner_id else None

    all_audit_entries = crud.list_audit_entries(db)
    module_options = unique_values([entry.source_module for entry in all_audit_entries])
    if "PSMF" not in module_options:
        module_options.append("PSMF")
        module_options = sorted(module_options)
    audit_entries = [
        entry
        for entry in all_audit_entries
        if (not audit_module or entry.source_module == audit_module)
        and (not audit_action or entry.action == audit_action)
        and in_date_range(entry.changed_at or entry.timestamp, audit_date_from, audit_date_to)
    ]

    filters = {
        "search": search or "",
        "component_type": component_type or "",
        "scope": scope or "",
        "status_filter": status_filter or "",
        "partner_filter": partner_filter or "",
        "active": active_filters(
            search=search,
            component_type=component_type,
            scope=scope,
            status_filter=status_filter,
            partner_filter=partner_filter,
        ),
    }
    audit_filters = {
        "audit_module": audit_module or "",
        "audit_action": audit_action or "",
        "audit_date_from": audit_date_from or "",
        "audit_date_to": audit_date_to or "",
    }
    return templates.TemplateResponse(
        request,
        "psmf.html",
        {
            "request": request,
            "active_page": "psmf",
            "tab": tab,
            "components": components,
            "all_components": all_components,
            "selected_component": selected_component,
            "selected_version": selected_version,
            "partners": crud.list_partners(db),
            "partner_preview": partner_preview,
            "show_preview": preview,
            "audit_entries": audit_entries,
            "audit_module_options": module_options,
            "audit_action_options": unique_values(
                [entry.action for entry in all_audit_entries if entry.source_module == "PSMF"]
            ),
            "filters": filters,
            "audit_filters": audit_filters,
            "stats": psmf_service.psmf_dashboard_stats(db),
            "type_options": psmf_service.PSMF_COMPONENT_TYPES,
            "scope_options": psmf_service.PSMF_SCOPES,
            "status_options": psmf_service.PSMF_STATUSES,
            "type_labels": psmf_service.PSMF_TYPE_LABELS,
            "scope_labels": psmf_service.PSMF_SCOPE_LABELS,
            "status_labels": psmf_service.PSMF_STATUS_LABELS,
            "action_labels": psmf_service.PSMF_ACTION_LABELS,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/psmf/components/{component_id}/save",
    dependencies=[Depends(require_permission("edit"))],
)
def save_psmf_component(
    component_id: str,
    request: Request,
    content: str = Form(...),
    change_summary: str | None = Form(None),
    db: Session = Depends(get_db),
):
    component = psmf_service.get_psmf_component(db, component_id)
    if not component:
        return redirect_with_message("/psmf?tab=components", error="Компонент МФСФ не найден.")
    if not content.strip():
        return redirect_with_message(
            f"/psmf?tab=components&component_id={component_id}",
            validation="Текст компонента не должен быть пустым.",
        )
    try:
        psmf_service.save_component_draft(
            db,
            component,
            content=content,
            change_summary=change_summary,
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(
            f"/psmf?tab=components&component_id={component_id}",
            error=str(exc),
        )
    return redirect_with_message(
        f"/psmf?tab=components&component_id={component_id}",
        message="Компонент МФСФ сохранен.",
    )


@router.post(
    "/psmf/components/{component_id}/submit",
    dependencies=[Depends(require_permission("edit"))],
)
def submit_psmf_component(
    component_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    component = psmf_service.get_psmf_component(db, component_id)
    if not component:
        return redirect_with_message("/psmf?tab=components", error="Компонент МФСФ не найден.")
    try:
        psmf_service.submit_component_for_review(
            db,
            component,
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(
            f"/psmf?tab=components&component_id={component_id}",
            error=str(exc),
        )
    return redirect_with_message(
        f"/psmf?tab=components&component_id={component_id}",
        message="Компонент отправлен на проверку.",
    )


@router.post(
    "/psmf/components/{component_id}/approve",
    dependencies=[Depends(require_any_permission("approve", "edit"))],
)
def approve_psmf_component(
    component_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    component = psmf_service.get_psmf_component(db, component_id)
    if not component:
        return redirect_with_message("/psmf?tab=components", error="Компонент МФСФ не найден.")
    try:
        psmf_service.approve_component(
            db,
            component,
            approved_by=current_actor_name(request),
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(
            f"/psmf?tab=components&component_id={component_id}",
            error=str(exc),
        )
    return redirect_with_message(
        f"/psmf?tab=components&component_id={component_id}",
        message="Компонент утвержден.",
    )


@router.post(
    "/psmf/components/{component_id}/new-version",
    dependencies=[Depends(require_any_permission("create", "edit"))],
)
def create_psmf_component_version(
    component_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    component = psmf_service.get_psmf_component(db, component_id)
    if not component:
        return redirect_with_message("/psmf?tab=components", error="Компонент МФСФ не найден.")
    try:
        psmf_service.create_new_component_version(
            db,
            component,
            created_by=current_actor_name(request),
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(
            f"/psmf?tab=components&component_id={component_id}",
            error=str(exc),
        )
    return redirect_with_message(
        f"/psmf?tab=components&component_id={component_id}",
        message="Создана новая версия компонента.",
    )


@router.post("/psmf/partner-preview", dependencies=[Depends(require_permission("view"))])
def generate_partner_psmf_preview(
    request: Request,
    partner_id: str = Form(...),
    db: Session = Depends(get_db),
):
    preview = psmf_service.build_partner_psmf(db, partner_id)
    if not preview:
        return redirect_with_message("/psmf?tab=partner", error="Партнер не найден.")
    psmf_service.log_partner_preview(
        db,
        partner=preview["partner"],
        row_count=len(preview["rows"]),
        warning_count=len(preview["warnings"]),
        user_id=current_user_id(request),
    )
    return redirect_with_message(
        f"/psmf?tab=partner&partner_id={partner_id}&preview=true",
        message="Предварительный МФСФ сформирован.",
    )


@router.get("/psmf/partner-preview/download", dependencies=[Depends(require_permission("export"))])
def download_partner_psmf_html(
    request: Request,
    partner_id: str,
    db: Session = Depends(get_db),
):
    preview = psmf_service.build_partner_psmf(db, partner_id)
    if not preview:
        return redirect_with_message("/psmf?tab=partner", error="Партнер не найден.")
    psmf_service.log_partner_export(
        db,
        partner=preview["partner"],
        row_count=len(preview["rows"]),
        user_id=current_user_id(request),
    )
    filename = f"psmf_{preview['partner'].id}.html"
    return Response(
        psmf_service.render_partner_psmf_html(preview),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
