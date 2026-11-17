from app.concurrency import snapshot_token, require_snapshot, PSMF_INPUTS
from app import models
from app.concurrency import protect_record
from app.unit_of_work import atomic_write
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy.orm import Session

from app import crud
from app import psmf as psmf_service
from app.auth import require_any_permission, require_permission
from app.concurrency import require_version
from sqlalchemy.orm.exc import StaleDataError
from app.database import get_db
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, in_date_range, redirect_with_message, unique_values


router = APIRouter()


def require_text_versions(record, text_version, expected_version, expected_content_version):
    require_version(record, expected_version)
    if text_version is None:
        raise StaleDataError("Text version no longer exists. Reload before saving.")
    require_version(text_version, expected_content_version)


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
    component_filter: str | None = None,
    block_type: str | None = None,
    warning_level: str | None = None,
    component_id: str | None = None,
    root_component_id: str | None = None,
    block_id: str | None = None,
    version_id: str | None = None,
    block_version_id: str | None = None,
    partner_id: str | None = None,
    preview: bool = False,
    audit_action: str | None = None,
    audit_date_from: date | None = None,
    audit_date_to: date | None = None,
    db: Session = Depends(get_db),
):
    requested_tab = tab
    if tab == "audit":
        require_permission("audit_view")(request)
    if tab in {"components", "blocks", "structure"}:
        tab = "chapters"

    all_components = psmf_service.list_psmf_components(db)
    all_blocks = psmf_service.list_psmf_blocks(db)
    warnings = psmf_service.collect_psmf_warnings(db, partner_id=partner_id)
    if warning_level:
        warnings = [warning for warning in warnings if warning["level"] == warning_level]

    components = [
        component
        for component in all_components
        if contains_search(
            search,
            component.code,
            component.title,
            component.title_ru,
            component.original_code,
            component.original_title,
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
    blocks = [
        block
        for block in all_blocks
        if contains_search(
            search,
            block.code,
            block.title_ru,
            block.original_code,
            block.original_title,
            block.block_type,
            block.status,
            block.component.code if block.component else "",
            psmf_service.display_title(block.component) if block.component else "",
        )
        and (not block_type or block.block_type == block_type)
        and (not status_filter or block.status == status_filter)
        and (not partner_filter or block.partner_id == partner_filter)
        and (not component_filter or block.component_id == component_filter)
    ]
    structure_rows = psmf_service.psmf_structure_rows(all_components)
    annex_container = next(
        (
            row["component"]
            for row in structure_rows
            if row["component"].parent_component_id is None and row["component"].code == "9"
        ),
        None,
    )
    top_components = [
        row["component"]
        for row in structure_rows
        if row["component"].parent_component_id is None and row["component"].code != "9"
    ]
    annex_top_components = [
        row["component"]
        for row in structure_rows
        if annex_container and row["component"].parent_component_id == annex_container.id
    ]
    selected_root_from_param = (
        psmf_service.get_psmf_component(db, root_component_id)
        if root_component_id
        else None
    )
    selected_block = psmf_service.get_psmf_block(db, block_id) if block_id else None
    selected_component = (
        psmf_service.get_psmf_component(db, component_id) if component_id else None
    )
    if not selected_component and component_filter:
        selected_component = psmf_service.get_psmf_component(db, component_filter)
    if not selected_component and selected_block:
        selected_component = selected_block.component
    if not selected_component and selected_root_from_param:
        selected_component = selected_root_from_param

    def is_descendant_of(component, root_component) -> bool:
        parent = component.parent
        while parent:
            if root_component and parent.id == root_component.id:
                return True
            parent = parent.parent
        return False

    partner_annex_components = [
        row["component"]
        for row in structure_rows
        if annex_container
        and row["component"].id != annex_container.id
        and is_descendant_of(row["component"], annex_container)
        and psmf_service.is_partner_specific_component(row["component"])
    ]

    if (
        tab == "chapters"
        and selected_component
        and annex_container
        and (
            selected_component.id == annex_container.id
            or is_descendant_of(selected_component, annex_container)
        )
    ):
        tab = "annexes"
    if tab == "annexes" and selected_component and not psmf_service.has_common_annex_content(selected_component):
        selected_component = None
    if tab == "partner_annexes" and selected_component and not psmf_service.has_partner_specific_content(selected_component):
        selected_component = None
    if tab == "annexes" and not selected_component:
        selected_component = next(
            (
                component
                for component in annex_top_components
                if psmf_service.has_common_annex_content(component)
            ),
            next(iter(annex_top_components), annex_container),
        )
    if tab == "partner_annexes" and not selected_component:
        selected_component = next(
            (
                component
                for component in annex_top_components
                if psmf_service.has_partner_specific_content(component)
            ),
            None,
        )
    if tab == "chapters" and not selected_component:
        selected_component = next(iter(top_components), None)

    selected_root_component = selected_component
    while selected_root_component and selected_root_component.parent_component_id:
        if tab in {"annexes", "partner_annexes"} and selected_root_component.parent_component_id == (annex_container.id if annex_container else None):
            break
        selected_root_component = selected_root_component.parent
    if selected_root_from_param and selected_component == selected_root_from_param:
        selected_root_component = selected_root_from_param
    selected_root_depth = next(
        (
            row["depth"]
            for row in structure_rows
            if selected_root_component and row["component"].id == selected_root_component.id
        ),
        0,
    )

    visible_structure_rows = [
        {
            **row,
            "relative_depth": max(row["depth"] - selected_root_depth - 1, 0),
        }
        for row in structure_rows
        if selected_root_component
        and row["component"].id != selected_root_component.id
        and is_descendant_of(row["component"], selected_root_component)
    ]
    structure_top_components = annex_top_components if tab == "annexes" else top_components
    structure_title = "Приложения" if tab == "annexes" else "Главы МФСФ"
    structure_hint = (
        "Выберите группу приложений; слева откроются приложения внутри нее."
        if tab == "annexes"
        else "Выберите крупный раздел; слева откроются его подразделы."
    )
    structure_root_label = "Группа приложений" if tab == "annexes" else "Крупный раздел"
    if tab == "annexes":
        visible_structure_rows = [
            row for row in visible_structure_rows if psmf_service.has_common_annex_content(row["component"])
        ]
        structure_top_components = [
            component
            for component in annex_top_components
            if psmf_service.has_common_annex_content(component)
        ]
        structure_title = "Общие приложения"
        structure_hint = "Выберите группу общих приложений; партнерские блоки скрыты в отдельной вкладке."
        structure_root_label = "Группа приложений"
    elif tab == "partner_annexes":
        visible_structure_rows = [
            row for row in visible_structure_rows if psmf_service.is_partner_specific_component(row["component"])
        ]
        structure_top_components = [
            component
            for component in annex_top_components
            if psmf_service.has_partner_specific_content(component)
        ]
        structure_title = "Приложения партнеров"
        structure_hint = "Выберите партнера и группу приложений; ниже будут только партнерские блоки и автотаблицы."
        structure_root_label = "Группа приложений"

    if tab == "partner_annexes":
        visible_structure_rows = []
        structure_top_components = partner_annex_components
        structure_hint = "Выберите партнера и приложение; ниже будут только партнерские блоки и автотаблицы."
        structure_root_label = "Приложение"

    if tab == "partner_annexes":
        visible_structure_rows = [
            {
                **row,
                "relative_depth": max(row["depth"] - selected_root_depth - 1, 0),
            }
            for row in structure_rows
            if selected_root_component
            and row["component"].id != selected_root_component.id
            and is_descendant_of(row["component"], selected_root_component)
            and psmf_service.is_partner_specific_component(row["component"])
        ]
        structure_top_components = [
            component
            for component in annex_top_components
            if psmf_service.has_partner_specific_content(component)
        ]
        structure_root_label = "Группа приложений"

    current_component_version = (
        psmf_service.current_version(selected_component) if selected_component else None
    )
    selected_version = current_component_version
    if version_id and selected_component:
        selected_version = next(
            (
                version
                for version in selected_component.versions
                if version.id == version_id and not version.is_deleted
            ),
            current_component_version,
        )
    selected_version_is_archive = bool(
        selected_version
        and current_component_version
        and selected_version.id != current_component_version.id
    )
    current_selected_block_version = (
        psmf_service.current_block_version(selected_block) if selected_block else None
    )
    selected_block_version = current_selected_block_version
    if block_version_id and selected_block:
        selected_block_version = next(
            (
                version
                for version in selected_block.versions
                if version.id == block_version_id
            ),
            current_selected_block_version,
        )
    selected_block_version_is_archive = bool(
        selected_block_version
        and current_selected_block_version
        and selected_block_version.id != current_selected_block_version.id
    )

    partner_preview = psmf_service.build_partner_psmf(db, partner_id) if partner_id else None
    partner_builds = psmf_service.list_partner_builds(db, partner_id=partner_id)
    latest_partner_build = partner_builds[0] if partner_builds else None
    build_card_documents = [
        document
        for document in crud.list_attachments(db)
        if latest_partner_build
        and document.related_object_type == "PSMFBuild"
        and document.related_object_id == latest_partner_build.id
    ]
    inspection_package = psmf_service.inspection_package(db, partner_id=partner_id)

    all_audit_entries = (
        crud.list_audit_entries(db)
        if "audit_view" in request.state.permission_codes else []
    )
    audit_entries = [
        entry
        for entry in all_audit_entries
        if entry.source_module == "PSMF"
        and (not audit_action or entry.action == audit_action)
        and in_date_range(entry.changed_at or entry.timestamp, audit_date_from, audit_date_to)
    ]

    filters = {
        "search": search or "",
        "component_type": component_type or "",
        "scope": scope or "",
        "status_filter": status_filter or "",
        "partner_filter": partner_filter or "",
        "component_filter": component_filter or "",
        "block_type": block_type or "",
        "warning_level": warning_level or "",
        "active": active_filters(
            search=search,
            component_type=component_type,
            scope=scope,
            status_filter=status_filter,
            partner_filter=partner_filter,
            component_filter=component_filter,
            block_type=block_type,
            warning_level=warning_level,
        ),
    }
    audit_filters = {
        "audit_action": audit_action or "",
        "audit_date_from": audit_date_from or "",
        "audit_date_to": audit_date_to or "",
    }
    return templates.TemplateResponse(
        request,
        "psmf.html",
        {
            "component_current_version": psmf_service.current_version,
            "block_current_version": psmf_service.current_block_version,
            "request": request,
            "expected_snapshot": snapshot_token(db, PSMF_INPUTS),
            "active_page": "psmf",
            "tab": tab,
            "components": components,
            "all_components": all_components,
            "blocks": blocks,
            "selected_component_blocks": [
                block
                for block in (selected_component.blocks if selected_component else [])
                if not block.is_deleted
                and (
                    (tab == "partner_annexes" and psmf_service.is_partner_specific_block(block))
                    or (tab == "annexes" and not psmf_service.is_partner_specific_block(block))
                    or tab not in {"annexes", "partner_annexes"}
                    or requested_tab in {"components", "blocks", "structure", "chapters"}
                )
            ],
            "all_blocks": all_blocks,
            "structure_rows": structure_rows,
            "top_components": top_components,
            "annex_top_components": annex_top_components,
            "structure_top_components": structure_top_components,
            "structure_title": structure_title,
            "structure_hint": structure_hint,
            "structure_root_label": structure_root_label,
            "selected_root_component": selected_root_component,
            "visible_structure_rows": visible_structure_rows,
            "selected_component": selected_component,
            "selected_version": selected_version,
            "current_component_version": current_component_version,
            "selected_version_is_archive": selected_version_is_archive,
            "selected_block": selected_block,
            "selected_block_version": selected_block_version,
            "current_selected_block_version": current_selected_block_version,
            "selected_block_version_is_archive": selected_block_version_is_archive,
            "partners": crud.list_partners(db),
            "partner_preview": partner_preview,
            "partner_builds": partner_builds,
            "latest_partner_build": latest_partner_build,
            "build_card_documents": build_card_documents,
            "inspection_package": inspection_package,
            "show_preview": preview,
            "warnings": warnings,
            "audit_entries": audit_entries,
            "audit_action_options": unique_values(
                [entry.action for entry in all_audit_entries if entry.source_module == "PSMF"]
            ),
            "filters": filters,
            "audit_filters": audit_filters,
            "stats": psmf_service.psmf_dashboard_stats(db),
            "settings": psmf_service.get_psmf_settings(db),
            "type_options": psmf_service.PSMF_COMPONENT_TYPES,
            "scope_options": psmf_service.PSMF_SCOPES,
            "status_options": psmf_service.PSMF_STATUSES,
            "block_type_options": psmf_service.PSMF_BLOCK_TYPES,
            "warning_level_options": psmf_service.PSMF_WARNING_LEVELS,
            "type_labels": psmf_service.PSMF_TYPE_LABELS,
            "scope_labels": psmf_service.PSMF_SCOPE_LABELS,
            "status_labels": psmf_service.PSMF_STATUS_LABELS,
            "block_type_labels": psmf_service.PSMF_BLOCK_TYPE_LABELS,
            "warning_labels": psmf_service.PSMF_WARNING_LABELS,
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
    expected_version: int = Form(..., ge=1),
    expected_content_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    component = psmf_service.get_psmf_component(db, component_id)
    if not component:
        return redirect_with_message("/psmf?tab=chapters", error="Глава МФСФ не найдена.")
    require_text_versions(component, psmf_service.current_version(component),
                          expected_version, expected_content_version)
    if not content.strip():
        return redirect_with_message(
            f"/psmf?tab=chapters&component_id={component_id}",
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
            f"/psmf?tab=chapters&component_id={component_id}",
            error=str(exc),
        )
    return redirect_with_message(
        f"/psmf?tab=chapters&component_id={component_id}",
        message="Компонент МФСФ сохранен.",
    )


@router.post(
    "/psmf/components/{component_id}/submit",
    dependencies=[Depends(require_permission("edit"))],
)
def submit_psmf_component(
    component_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    expected_content_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    component = psmf_service.get_psmf_component(db, component_id)
    if not component:
        return redirect_with_message("/psmf?tab=chapters", error="Глава МФСФ не найдена.")
    require_text_versions(component, psmf_service.current_version(component),
                          expected_version, expected_content_version)
    try:
        psmf_service.submit_component_for_review(
            db,
            component,
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(
            f"/psmf?tab=chapters&component_id={component_id}",
            error=str(exc),
        )
    return redirect_with_message(
        f"/psmf?tab=chapters&component_id={component_id}",
        message="Компонент отправлен на проверку.",
    )


@router.post(
    "/psmf/components/{component_id}/approve",
    dependencies=[Depends(require_any_permission("approve", "edit"))],
)
def approve_psmf_component(
    component_id: str,
    request: Request,
    approval_comment: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    expected_content_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    component = psmf_service.get_psmf_component(db, component_id)
    if not component:
        return redirect_with_message("/psmf?tab=chapters", error="Глава МФСФ не найдена.")
    require_text_versions(component, psmf_service.current_version(component),
                          expected_version, expected_content_version)
    try:
        psmf_service.approve_component(
            db,
            component,
            approved_by=current_actor_name(request),
            approval_comment=approval_comment,
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(
            f"/psmf?tab=chapters&component_id={component_id}",
            error=str(exc),
        )
    return redirect_with_message(
        f"/psmf?tab=chapters&component_id={component_id}",
        message="Компонент утвержден.",
    )


@router.post(
    "/psmf/components/{component_id}/return-revision",
    dependencies=[Depends(require_any_permission("approve", "edit"))],
)
def return_psmf_component_for_revision(
    component_id: str,
    request: Request,
    return_comment: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    expected_content_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    component = psmf_service.get_psmf_component(db, component_id)
    if not component:
        return redirect_with_message("/psmf?tab=approval", error="Глава МФСФ не найдена.")
    require_text_versions(component, psmf_service.current_version(component),
                          expected_version, expected_content_version)
    try:
        psmf_service.return_component_for_revision(
            db,
            component,
            returned_by=current_actor_name(request),
            return_comment=return_comment,
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/psmf?tab=approval", error=str(exc))
    return redirect_with_message("/psmf?tab=approval", message="Глава возвращена на доработку.")


@router.post(
    "/psmf/components/{component_id}/new-version",
    dependencies=[Depends(require_any_permission("create", "edit"))],
)
def create_psmf_component_version(
    component_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    expected_content_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    component = psmf_service.get_psmf_component(db, component_id)
    if not component:
        return redirect_with_message("/psmf?tab=chapters", error="Глава МФСФ не найдена.")
    require_text_versions(component, psmf_service.current_version(component),
                          expected_version, expected_content_version)
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
            f"/psmf?tab=chapters&component_id={component_id}",
            error=str(exc),
        )
    return redirect_with_message(
        f"/psmf?tab=chapters&component_id={component_id}",
        message="Создана новая версия компонента.",
    )


@router.post(
    "/psmf/blocks/{block_id}/save",
    dependencies=[Depends(require_permission("edit"))],
)
def save_psmf_block(
    block_id: str,
    request: Request,
    content: str = Form(...),
    change_summary: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    expected_content_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    block = psmf_service.get_psmf_block(db, block_id)
    if not block:
        return redirect_with_message("/psmf?tab=chapters", error="Блок МФСФ не найден.")
    require_text_versions(block, psmf_service.current_block_version(block),
                          expected_version, expected_content_version)
    if not content.strip():
        return redirect_with_message(
            f"/psmf/blocks/{block_id}/edit",
            validation="Текст блока не должен быть пустым.",
        )
    try:
        psmf_service.save_block_draft(
            db,
            block,
            content=content,
            change_summary=change_summary,
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/psmf/blocks/{block_id}/edit", error=str(exc))
    return redirect_with_message(
        f"/psmf/blocks/{block_id}/edit",
        message="Блок МФСФ сохранен.",
    )


@router.post(
    "/psmf/blocks/{block_id}/submit",
    dependencies=[Depends(require_permission("edit"))],
)
def submit_psmf_block(
    block_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    expected_content_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    block = psmf_service.get_psmf_block(db, block_id)
    if not block:
        return redirect_with_message("/psmf?tab=chapters", error="Блок МФСФ не найден.")
    require_text_versions(block, psmf_service.current_block_version(block),
                          expected_version, expected_content_version)
    try:
        psmf_service.submit_block_for_review(db, block, user_id=current_user_id(request))
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/psmf/blocks/{block_id}/edit", error=str(exc))
    return redirect_with_message(
        f"/psmf/blocks/{block_id}/edit",
        message="Блок отправлен на проверку.",
    )


@router.post(
    "/psmf/blocks/{block_id}/approve",
    dependencies=[Depends(require_any_permission("approve", "edit"))],
)
def approve_psmf_block(
    block_id: str,
    request: Request,
    approval_comment: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    expected_content_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    block = psmf_service.get_psmf_block(db, block_id)
    if not block:
        return redirect_with_message("/psmf?tab=chapters", error="Блок МФСФ не найден.")
    require_text_versions(block, psmf_service.current_block_version(block),
                          expected_version, expected_content_version)
    try:
        psmf_service.approve_block(
            db,
            block,
            approved_by=current_actor_name(request),
            approval_comment=approval_comment,
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/psmf/blocks/{block_id}/edit", error=str(exc))
    return redirect_with_message(
        f"/psmf/blocks/{block_id}/edit",
        message="Блок утвержден.",
    )


@router.post(
    "/psmf/blocks/{block_id}/return-revision",
    dependencies=[Depends(require_any_permission("approve", "edit"))],
)
def return_psmf_block_for_revision(
    block_id: str,
    request: Request,
    return_comment: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    expected_content_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    block = psmf_service.get_psmf_block(db, block_id)
    if not block:
        return redirect_with_message("/psmf?tab=approval", error="Блок МФСФ не найден.")
    require_text_versions(block, psmf_service.current_block_version(block),
                          expected_version, expected_content_version)
    try:
        psmf_service.return_block_for_revision(
            db,
            block,
            returned_by=current_actor_name(request),
            return_comment=return_comment,
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/psmf?tab=approval", error=str(exc))
    return redirect_with_message("/psmf?tab=approval", message="Блок возвращен на доработку.")


@router.post(
    "/psmf/blocks/{block_id}/new-version",
    dependencies=[Depends(require_any_permission("create", "edit"))],
)
def create_psmf_block_version(
    block_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    expected_content_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    block = psmf_service.get_psmf_block(db, block_id)
    if not block:
        return redirect_with_message("/psmf?tab=chapters", error="Блок МФСФ не найден.")
    require_text_versions(block, psmf_service.current_block_version(block),
                          expected_version, expected_content_version)
    try:
        psmf_service.create_new_block_version(
            db,
            block,
            created_by=current_actor_name(request),
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/psmf/blocks/{block_id}/edit", error=str(exc))
    return redirect_with_message(
        f"/psmf/blocks/{block_id}/edit",
        message="Создана новая версия блока.",
    )


@router.post(
    "/psmf/blocks/{block_id}/partner-review",
    dependencies=[Depends(require_permission("edit"))],
)
def review_psmf_partner_block(
    block_id: str,
    request: Request,
    partner_review_comment: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    expected_content_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    block = psmf_service.get_psmf_block(db, block_id)
    if not block:
        return redirect_with_message("/psmf?tab=chapters", error="Блок МФСФ не найден.")
    require_text_versions(block, psmf_service.current_block_version(block),
                          expected_version, expected_content_version)
    try:
        psmf_service.partner_review_block(
            db,
            block,
            reviewed_by=current_actor_name(request),
            comment=partner_review_comment,
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/psmf/blocks/{block_id}/edit", error=str(exc))
    return redirect_with_message(
        f"/psmf/blocks/{block_id}/edit",
        message="Партнерский блок согласован.",
    )


@router.get("/psmf/blocks/{block_id}/edit", response_class=HTMLResponse)
def edit_psmf_block_page(
    block_id: str,
    request: Request,
    block_version_id: str | None = None,
    db: Session = Depends(get_db),
):
    block = psmf_service.get_psmf_block(db, block_id)
    if not block:
        return redirect_with_message("/psmf?tab=chapters", error="Блок МФСФ не найден.")
    current_block_version = psmf_service.current_block_version(block)
    block_version = current_block_version
    if block_version_id:
        block_version = next(
            (
                version
                for version in block.versions
                if version.id == block_version_id
            ),
            current_block_version,
        )
    block_version_is_archive = bool(
        block_version and current_block_version and block_version.id != current_block_version.id
    )
    block_attachments = [
        attachment
        for attachment in crud.list_attachments(db)
        if attachment.related_object_type == "PSMFBlock"
        and attachment.related_object_id == block.id
    ]
    return_tab = "chapters"
    parent = block.component.parent if block.component else None
    while parent:
        if parent.code == "9":
            return_tab = "annexes"
            break
        parent = parent.parent
    return templates.TemplateResponse(
        request,
        "psmf_block_edit.html",
        {
            "block_current_version": psmf_service.current_block_version,
            "request": request,
            "expected_snapshot": snapshot_token(db, PSMF_INPUTS),
            "active_page": "psmf",
            "block": block,
            "block_version": block_version,
            "current_block_version": current_block_version,
            "block_version_is_archive": block_version_is_archive,
            "block_attachments": block_attachments,
            "block_is_partner_specific": psmf_service.is_partner_specific_block(block),
            "partners": crud.list_partners(db),
            "return_tab": return_tab,
            "status_labels": psmf_service.PSMF_STATUS_LABELS,
            "block_type_labels": psmf_service.PSMF_BLOCK_TYPE_LABELS,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/psmf/blocks/{block_id}/attachments",
    dependencies=[Depends(require_any_permission("upload", "edit"))],
)
@atomic_write
def upload_psmf_block_attachment(
    block_id: str,
    request: Request,
    document_file: UploadFile = File(...),
    document_title: str | None = Form(None),
    document_version: str | None = Form(None),
    document_date: str | None = Form(None),
    partner_id: str | None = Form(None),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.PSMFBlock, block_id, expected_version)
    from app.routers.placeholders import optional_date, safe_display_name, store_document_file

    block = psmf_service.get_psmf_block(db, block_id)
    return_path = f"/psmf/blocks/{block_id}/edit"
    if not block:
        return redirect_with_message("/psmf?tab=annexes", error="Блок МФСФ не найден.")
    original_name = safe_display_name(document_file.filename)
    if Path(original_name).suffix.lower() != ".pdf":
        return redirect_with_message(return_path, validation="Для приложения можно загрузить только PDF-файл.")
    is_partner_specific = psmf_service.is_partner_specific_block(block)
    if is_partner_specific and not partner_id:
        return redirect_with_message(return_path, validation="Для партнерского приложения выберите партнера.")
    if partner_id and not crud.get_partner(db, partner_id):
        return redirect_with_message(return_path, validation="Партнер не найден.")
    try:
        parsed_document_date = optional_date(document_date)
    except ValueError:
        return redirect_with_message(return_path, validation="Укажите корректную дату документа.")
    try:
        stored_file = store_document_file(document_file)
    except ValueError as exc:
        return redirect_with_message(return_path, validation=str(exc))
    current_user = getattr(request.state, "current_user", None)
    crud.create_attachment(
        db,
        file_name=original_name,
        attachment_type="PSMF",
        document_title=(document_title or "").strip() or original_name,
        document_type="PSMF",
        related_object_type="PSMFBlock",
        related_object_id=block.id,
        partner_id=partner_id if is_partner_specific else None,
        mime_type=document_file.content_type or "application/pdf",
        file_size_bytes=stored_file["file_size_bytes"],
        storage_path=stored_file["storage_path"],
        document_version=(document_version or "").strip() or None,
        document_date=parsed_document_date,
        status="approved",
        scope="PARTNER_SPECIFIC" if is_partner_specific else "GLOBAL",
        psmf_annex_code=psmf_service.psmf_annex_code_for_block(block),
        include_in_psmf=True,
        checksum_sha256=stored_file["checksum_sha256"],
        uploaded_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message(return_path, message="PDF-документ добавлен в приложение МФСФ.")


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
        message="Предварительная сборка МФСФ сформирована.",
    )


@router.post("/psmf/partner-build", dependencies=[Depends(require_permission("export"))])
@atomic_write(serialize=True)
def create_partner_psmf_build(
    request: Request,
    partner_id: str = Form(...),
    build_type: str = Form("DRAFT"),
    confirm_warnings: bool = Form(False),
    expected_snapshot: str = Form(..., min_length=64, max_length=64),
    db: Session = Depends(get_db),
):
    require_snapshot(db, PSMF_INPUTS, expected_snapshot)
    try:
        build = psmf_service.create_partner_build(
            db,
            partner_id=partner_id,
            build_type=build_type,
            generated_by=current_actor_name(request),
            confirm_warnings=confirm_warnings,
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(
            f"/psmf?tab=partner&partner_id={partner_id}&preview=true",
            error=str(exc),
        )
    return redirect_with_message(
        f"/psmf?tab=partner&partner_id={partner_id}&preview=true",
        message=f"{build.build_type} комплект МФСФ сформирован.",
    )


@router.post(
    "/psmf/builds/{build_id}/approve",
    dependencies=[Depends(require_any_permission("approve", "edit"))],
)
@atomic_write
def approve_psmf_build(
    build_id: str,
    request: Request,
    approval_comment: str | None = Form(None),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.PSMFPartnerBuild, build_id, expected_version)
    build = psmf_service.get_partner_build(db, build_id)
    if not build:
        return redirect_with_message("/psmf?tab=approval", error="Сборка МФСФ не найдена.")
    try:
        psmf_service.approve_partner_build(
            db,
            build,
            approved_by=current_actor_name(request),
            approval_comment=approval_comment,
            user_id=current_user_id(request),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/psmf?tab=approval", error=str(exc))
    return redirect_with_message("/psmf?tab=approval", message="Финальная сборка утверждена.")


@router.get("/psmf/builds/{build_id}/download", dependencies=[Depends(require_permission("export"))])
def download_psmf_build_docx(build_id: str, db: Session = Depends(get_db)):
    build = psmf_service.get_partner_build(db, build_id)
    if not build or not build.docx_file_path:
        return redirect_with_message("/psmf?tab=partner", error="DOCX сборки не найден.")
    path = Path(build.docx_file_path)
    if not path.exists():
        return redirect_with_message("/psmf?tab=partner", error="Файл DOCX отсутствует на диске.")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=path.name,
    )


@router.get("/psmf/builds/{build_id}/download.pdf", dependencies=[Depends(require_permission("export"))])
def download_psmf_build_pdf(build_id: str, db: Session = Depends(get_db)):
    build = psmf_service.get_partner_build(db, build_id)
    if not build or not build.pdf_file_path:
        return redirect_with_message("/psmf?tab=partner", error="PDF-сборка не найдена.")
    path = Path(build.pdf_file_path)
    if not path.exists():
        return redirect_with_message("/psmf?tab=partner", error="Файл PDF отсутствует на диске.")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/psmf/builds/{build_id}/download-package", dependencies=[Depends(require_permission("export"))])
def download_psmf_build_package(build_id: str, db: Session = Depends(get_db)):
    build = psmf_service.get_partner_build(db, build_id)
    if not build or not build.package_file_path:
        return redirect_with_message("/psmf?tab=partner", error="PDF-комплект не найден.")
    path = Path(build.package_file_path)
    if not path.exists():
        return redirect_with_message("/psmf?tab=partner", error="ZIP-комплект отсутствует на диске.")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.post("/psmf/settings", dependencies=[Depends(require_permission("edit"))])
@atomic_write
def save_psmf_settings(
    request: Request,
    company_name: str = Form(...),
    psmf_template_version: str = Form(...),
    default_empty_state_text: str = Form(...),
    enable_docx_export: bool = Form(False),
    enable_pdf_export: bool = Form(False),
    include_optional_annex_k_if_has_content: bool = Form(False),
    skip_empty_partner_specific_blocks: bool = Form(False),
    final_build_requires_all_required_components_approved: bool = Form(False),
    allow_final_build_with_warnings_after_confirmation: bool = Form(False),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    settings_record = psmf_service.get_psmf_settings(db)
    if settings_record.id is None:
        raise HTTPException(409, "Настройки МФСФ недоступны. Обратитесь к администратору.")
    protect_record(db, models.PSMFSettings, settings_record.id, expected_version)
    settings = psmf_service.get_psmf_settings(db)
    psmf_service.update_settings(
        db,
        settings,
        company_name=company_name,
        psmf_template_version=psmf_template_version,
        default_empty_state_text=default_empty_state_text,
        enable_docx_export=enable_docx_export,
        enable_pdf_export=enable_pdf_export,
        include_optional_annex_k_if_has_content=include_optional_annex_k_if_has_content,
        skip_empty_partner_specific_blocks=skip_empty_partner_specific_blocks,
        final_build_requires_all_required_components_approved=final_build_requires_all_required_components_approved,
        allow_final_build_with_warnings_after_confirmation=allow_final_build_with_warnings_after_confirmation,
        user_id=current_user_id(request),
    )
    return redirect_with_message("/psmf?tab=settings", message="Настройки МФСФ сохранены.")


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
