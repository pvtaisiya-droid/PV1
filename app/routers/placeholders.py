from app import models
from app.concurrency import protect_record
from app.unit_of_work import atomic_write, register_new_file
import hashlib
import re
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud
from app.audit import log_audit
from app.auth import require_any_permission, require_permission
from app.config import get_settings
from app.concurrency import require_version
from app.database import get_db
from app.pagination import paginate_items
from app.statuses import status_codes
from app.templating import templates
from app.ui_helpers import (
    active_filters,
    contains_search,
    in_date_range,
    redirect_with_message,
    unique_values,
)


router = APIRouter()
UPLOAD_ROOT = get_settings().storage_root / "documents"
MAX_UPLOAD_BYTES = get_settings().max_upload_bytes
ALLOWED_UPLOAD_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".eml",
    ".jpeg",
    ".jpg",
    ".json",
    ".ods",
    ".odt",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rtf",
    ".txt",
    ".xls",
    ".xlsx",
    ".xml",
}
DOCUMENT_TYPES = [
    "job description",
    "appointment order",
    "CV",
    "diploma or certificate",
    "PV agreement",
    "additional agreement",
    "organization chart",
    "training record",
    "risk management plan",
    "safety reference information",
    "risk minimization measure",
    "audit document",
    "quality system document",
    "reconciliation form",
    "partner response",
    "registration document",
    "PSUR/PBRER",
    "PSMF",
    "audit",
    "incoming request",
    "other",
]
DOCUMENT_STATUSES = status_codes("document")
RELATED_OBJECT_TYPES = [
    "QPPV",
    "QPPVDeputy",
    "PartnerResponsiblePerson",
    "OrganizationStructure",
    "Partner",
    "Contract",
    "Product",
    "Employee",
    "Training",
    "Audit",
    "Study",
    "SafetySource",
    "ComputerizedSystem",
    "Monitoring",
    "Case",
    "SafetyReport",
    "Reconciliation",
    "IncomingRequest",
    "SOP",
    "PSMFComponent",
    "PSMFBlock",
    "Other",
]
PSMF_ANNEX_CODES = [
    "Приложение А-1", "Приложение А-2", "Приложение А-3",
    "Приложение Б-1", "Приложение Б-1.1", "Приложение Б-3", "Приложение Б-3.1",
    "Приложение В-1", "Приложение В-1.1", "Приложение В-2",
    "Приложение Г-1", "Приложение Г-1.1",
    "Приложение Д-1", "Приложение Д-1.1", "Приложение Д-2",
    "Приложение Е-1", "Приложение Е-2", "Приложение Е-2.1", "Приложение Е-3",
    "Приложение Ж-1", "Приложение Ж-1.1", "Приложение Ж-2", "Приложение Ж-2.1",
    "Приложение Ж-3", "Приложение Ж-3.1", "Приложение Ж-4",
    "Приложение З-1", "Приложение З-2", "Приложение З-2.1", "Приложение З-3",
    "Приложение З-4", "Приложение З-5", "Приложение З-6",
    "Приложение И-1", "Приложение И-2", "Приложение И-2.1",
    "Приложение К", "Приложение Л",
]


PLACEHOLDER_PAGES = {
    "rmp": {
        "title": "RMP",
        "description": "Risk management plan module.",
    },
    "training": {
        "title": "Training",
        "description": "Training module.",
    },
    "documents": {
        "title": "Documents",
        "description": "Document registry and controlled attachments module.",
    },
    "audit_log": {
        "title": "Audit Log",
        "description": "Central audit trail view.",
    },
    "message_journal": {
        "title": "Incoming message journal",
        "description": "Incoming message journal module.",
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


@router.get("/rmp", response_class=HTMLResponse)
def rmp_page(request: Request):
    return render_placeholder(request, "rmp")


@router.get("/training", response_class=HTMLResponse)
def training_page(
    request: Request,
    employee_id: str | None = None,
    db: Session = Depends(get_db),
):
    employees = crud.list_users(db)
    selected_employee = next((row for row in employees if row.id == employee_id), None)
    documents = [
        document
        for document in crud.list_attachments(db)
        if (
            selected_employee
            and document.related_object_type == "Employee"
            and document.related_object_id == selected_employee.id
        )
        or (
            not selected_employee
            and document.related_object_type == "Training"
            and document.related_object_id == "registry"
        )
    ]
    return templates.TemplateResponse(
        request,
        "training.html",
        {
            "request": request,
            "active_page": "training",
            "employees": employees,
            "selected_employee": selected_employee,
            "documents": documents,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.get("/message-journal", response_class=HTMLResponse)
def message_journal_page(request: Request):
    return render_placeholder(request, "message_journal")


@router.get("/documents", response_class=HTMLResponse)
def documents_page(
    request: Request,
    search: str | None = None,
    attachment_type: str | None = None,
    partner_id: str | None = None,
    psmf_annex_code: str | None = None,
    related_object_type: str | None = None,
    related_object_id: str | None = None,
    employee_id: str | None = None,
    contract_id: str | None = None,
    product_id: str | None = None,
    document_status: str | None = None,
    expiry: str | None = None,
    psmf_inclusion: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    per_page: int | None = None,
    db: Session = Depends(get_db),
):
    all_documents = crud.list_attachments(db)
    documents = [
        document
        for document in all_documents
        if contains_search(
            search,
            document.document_title,
            document.document_type,
            document.file_name,
            document.attachment_type,
            document.related_object_type,
            document.related_object_id,
            document.file_url,
            document.comment,
            document.storage_path,
            document.checksum_sha256,
            document.case.case_number if document.case else "",
            document.safety_report.safety_report_number if document.safety_report else "",
            document.partner.partner_name if document.partner else "",
            document.product.product_name if document.product else "",
        )
        and (
            not attachment_type
            or document.document_type == attachment_type
            or document.attachment_type == attachment_type
        )
        and (not partner_id or document_partner_id(document) == partner_id)
        and (not psmf_annex_code or document.psmf_annex_code == psmf_annex_code)
        and (not related_object_type or document.related_object_type == related_object_type)
        and (not related_object_id or document.related_object_id == related_object_id)
        and (
            not employee_id
            or (
                document.related_object_type == "Employee"
                and document.related_object_id == employee_id
            )
        )
        and (
            not contract_id
            or (
                document.related_object_type == "Contract"
                and document.related_object_id == contract_id
            )
        )
        and (not product_id or document_product_id(document) == product_id)
        and (not document_status or document.status == document_status)
        and matches_expiry(document.valid_until, expiry)
        and matches_psmf_inclusion(document, psmf_inclusion)
        and in_date_range(
            document.document_date or document.uploaded_at or document.created_at,
            date_from,
            date_to,
        )
    ]
    filters = {
        "search": search or "",
        "attachment_type": attachment_type or "",
        "partner_id": partner_id or "",
        "psmf_annex_code": psmf_annex_code or "",
        "related_object_type": related_object_type or "",
        "related_object_id": related_object_id or "",
        "employee_id": employee_id or "",
        "contract_id": contract_id or "",
        "product_id": product_id or "",
        "document_status": document_status or "",
        "expiry": expiry or "",
        "psmf_inclusion": psmf_inclusion or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "active": active_filters(
            search=search,
            attachment_type=attachment_type,
            partner_id=partner_id,
            psmf_annex_code=psmf_annex_code,
            related_object_type=related_object_type,
            related_object_id=related_object_id,
            employee_id=employee_id,
            contract_id=contract_id,
            product_id=product_id,
            document_status=document_status,
            expiry=expiry,
            psmf_inclusion=psmf_inclusion,
            date_from=date_from,
            date_to=date_to,
        ),
    }
    page_documents, pagination = paginate_items(documents, page, per_page)
    return templates.TemplateResponse(
        request,
        "documents.html",
        {
            "request": request,
            "active_page": "documents",
            "documents": page_documents,
            "cases": crud.list_cases(db),
            "reports": crud.list_safety_reports(db),
            "partners": crud.list_partners(db),
            "products": crud.list_products(db),
            "contracts": crud.list_contracts(db),
            "employees": crud.list_users(db),
            "type_options": DOCUMENT_TYPES,
            "status_options": DOCUMENT_STATUSES,
            "related_object_types": RELATED_OBJECT_TYPES,
            "psmf_annex_codes": PSMF_ANNEX_CODES,
            "filters": filters,
            "total_count": len(all_documents),
            "filtered_count": len(documents),
            "pagination": pagination,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/documents",
    dependencies=[Depends(require_any_permission("upload", "create", "manage_document_versions"))],
)
@atomic_write
def create_document_form(
    request: Request,
    document_file: UploadFile | None = File(None),
    document_title: str | None = Form(None),
    document_type: str | None = Form(None),
    related_object_type: str | None = Form(None),
    related_object_id: str | None = Form(None),
    partner_id: str | None = Form(None),
    product_id: str | None = Form(None),
    file_url: str | None = Form(None),
    document_version: str | None = Form(None),
    document_date: str | None = Form(None),
    valid_until: str | None = Form(None),
    document_status: str | None = Form("draft"),
    is_signed: bool = Form(False),
    page_count: str | None = Form(None),
    scope: str = Form("GLOBAL"),
    psmf_annex_code: str | None = Form(None),
    include_in_psmf: bool = Form(False),
    inclusion_order: str | None = Form(None),
    comment: str | None = Form(None),
    file_name: str | None = Form(None),
    case_id: str | None = Form(None),
    safety_report_id: str | None = Form(None),
    return_to_case_id: str | None = Form(None),
    return_to: str | None = Form(None),
    expected_related_version: int | None = Form(None, gt=0),
    expected_partner_version: int | None = Form(None, gt=0),
    expected_product_version: int | None = Form(None, gt=0),
    db: Session = Depends(get_db),
):
    parent_models = {'Case': models.Case, 'Partner': models.Partner,
        'Contract': models.Contract, 'Product': models.Product, 'Employee': models.User,
        'PartnerResponsiblePerson': models.ContractContact, 'SafetySource': models.SafetyReport,
        'SafetyReport': models.SafetyReport, 'PSMFBlock': models.PSMFBlock,
        'PSMFComponent': models.PSMFComponent, 'PSMFBuild': models.PSMFPartnerBuild, 'SOP': models.SOP}
    targets = []
    if related_object_type in parent_models and related_object_id:
        targets.append((parent_models[related_object_type], related_object_id, expected_related_version))
    if case_id:
        targets.append((models.Case, case_id, expected_related_version))
    if safety_report_id:
        targets.append((models.SafetyReport, safety_report_id, expected_related_version))
    if not targets:
        if partner_id:
            targets.append((models.Partner, partner_id, expected_partner_version))
        if product_id:
            targets.append((models.Product, product_id, expected_product_version))
    for model, identifier, version in targets:
        if version is None:
            raise HTTPException(status_code=422, detail='Expected source version is required')
        parent = protect_record(db, model, identifier, version)
        if model is models.Case and crud.case_is_controlled(parent):
            raise HTTPException(status_code=409, detail='Case is locked. Reload before saving.')
    return_path = safe_document_return_url(return_to) or (
        f"/cases/{return_to_case_id}?tab=documents"
        if return_to_case_id and return_to_case_id == case_id
        else "/documents"
    )
    has_upload = bool(document_file and safe_display_name(document_file.filename))
    original_name = safe_display_name(document_file.filename if document_file else None)
    display_name = (
        (file_name or "").strip()
        or (document_title or "").strip()
        or original_name
        or (file_url or "").strip()
    )
    if not display_name:
        return redirect_with_message(return_path, validation="Document title or file is required.")
    if not has_upload and not (file_url or "").strip():
        return redirect_with_message(return_path, validation="Upload a file or provide a file URL.")
    if case_id and not crud.get_case(db, case_id):
        return redirect_with_message(return_path, validation="Selected case was not found.")
    if safety_report_id and not crud.get_safety_report(db, safety_report_id):
        return redirect_with_message(
            return_path,
            validation="Selected safety report was not found.",
        )
    if partner_id and not crud.get_partner(db, partner_id):
        return redirect_with_message(return_path, validation="Selected partner was not found.")
    if product_id and not crud.get_product(db, product_id):
        return redirect_with_message(return_path, validation="Selected product was not found.")
    try:
        parsed_document_date = optional_date(document_date)
        parsed_valid_until = optional_date(valid_until)
        parsed_page_count = optional_int(page_count)
        parsed_inclusion_order = optional_int(inclusion_order) or 0
    except (TypeError, ValueError):
        return redirect_with_message(return_path, validation="Check dates, page count and inclusion order.")
    stored_file = {
        "storage_path": None,
        "file_size_bytes": None,
        "checksum_sha256": None,
    }
    mime_type = None
    if has_upload and document_file:
        try:
            stored_file = store_document_file(document_file)
        except ValueError as exc:
            return redirect_with_message(return_path, validation=str(exc))
        mime_type = document_file.content_type or "application/octet-stream"
    if parsed_page_count is None:
        parsed_page_count = pdf_page_count(
            stored_file.get("storage_path"),
            mime_type,
            original_name,
        )
    current_user = getattr(request.state, "current_user", None)
    crud.create_attachment(
        db,
        file_name=original_name or display_name,
        attachment_type=document_type,
        document_title=(document_title or "").strip() or display_name,
        document_type=document_type or "other",
        related_object_type=related_object_type or None,
        related_object_id=(related_object_id or "").strip() or None,
        partner_id=partner_id or None,
        product_id=product_id or None,
        case_id=case_id or None,
        safety_report_id=safety_report_id or None,
        mime_type=mime_type,
        file_size_bytes=stored_file["file_size_bytes"],
        storage_path=stored_file["storage_path"],
        file_url=(file_url or "").strip() or None,
        document_version=(document_version or "").strip() or None,
        document_date=parsed_document_date,
        valid_until=parsed_valid_until,
        status=document_status or "draft",
        is_signed=is_signed,
        page_count=parsed_page_count,
        scope=normalized_scope(scope, partner_id),
        psmf_annex_code=normalize_annex_code(psmf_annex_code),
        include_in_psmf=include_in_psmf,
        inclusion_order=parsed_inclusion_order,
        comment=comment,
        checksum_sha256=stored_file["checksum_sha256"],
        uploaded_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message(return_path, message="Document saved.")


@router.get(
    "/documents/{document_id}/view",
    dependencies=[Depends(require_permission("view"))],
)
def view_document(document_id: str, db: Session = Depends(get_db)):
    document = crud.get_attachment(db, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    file_path = resolve_document_path(document.storage_path)
    if file_path and file_path.is_file():
        return FileResponse(
            file_path,
            media_type=document.mime_type or "application/octet-stream",
            headers={"Content-Disposition": "inline"},
        )
    if document.file_url:
        return RedirectResponse(document.file_url, status_code=303)
    raise HTTPException(status_code=404, detail="Document file not found")


@router.post(
    "/documents/{document_id}/versions",
    dependencies=[Depends(require_any_permission("upload", "manage_document_versions"))],
)
def add_document_version(
    document_id: str,
    request: Request,
    document_file: UploadFile = File(...),
    document_version: str | None = Form(None),
    document_date: str | None = Form(None),
    valid_until: str | None = Form(None),
    document_status: str | None = Form("active"),
    is_signed: bool = Form(False),
    page_count: str | None = Form(None),
    change_reason: str | None = Form(None),
    return_to: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    return_path = safe_document_return_url(return_to) or "/documents"
    document = crud.get_attachment(db, document_id)
    if not document:
        return redirect_with_message(return_path, error="Document not found.")
    require_version(document, expected_version)
    try:
        parsed_document_date = optional_date(document_date)
        parsed_valid_until = optional_date(valid_until)
        parsed_page_count = optional_int(page_count)
    except ValueError as exc:
        return redirect_with_message(return_path, validation=str(exc))
    try:
        stored_file = store_document_file(document_file)
        parsed_page_count = parsed_page_count or pdf_page_count(
            stored_file.get("storage_path"),
            document_file.content_type,
            document_file.filename,
        )
    except ValueError as exc:
        return redirect_with_message(return_path, validation=str(exc))
    current_user = getattr(request.state, "current_user", None)
    try:
        crud.create_new_document_version(
            db,
            document,
            file_name=safe_display_name(document_file.filename),
            mime_type=document_file.content_type or "application/octet-stream",
            file_size_bytes=stored_file["file_size_bytes"],
            storage_path=stored_file["storage_path"],
            checksum_sha256=stored_file["checksum_sha256"],
            version_label=(document_version or "").strip() or None,
            document_date=parsed_document_date,
            valid_until=parsed_valid_until,
            status=document_status,
            is_signed=is_signed,
            page_count=parsed_page_count,
            change_reason=(change_reason or "").strip() or None,
            actor_id=current_user.id if current_user else None,
        )
    except Exception:
        db.rollback()
        Path(str(stored_file["storage_path"])).unlink(missing_ok=True)
        raise
    return redirect_with_message(
        return_path,
        message="New immutable document version saved. Earlier PSMF builds were not changed.",
    )


@router.post(
    "/documents/{document_id}/psmf-inclusion",
    dependencies=[Depends(require_any_permission("edit", "manage_document_versions"))],
)
def set_document_psmf_inclusion(
    document_id: str,
    request: Request,
    include_in_psmf: bool = Form(False),
    return_to: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    return_path = safe_document_return_url(return_to) or "/documents"
    document = crud.get_attachment(db, document_id)
    if not document:
        return redirect_with_message(return_path, error="Document not found.")
    require_version(document, expected_version)
    current_user = getattr(request.state, "current_user", None)
    crud.set_attachment_psmf_inclusion(
        db,
        document,
        is_included=include_in_psmf,
        actor_id=current_user.id if current_user else None,
    )
    return redirect_with_message(
        return_path,
        message="Document inclusion in PSMF was updated.",
    )


@router.get(
    "/documents/{document_id}/download",
    dependencies=[Depends(require_permission("view"))],
)
def download_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    document = crud.get_attachment(db, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    file_path = resolve_document_path(document.storage_path)
    if not file_path or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found")

    current_user = getattr(request.state, "current_user", None)
    log_audit(
        db,
        entity_type="Attachment",
        entity_id=document.id,
        action="download",
        case_id=document.case_id,
        new_value=document.file_name,
        user_id=current_user.id if current_user else None,
        source_module="Documents",
        comment=f"Downloaded document {document.file_name}.",
    )
    db.commit()
    return FileResponse(
        file_path,
        media_type=document.mime_type or "application/octet-stream",
        filename=document.file_name,
    )


@router.post(
    "/documents/{document_id}/delete",
    dependencies=[Depends(require_permission("soft_delete"))],
)
def delete_document_form(
    document_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    document = crud.get_attachment(db, document_id)
    if not document:
        return redirect_with_message("/documents", error="Document not found.")
    require_version(document, expected_version)
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
    page: int = 1,
    per_page: int | None = None,
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
    page_entries, pagination = paginate_items(entries, page, per_page)
    return templates.TemplateResponse(
        request,
        "audit_log.html",
        {
            "request": request,
            "active_page": "audit_log",
            "entries": page_entries,
            "users": crud.list_users(db),
            "entity_options": unique_values([entry.entity_type for entry in all_entries]),
            "module_options": unique_values([entry.source_module for entry in all_entries]),
            "action_options": unique_values([entry.action for entry in all_entries]),
            "filters": filters,
            "total_count": len(all_entries),
            "filtered_count": len(entries),
            "pagination": pagination,
        },
    )


def document_partner_id(document) -> str | None:
    if document.partner_id:
        return document.partner_id
    if document.case and document.case.partner_id:
        return document.case.partner_id
    if document.safety_report and document.safety_report.partner_id:
        return document.safety_report.partner_id
    if document.related_object_type in {"Partner", "partner"}:
        return document.related_object_id
    return None


def document_product_id(document) -> str | None:
    if document.product_id:
        return document.product_id
    if document.case:
        for row in document.case.case_products:
            if row.product_id:
                return row.product_id
    if document.related_object_type in {"Product", "product"}:
        return document.related_object_id
    return None


def safe_display_name(filename: str | None) -> str:
    return Path(filename or "").name.strip()


def safe_storage_name(filename: str | None) -> str:
    base_name = safe_display_name(filename) or "document"
    base_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name).strip("._")
    return (base_name or "document")[:160]


def validate_upload_name(filename: str | None) -> str:
    display_name = safe_display_name(filename)
    if not display_name:
        raise ValueError("Select a file to upload.")
    suffix = Path(display_name).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
        raise ValueError(f"Unsupported file type. Allowed: {allowed}.")
    return display_name


def store_document_file(
    document_file: UploadFile, *, created_paths: list[Path] | None = None,
) -> dict[str, object]:
    display_name = validate_upload_name(document_file.filename)

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    target = (UPLOAD_ROOT / f"{uuid.uuid4().hex}_{safe_storage_name(display_name)}").resolve()
    if not is_relative_to(target, UPLOAD_ROOT):
        raise ValueError("Invalid file name.")

    checksum = hashlib.sha256()
    file_size = 0
    too_large = False
    if created_paths is not None:
        created_paths.append(target)
    register_new_file(target)
    with target.open("wb") as output:
        while True:
            chunk = document_file.file.read(1024 * 1024)
            if not chunk:
                break
            if file_size + len(chunk) > MAX_UPLOAD_BYTES:
                too_large = True
                break
            file_size += len(chunk)
            checksum.update(chunk)
            output.write(chunk)

    if too_large:
        target.unlink(missing_ok=True)
        max_mb = max(1, MAX_UPLOAD_BYTES // (1024 * 1024))
        raise ValueError(f"Uploaded file exceeds {max_mb} MB.")

    if file_size == 0:
        target.unlink(missing_ok=True)
        raise ValueError("Uploaded file is empty.")

    return {
        "storage_path": target.as_posix(),
        "file_size_bytes": file_size,
        "checksum_sha256": checksum.hexdigest(),
    }


def resolve_document_path(storage_path: str | None) -> Path | None:
    if not storage_path:
        return None
    candidate = (Path.cwd() / storage_path).resolve()
    if not is_relative_to(candidate, UPLOAD_ROOT):
        return None
    return candidate


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def optional_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def optional_int(value: str | None) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    parsed = int(value)
    if parsed < 0:
        raise ValueError("Numeric values cannot be negative.")
    return parsed


def safe_document_return_url(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value or not value.startswith("/") or value.startswith("//"):
        return None
    return value


def normalize_annex_code(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    return value if value.startswith("Приложение ") else f"Приложение {value}"


def normalized_scope(value: str | None, partner_id: str | None) -> str:
    value = (value or "").strip().upper()
    if value not in {"GLOBAL", "PARTNER_SPECIFIC"}:
        value = "PARTNER_SPECIFIC" if partner_id else "GLOBAL"
    return value


def matches_expiry(valid_until: date | None, filter_value: str | None) -> bool:
    today = date.today()
    if filter_value == "expired":
        return bool(valid_until and valid_until < today)
    if filter_value == "valid":
        return bool(not valid_until or valid_until >= today)
    if filter_value == "no_expiry":
        return valid_until is None
    return True


def matches_psmf_inclusion(document, filter_value: str | None) -> bool:
    if filter_value == "included":
        return bool(document.include_in_psmf)
    if filter_value == "excluded":
        return not document.include_in_psmf
    if filter_value == "included_in_build":
        return bool(document.build_inclusions)
    return True


def pdf_page_count(storage_path: object, mime_type: str | None, filename: str | None) -> int | None:
    if not storage_path:
        return None
    if (mime_type or "").lower() != "application/pdf" and Path(filename or "").suffix.lower() != ".pdf":
        return None
    file_path = resolve_document_path(str(storage_path))
    if not file_path:
        return None
    try:
        from pypdf import PdfReader

        return len(PdfReader(file_path).pages)
    except Exception:
        return None


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return render_placeholder(request, "settings")
