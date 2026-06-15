from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import crud
from app.auth import require_any_permission, require_permission
from app.database import get_db
from app.templating import templates
from app.ui_helpers import (
    active_filters,
    contains_search,
    in_date_range,
    redirect_with_message,
    unique_values,
)


router = APIRouter()


PLACEHOLDER_PAGES = {
    "psur": {
        "title": "PSUR / PBRER",
        "description": "Periodic safety reports planning and tracking module.",
    },
    "rmp": {
        "title": "RMP",
        "description": "Risk management plan module.",
    },
    "psmf": {
        "title": "PSMF",
        "description": "Pharmacovigilance system master file module.",
    },
    "documents": {
        "title": "Documents",
        "description": "Document registry and controlled attachments module.",
    },
    "audit_log": {
        "title": "Audit Log",
        "description": "Central audit trail view.",
    },
    "settings": {
        "title": "Settings",
        "description": "System settings module.",
    },
}


def render_placeholder(request: Request, active_page: str) -> HTMLResponse:
    page = PLACEHOLDER_PAGES[active_page]
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        {
            "request": request,
            "active_page": active_page,
            "page_title": page["title"],
            "page_description": page["description"],
        },
    )


@router.get("/psur", response_class=HTMLResponse)
def psur_page(request: Request):
    return render_placeholder(request, "psur")


@router.get("/rmp", response_class=HTMLResponse)
def rmp_page(request: Request):
    return render_placeholder(request, "rmp")


@router.get("/psmf", response_class=HTMLResponse)
def psmf_page(request: Request):
    return render_placeholder(request, "psmf")


@router.get("/documents", response_class=HTMLResponse)
def documents_page(
    request: Request,
    search: str | None = None,
    attachment_type: str | None = None,
    partner_id: str | None = None,
    product_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    all_documents = crud.list_attachments(db)
    documents = [
        document
        for document in all_documents
        if contains_search(
            search,
            document.file_name,
            document.attachment_type,
            document.storage_path,
            document.checksum_sha256,
            document.case.case_number if document.case else "",
            document.safety_report.safety_report_number if document.safety_report else "",
        )
        and (not attachment_type or document.attachment_type == attachment_type)
        and (not partner_id or document_partner_id(document) == partner_id)
        and (
            not product_id
            or (
                document.case
                and any(row.product_id == product_id for row in document.case.case_products)
            )
        )
        and in_date_range(document.uploaded_at or document.created_at, date_from, date_to)
    ]
    filters = {
        "search": search or "",
        "attachment_type": attachment_type or "",
        "partner_id": partner_id or "",
        "product_id": product_id or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "active": active_filters(
            search=search,
            attachment_type=attachment_type,
            partner_id=partner_id,
            product_id=product_id,
            date_from=date_from,
            date_to=date_to,
        ),
    }
    return templates.TemplateResponse(
        request,
        "documents.html",
        {
            "request": request,
            "active_page": "documents",
            "documents": documents,
            "cases": crud.list_cases(db),
            "reports": crud.list_safety_reports(db),
            "partners": crud.list_partners(db),
            "products": crud.list_products(db),
            "type_options": unique_values(
                [document.attachment_type for document in all_documents]
            ),
            "filters": filters,
            "total_count": len(all_documents),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/documents",
    dependencies=[Depends(require_any_permission("upload", "create"))],
)
def create_document_form(
    request: Request,
    file_name: str = Form(...),
    attachment_type: str | None = Form(None),
    case_id: str | None = Form(None),
    safety_report_id: str | None = Form(None),
    mime_type: str | None = Form(None),
    storage_path: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not file_name.strip():
        return redirect_with_message("/documents", validation="File name is required.")
    if case_id and not crud.get_case(db, case_id):
        return redirect_with_message("/documents", validation="Selected case was not found.")
    if safety_report_id and not crud.get_safety_report(db, safety_report_id):
        return redirect_with_message(
            "/documents",
            validation="Selected safety report was not found.",
        )
    current_user = getattr(request.state, "current_user", None)
    crud.create_attachment(
        db,
        file_name=file_name.strip(),
        attachment_type=attachment_type,
        case_id=case_id or None,
        safety_report_id=safety_report_id or None,
        mime_type=mime_type,
        storage_path=storage_path,
        uploaded_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message("/documents", message="Document saved.")


@router.post(
    "/documents/{document_id}/delete",
    dependencies=[Depends(require_permission("soft_delete"))],
)
def delete_document_form(
    document_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    db: Session = Depends(get_db),
):
    document = crud.get_attachment(db, document_id)
    if not document:
        return redirect_with_message("/documents", error="Document not found.")
    current_user = getattr(request.state, "current_user", None)
    crud.delete_attachment(
        db,
        document,
        deleted_by_user_id=current_user.id if current_user else None,
        delete_reason=delete_reason,
    )
    return redirect_with_message("/documents", message="Document deleted.")


@router.get(
    "/audit-log",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("audit_view"))],
)
def audit_log_page(
    request: Request,
    search: str | None = None,
    entity_type: str | None = None,
    source_module: str | None = None,
    action: str | None = None,
    user_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    all_entries = crud.list_audit_entries(db)
    entries = [
        entry
        for entry in all_entries
        if contains_search(
            search,
            entry.entity_type,
            entry.entity_id,
            entry.action,
            entry.field_name,
            entry.old_value,
            entry.new_value,
            entry.change_reason,
            entry.comment,
            entry.source_module,
            entry.user.email if entry.user else "",
            entry.case.case_number if entry.case else "",
        )
        and (not entity_type or entry.entity_type == entity_type)
        and (not source_module or entry.source_module == source_module)
        and (not action or entry.action == action)
        and (not user_id or (entry.changed_by or entry.user_id) == user_id)
        and in_date_range(entry.changed_at or entry.timestamp, date_from, date_to)
    ]
    filters = {
        "search": search or "",
        "entity_type": entity_type or "",
        "source_module": source_module or "",
        "action": action or "",
        "user_id": user_id or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "active": active_filters(
            search=search,
            entity_type=entity_type,
            source_module=source_module,
            action=action,
            user_id=user_id,
            date_from=date_from,
            date_to=date_to,
        ),
    }
    return templates.TemplateResponse(
        request,
        "audit_log.html",
        {
            "request": request,
            "active_page": "audit_log",
            "entries": entries,
            "users": crud.list_users(db),
            "entity_options": unique_values([entry.entity_type for entry in all_entries]),
            "module_options": unique_values([entry.source_module for entry in all_entries]),
            "action_options": unique_values([entry.action for entry in all_entries]),
            "filters": filters,
            "total_count": len(all_entries),
        },
    )


def document_partner_id(document) -> str | None:
    if document.case and document.case.partner_id:
        return document.case.partner_id
    if document.safety_report and document.safety_report.partner_id:
        return document.safety_report.partner_id
    return None


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return render_placeholder(request, "settings")
