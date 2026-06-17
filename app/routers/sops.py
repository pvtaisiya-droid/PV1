from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.database import get_db
from app.pagination import paginate_items
from app.routers.placeholders import store_document_file
from app.statuses import status_codes
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, in_date_range, redirect_with_message, unique_values


router = APIRouter()

SOP_DOCUMENT_TYPES = [
    "SOP",
    "Work Instruction",
    "Form",
    "Template",
    "Checklist",
    "Policy",
    "Plan",
    "Report",
    "Other",
]

SOP_STATUSES = status_codes("sop")

SOP_PROCESS_AREAS = [
    "ICSR",
    "Incoming Requests",
    "PV Agreements",
    "Reconciliation",
    "PSUR/PBRER",
    "PSMF",
    "Literature Monitoring",
    "Signal Management",
    "Training",
    "Audit",
    "Partner Management",
    "Document Control",
    "Quality System",
    "Other",
]


@router.get("/sops", response_class=HTMLResponse)
def sops_page(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    document_type: str | None = None,
    process_area: str | None = None,
    owner: str | None = None,
    next_review_to: date | None = None,
    page: int = 1,
    per_page: int | None = None,
    db: Session = Depends(get_db),
):
    all_sops = crud.list_sops(db)
    sops = [
        sop
        for sop in all_sops
        if contains_search(search, sop.sop_code, sop.title, sop.process_area)
        and (not status_filter or sop.status == status_filter)
        and (not document_type or sop.document_type == document_type)
        and (not process_area or sop.process_area == process_area)
        and (not owner or sop.owner == owner)
        and in_date_range(sop.next_review_date, None, next_review_to)
    ]
    filters = {
        "search": search or "",
        "status_filter": status_filter or "",
        "document_type": document_type or "",
        "process_area": process_area or "",
        "owner": owner or "",
        "next_review_to": next_review_to or "",
        "active": active_filters(
            search=search,
            status_filter=status_filter,
            document_type=document_type,
            process_area=process_area,
            owner=owner,
            next_review_to=next_review_to,
        ),
    }
    page_sops, pagination = paginate_items(sops, page, per_page)
    return templates.TemplateResponse(
        request,
        "sops.html",
        {
            "request": request,
            "active_page": "sops",
            "sops": page_sops,
            "document_type_options": SOP_DOCUMENT_TYPES,
            "status_options": SOP_STATUSES,
            "process_area_options": SOP_PROCESS_AREAS,
            "owner_options": unique_values([sop.owner for sop in all_sops]),
            "filters": filters,
            "total_count": len(all_sops),
            "filtered_count": len(sops),
            "pagination": pagination,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/sops",
    dependencies=[
        Depends(require_any_permission("create", "manage_reference_data", "upload", "manage_sop_versions"))
    ],
)
def create_sop_form(
    request: Request,
    sop_code: str = Form(...),
    title: str = Form(...),
    document_type: str = Form("SOP"),
    version: str = Form("1.0"),
    status_value: str = Form("Draft"),
    process_area: str = Form("Other"),
    owner: str = Form(...),
    effective_date: str = Form(...),
    next_review_date: str = Form(...),
    reviewer: str | None = Form(None),
    approver: str | None = Form(None),
    approval_date: str | None = Form(None),
    revision_reason: str | None = Form(None),
    file_url: str | None = Form(None),
    description: str | None = Form(None),
    training_required: bool = Form(False),
    sop_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    validation = validate_sop_required(sop_code, title, version, owner, effective_date, next_review_date)
    if validation:
        return redirect_with_message("/sops", validation=validation)
    try:
        stored_file = store_optional_sop_file(sop_file)
    except ValueError as exc:
        return redirect_with_message("/sops", validation=str(exc))
    current_user = getattr(request.state, "current_user", None)
    try:
        sop = crud.create_sop(
            db,
            build_sop_payload(
                sop_code=sop_code,
                title=title,
                document_type=document_type,
                version=version,
                status_value=status_value,
                process_area=process_area,
                owner=owner,
                reviewer=reviewer,
                approver=approver,
                approval_date=approval_date,
                effective_date=effective_date,
                next_review_date=next_review_date,
                revision_reason=revision_reason,
                file_path=stored_file.get("storage_path"),
                file_url=file_url,
                description=description,
                training_required=training_required,
            ),
            created_by_user_id=current_user.id if current_user else None,
        )
    except (IntegrityError, ValueError) as exc:
        db.rollback()
        return redirect_with_message("/sops", error=f"SOP could not be saved. {exc}")
    create_sop_document_if_needed(db, sop, stored_file, sop_file, current_user)
    return redirect_with_message(f"/sops/{sop.id}", message="SOP saved.")


@router.post(
    "/sops/{sop_id}/edit",
    dependencies=[
        Depends(require_any_permission("edit", "manage_reference_data", "upload", "manage_sop_versions"))
    ],
)
def edit_sop_form(
    sop_id: str,
    request: Request,
    sop_code: str = Form(...),
    title: str = Form(...),
    document_type: str = Form("SOP"),
    version: str = Form("1.0"),
    status_value: str = Form("Draft"),
    process_area: str = Form("Other"),
    owner: str = Form(...),
    effective_date: str = Form(...),
    next_review_date: str = Form(...),
    reviewer: str | None = Form(None),
    approver: str | None = Form(None),
    approval_date: str | None = Form(None),
    revision_reason: str | None = Form(None),
    file_url: str | None = Form(None),
    description: str | None = Form(None),
    training_required: bool = Form(False),
    sop_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    sop = crud.get_sop(db, sop_id)
    if not sop:
        return redirect_with_message("/sops", error="SOP not found.")
    validation = validate_sop_required(sop_code, title, version, owner, effective_date, next_review_date)
    if validation:
        return redirect_with_message(f"/sops/{sop_id}", validation=validation)
    try:
        stored_file = store_optional_sop_file(sop_file)
    except ValueError as exc:
        return redirect_with_message(f"/sops/{sop_id}", validation=str(exc))
    file_path = stored_file.get("storage_path") or sop.file_path
    current_user = getattr(request.state, "current_user", None)
    try:
        sop = crud.update_sop(
            db,
            sop,
            build_sop_payload(
                sop_code=sop_code,
                title=title,
                document_type=document_type,
                version=version,
                status_value=status_value,
                process_area=process_area,
                owner=owner,
                reviewer=reviewer,
                approver=approver,
                approval_date=approval_date,
                effective_date=effective_date,
                next_review_date=next_review_date,
                revision_reason=revision_reason,
                file_path=file_path,
                file_url=file_url,
                description=description,
                training_required=training_required,
            ),
            changed_by_user_id=current_user.id if current_user else None,
        )
    except (IntegrityError, ValueError) as exc:
        db.rollback()
        return redirect_with_message(f"/sops/{sop_id}", error=f"SOP could not be saved. {exc}")
    create_sop_document_if_needed(db, sop, stored_file, sop_file, current_user)
    return redirect_with_message(f"/sops/{sop.id}", message="SOP saved.")


@router.get("/sops/{sop_id}", response_class=HTMLResponse)
def sop_detail_page(sop_id: str, request: Request, db: Session = Depends(get_db)):
    sop = crud.get_sop(db, sop_id)
    if not sop:
        return redirect_with_message("/sops", error="SOP not found.")
    documents = [
        document
        for document in crud.list_attachments(db)
        if document.related_object_type == "SOP" and document.related_object_id == sop.id
    ]
    sop_versions = crud.list_sop_versions(db, sop.id)
    sop_version_ids = {version.id for version in sop_versions}
    audit_entries = [
        entry
        for entry in crud.list_audit_entries(db)
        if (entry.entity_type == "SOP" and entry.entity_id == sop.id)
        or (entry.entity_type == "SOPVersion" and entry.entity_id in sop_version_ids)
    ]
    return templates.TemplateResponse(
        request,
        "sop_detail.html",
        {
            "request": request,
            "active_page": "sops",
            "sop": sop,
            "documents": documents,
            "sop_versions": sop_versions,
            "audit_entries": audit_entries,
            "document_type_options": SOP_DOCUMENT_TYPES,
            "status_options": SOP_STATUSES,
            "process_area_options": SOP_PROCESS_AREAS,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.get("/api/sops", response_model=list[schemas.SOPRead])
def api_list_sops(db: Session = Depends(get_db)):
    return crud.list_sops(db)


def build_sop_payload(
    *,
    sop_code: str,
    title: str,
    document_type: str,
    version: str,
    status_value: str,
    process_area: str,
    owner: str,
    reviewer: str | None,
    approver: str | None,
    approval_date: str | None,
    effective_date: str,
    next_review_date: str,
    revision_reason: str | None,
    file_path: str | None,
    file_url: str | None,
    description: str | None,
    training_required: bool,
) -> schemas.SOPCreate:
    return schemas.SOPCreate(
        sop_code=sop_code.strip(),
        title=title.strip(),
        document_type=document_type,
        version=version.strip(),
        status=status_value,
        process_area=process_area,
        owner=owner.strip(),
        reviewer=optional_text(reviewer),
        approver=optional_text(approver),
        approval_date=optional_date(approval_date),
        effective_date=date.fromisoformat(effective_date),
        next_review_date=date.fromisoformat(next_review_date),
        revision_reason=optional_text(revision_reason),
        file_path=file_path,
        file_url=optional_text(file_url),
        description=optional_text(description),
        training_required=training_required,
    )


def validate_sop_required(
    sop_code: str,
    title: str,
    version: str,
    owner: str,
    effective_date: str,
    next_review_date: str,
) -> str | None:
    if not sop_code.strip() or not title.strip() or not version.strip() or not owner.strip():
        return "SOP code, title, version and owner are required."
    if not effective_date or not next_review_date:
        return "Effective date and next review date are required."
    try:
        effective = date.fromisoformat(effective_date)
        next_review = date.fromisoformat(next_review_date)
    except ValueError:
        return "Effective date and next review date must be valid dates."
    if next_review < effective:
        return "Next review date must be after effective date."
    return None


def store_optional_sop_file(sop_file: UploadFile | None) -> dict[str, object]:
    if not sop_file or not (sop_file.filename or "").strip():
        return {}
    return store_document_file(sop_file)


def create_sop_document_if_needed(
    db: Session,
    sop,
    stored_file: dict[str, object],
    sop_file: UploadFile | None,
    current_user,
) -> None:
    storage_path = stored_file.get("storage_path")
    if not storage_path:
        return
    crud.create_attachment(
        db,
        file_name=sop_file.filename if sop_file and sop_file.filename else sop.sop_code,
        attachment_type=sop.document_type,
        document_title=f"{sop.sop_code} {sop.title}",
        document_type=sop.document_type,
        related_object_type="SOP",
        related_object_id=sop.id,
        mime_type=sop_file.content_type if sop_file else None,
        file_size_bytes=stored_file.get("file_size_bytes"),
        storage_path=storage_path,
        document_version=sop.version,
        document_date=sop.effective_date,
        status=sop.status,
        comment=sop.revision_reason,
        checksum_sha256=stored_file.get("checksum_sha256"),
        uploaded_by_user_id=current_user.id if current_user else None,
    )


def optional_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def optional_text(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value or value.lower() == "none":
        return None
    return value
