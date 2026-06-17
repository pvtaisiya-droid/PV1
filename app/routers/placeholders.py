import hashlib
import re
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app import crud
from app.audit import log_audit
from app.auth import require_any_permission, require_permission
from app.config import get_settings
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
UPLOAD_ROOT = (Path.cwd() / "uploads" / "documents").resolve()
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
    "PV agreement",
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
    "Partner",
    "Product",
    "Case",
    "SafetyReport",
    "Reconciliation",
    "IncomingRequest",
    "SOP",
    "Other",
]


PLACEHOLDER_PAGES = {
    "rmp": {
        "title": "RMP",
        "description": "Risk management plan module.",
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


@router.get("/rmp", response_class=HTMLResponse)
def rmp_page(request: Request):
    return render_placeholder(request, "rmp")


@router.get("/documents", response_class=HTMLResponse)
def documents_page(
    request: Request,
    search: str | None = None,
    attachment_type: str | None = None,
    partner_id: str | None = None,
    product_id: str | None = None,
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
        and (not product_id or document_product_id(document) == product_id)
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
            "type_options": DOCUMENT_TYPES,
            "status_options": DOCUMENT_STATUSES,
            "related_object_types": RELATED_OBJECT_TYPES,
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
    document_status: str | None = Form("draft"),
    comment: str | None = Form(None),
    file_name: str | None = Form(None),
    case_id: str | None = Form(None),
    safety_report_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    has_upload = bool(document_file and safe_display_name(document_file.filename))
    original_name = safe_display_name(document_file.filename if document_file else None)
    display_name = (
        (file_name or "").strip()
        or (document_title or "").strip()
        or original_name
        or (file_url or "").strip()
    )
    if not display_name:
        return redirect_with_message("/documents", validation="Document title or file is required.")
    if not has_upload and not (file_url or "").strip():
        return redirect_with_message("/documents", validation="Upload a file or provide a file URL.")
    if case_id and not crud.get_case(db, case_id):
        return redirect_with_message("/documents", validation="Selected case was not found.")
    if safety_report_id and not crud.get_safety_report(db, safety_report_id):
        return redirect_with_message(
            "/documents",
            validation="Selected safety report was not found.",
        )
    if partner_id and not crud.get_partner(db, partner_id):
        return redirect_with_message("/documents", validation="Selected partner was not found.")
    if product_id and not crud.get_product(db, product_id):
        return redirect_with_message("/documents", validation="Selected product was not found.")
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
            return redirect_with_message("/documents", validation=str(exc))
        mime_type = document_file.content_type or "application/octet-stream"
    current_user = getattr(request.state, "current_user", None)
    crud.create_attachment(
        db,
        file_name=display_name,
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
        document_date=optional_date(document_date),
        status=document_status or "draft",
        comment=comment,
        checksum_sha256=stored_file["checksum_sha256"],
        uploaded_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message("/documents", message="Document saved.")


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


def store_document_file(document_file: UploadFile) -> dict[str, object]:
    display_name = validate_upload_name(document_file.filename)

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    target = (UPLOAD_ROOT / f"{uuid.uuid4().hex}_{safe_storage_name(display_name)}").resolve()
    if not is_relative_to(target, UPLOAD_ROOT):
        raise ValueError("Invalid file name.")

    checksum = hashlib.sha256()
    file_size = 0
    too_large = False
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

    relative_path = target.relative_to(Path.cwd().resolve()).as_posix()
    return {
        "storage_path": relative_path,
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


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return render_placeholder(request, "settings")
