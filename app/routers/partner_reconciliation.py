from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission, require_permission
from app.database import get_db
from app.reconciliation import (
    RECONCILIATION_STATUSES,
    build_reconciliation_preview,
    contact_full_name,
)
from app.reconciliation_excel import build_reconciliation_workbook
from app.routers.placeholders import store_document_file
from app.templating import templates
from app.ui_helpers import redirect_with_message


router = APIRouter()
RECONCILIATION_RECORD_STATUSES = [
    "draft",
    "sent",
    "response_received",
    "discrepancy_found",
    "closed",
]


@router.get("/partner-reconciliation", response_class=HTMLResponse)
def partner_reconciliation_page(
    request: Request,
    reconciliation_id: str | None = None,
    partner_id: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    contact_id: str | None = None,
    language: str = "ru",
    status_filter: str | None = None,
    product_id: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    context = base_context(request, db)
    context.update(
        {
            "status_options": RECONCILIATION_STATUSES,
            "record_status_options": RECONCILIATION_RECORD_STATUSES,
            "status_filter": status_filter or "",
            "product_filter": product_id or "",
            "search": search or "",
            "language": language,
            "selected_partner_id": partner_id or "",
            "selected_contact_id": contact_id or "",
            "period_start": period_start or date.today() - timedelta(days=90),
            "period_end": period_end or date.today(),
            "reconciliation": None,
            "preview": None,
            "our_items": [],
            "partner_items": [],
            "discrepancy_items": [],
            "summary": {},
        }
    )

    if reconciliation_id:
        reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail="Reconciliation not found")
        all_items = filter_items(
            list(reconciliation.items),
            status_filter=status_filter,
            product_id=product_id,
            search=search,
        )
        context.update(
            {
                "reconciliation": reconciliation,
                "selected_partner_id": reconciliation.partner_id,
                "selected_contact_id": reconciliation.contact_id or "",
                "period_start": reconciliation.period_start,
                "period_end": reconciliation.period_end,
                "language": reconciliation.language,
                "our_items": [item for item in all_items if item.source_side == "our_company"],
                "partner_items": [item for item in all_items if item.source_side == "partner"],
                "discrepancy_items": [
                    item
                    for item in all_items
                    if item.reconciliation_status not in {"matched", "confirmed"}
                ],
                "summary": {
                    "our_case_count": reconciliation.our_case_count,
                    "partner_case_count": reconciliation.partner_case_count,
                    "matched_count": reconciliation.matched_count,
                    "discrepancy_count": reconciliation.discrepancy_count,
                },
            }
        )
    elif partner_id and period_start and period_end:
        preview = build_reconciliation_preview(
            db,
            partner_id=partner_id,
            period_start=period_start,
            period_end=period_end,
            contact_id=contact_id,
            language=language,
        )
        preview["items"] = filter_items(
            preview["items"],
            status_filter=status_filter,
            product_id=product_id,
            search=search,
        )
        context.update(
            {
                "preview": preview,
                "our_items": [item for item in preview["items"] if item["source_side"] == "our_company"],
                "partner_items": [item for item in preview["items"] if item["source_side"] == "partner"],
                "discrepancy_items": [
                    item
                    for item in preview["items"]
                    if item["reconciliation_status"] not in {"matched", "confirmed"}
                ],
                "summary": preview["summary"],
            }
        )

    return templates.TemplateResponse(request, "partner_reconciliation.html", context)


@router.post("/partner-reconciliation/save", dependencies=[Depends(require_permission("create"))])
def save_partner_reconciliation(
    partner_id: str = Form(...),
    period_start: date = Form(...),
    period_end: date = Form(...),
    contact_id: str | None = Form(None),
    language: str = Form("ru"),
    prepared_by: str | None = Form(None),
    products: str | None = Form(None),
    db: Session = Depends(get_db),
):
    preview = build_reconciliation_preview(
        db,
        partner_id=partner_id,
        period_start=period_start,
        period_end=period_end,
        contact_id=contact_id,
        language=language,
    )
    reconciliation = crud.create_partner_reconciliation(
        db,
        schemas.PartnerReconciliationCreate(
            partner_id=partner_id,
            contact_id=contact_id or None,
            period_start=period_start,
            period_end=period_end,
            language=language,
            prepared_by=prepared_by,
            products=products or product_names(preview["products"]),
        ),
        preview["items"],
    )
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation.id}",
        message="Reconciliation saved.",
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/confirm",
    dependencies=[Depends(require_permission("approve"))],
)
def confirm_partner_reconciliation(
    reconciliation_id: str,
    confirmed_by_user: str | None = Form(None),
    db: Session = Depends(get_db),
):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    crud.confirm_partner_reconciliation(db, reconciliation, confirmed_by_user)
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Reconciliation confirmed.",
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/status",
    dependencies=[Depends(require_any_permission("edit", "comment"))],
)
def update_partner_reconciliation_status_form(
    reconciliation_id: str,
    request: Request,
    reconciliation_status: str = Form(...),
    sent_date: str | None = Form(None),
    response_date: str | None = Form(None),
    products: str | None = Form(None),
    discrepancy_description: str | None = Form(None),
    db: Session = Depends(get_db),
):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    if reconciliation_status not in RECONCILIATION_RECORD_STATUSES:
        return redirect_with_message(
            f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
            validation="Invalid reconciliation status.",
        )
    current_user = getattr(request.state, "current_user", None)
    crud.update_partner_reconciliation_status(
        db,
        reconciliation,
        reconciliation_status=reconciliation_status,
        sent_date=optional_date(sent_date),
        response_date=optional_date(response_date),
        products=products,
        discrepancy_description=discrepancy_description,
        changed_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Reconciliation status saved.",
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/response",
    dependencies=[Depends(require_any_permission("upload", "edit"))],
)
def upload_partner_reconciliation_response(
    reconciliation_id: str,
    request: Request,
    response_file: UploadFile = File(...),
    discrepancy_description: str | None = Form(None),
    db: Session = Depends(get_db),
):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    try:
        stored_file = store_document_file(response_file)
    except ValueError as exc:
        return redirect_with_message(
            f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
            validation=str(exc),
        )
    current_user = getattr(request.state, "current_user", None)
    document = crud.create_attachment(
        db,
        file_name=response_file.filename or f"partner_response_{reconciliation_id}",
        attachment_type="partner response",
        document_title=response_file.filename or "Partner response",
        document_type="partner response",
        related_object_type="Reconciliation",
        related_object_id=reconciliation.id,
        partner_id=reconciliation.partner_id,
        mime_type=response_file.content_type or "application/octet-stream",
        file_size_bytes=stored_file["file_size_bytes"],
        storage_path=stored_file["storage_path"],
        status="active",
        comment="Partner response file linked to reconciliation.",
        checksum_sha256=stored_file["checksum_sha256"],
        uploaded_by_user_id=current_user.id if current_user else None,
    )
    crud.update_partner_reconciliation_status(
        db,
        reconciliation,
        reconciliation_status=(
            "discrepancy_found"
            if (discrepancy_description or "").strip()
            else "response_received"
        ),
        response_date=date.today(),
        discrepancy_description=discrepancy_description,
        document_id=document.id,
        changed_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Partner response saved.",
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/task",
    dependencies=[Depends(require_any_permission("create", "create_related_requests", "comment"))],
)
def create_partner_reconciliation_task(
    reconciliation_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    current_user = getattr(request.state, "current_user", None)
    partner_name = reconciliation.partner.partner_name if reconciliation.partner else "partner"
    crud.create_task(
        db,
        schemas.TaskCreate(
            title=f"Resolve reconciliation discrepancies: {partner_name}",
            description=reconciliation.discrepancy_description,
            status="new",
            priority="high",
            due_date=date.today() + timedelta(days=7),
            responsible_person=reconciliation.prepared_by,
            related_entity_type="PartnerReconciliation",
            related_entity_id=reconciliation.id,
            comment="Created from reconciliation discrepancy.",
        ),
        created_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Task saved.",
    )


@router.post(
    "/partner-reconciliation/items/{item_id}",
    dependencies=[Depends(require_any_permission("edit", "comment"))],
)
def update_partner_reconciliation_item(
    item_id: str,
    internal_case_id: str | None = Form(None),
    reconciliation_status: str = Form(...),
    reviewer_comment: str | None = Form(None),
    confirmed_by_user: str | None = Form(None),
    db: Session = Depends(get_db),
):
    item = crud.get_partner_reconciliation_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Reconciliation item not found")
    if reconciliation_status not in RECONCILIATION_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid reconciliation status")
    crud.update_partner_reconciliation_item(
        db,
        item,
        schemas.PartnerReconciliationItemUpdate(
            internal_case_id=internal_case_id or None,
            reconciliation_status=reconciliation_status,
            reviewer_comment=reviewer_comment,
            confirmed_by_user=confirmed_by_user,
        ),
    )
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={item.reconciliation_id}",
        message="Reconciliation item saved.",
    )


@router.get("/partner-reconciliation/export", dependencies=[Depends(require_permission("export"))])
def export_partner_reconciliation(
    reconciliation_id: str | None = None,
    partner_id: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    contact_id: str | None = None,
    language: str = "ru",
    db: Session = Depends(get_db),
):
    if reconciliation_id:
        reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail="Reconciliation not found")
        our_items = [item for item in reconciliation.items if item.source_side == "our_company"]
        partner_items = [item for item in reconciliation.items if item.source_side == "partner"]
        discrepancy_items = [
            item
            for item in reconciliation.items
            if item.reconciliation_status not in {"matched", "confirmed"}
        ]
        content = build_reconciliation_workbook(
            partner_name=reconciliation.partner.partner_name,
            period_start=reconciliation.period_start,
            period_end=reconciliation.period_end,
            generated_at=date.today(),
            contact_name=reconciliation.contact_name,
            contact_email=reconciliation.contact_email,
            prepared_by=reconciliation.prepared_by,
            status=reconciliation.reconciliation_status,
            our_items=our_items,
            partner_items=partner_items,
            discrepancy_items=discrepancy_items,
            language=language or reconciliation.language,
        )
        filename = f"partner_reconciliation_{reconciliation.id}.xlsx"
    else:
        if not partner_id or not period_start or not period_end:
            raise HTTPException(status_code=400, detail="Partner and period are required")
        preview = build_reconciliation_preview(
            db,
            partner_id=partner_id,
            period_start=period_start,
            period_end=period_end,
            contact_id=contact_id,
            language=language,
        )
        content = build_reconciliation_workbook(
            partner_name=preview["partner"].partner_name,
            period_start=period_start,
            period_end=period_end,
            generated_at=date.today(),
            contact_name=contact_full_name(preview["contact"]),
            contact_email=preview["contact"].email if preview["contact"] else None,
            prepared_by="",
            status="draft",
            our_items=preview["our_items"],
            partner_items=preview["partner_items"],
            discrepancy_items=preview["discrepancy_items"],
            language=language,
        )
        filename = "partner_reconciliation_preview.xlsx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/partner-reconciliations", response_model=list[schemas.PartnerReconciliationRead])
def api_list_partner_reconciliations(db: Session = Depends(get_db)):
    return crud.list_partner_reconciliations(db)


@router.get(
    "/api/partner-reconciliations/{reconciliation_id}",
    response_model=schemas.PartnerReconciliationRead,
)
def api_get_partner_reconciliation(reconciliation_id: str, db: Session = Depends(get_db)):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    return reconciliation


def base_context(request: Request, db: Session) -> dict:
    latest = crud.get_latest_partner_reconciliation(db)
    return {
        "request": request,
        "active_page": "partner_reconciliation",
        "partners": crud.list_partners(db),
        "contacts": crud.list_contract_contacts(db),
        "products": crud.list_products(db),
        "cases": crud.list_cases(db),
        "reconciliations": crud.list_partner_reconciliations(db),
        "latest_reconciliation": latest,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
        "validation": request.query_params.get("validation"),
    }


def filter_items(
    items: list,
    *,
    status_filter: str | None,
    product_id: str | None,
    search: str | None,
) -> list:
    result = items
    if status_filter:
        result = [
            item
            for item in result
            if get_value(item, "reconciliation_status") == status_filter
        ]
    if product_id:
        result = [
            item
            for item in result
            if get_value(item, "product_id") == product_id
        ]
    if search:
        needle = search.lower()
        result = [
            item
            for item in result
            if needle
            in " ".join(
                str(get_value(item, field) or "")
                for field in [
                    "internal_case_number",
                    "partner_case_number",
                    "product_name",
                    "active_substance",
                    "patient",
                    "adverse_event",
                    "short_description",
                    "reviewer_comment",
                ]
            ).lower()
        ]
    return result


def get_value(item, field: str):
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def product_names(products: list) -> str:
    return ", ".join(product.product_name for product in products)


def optional_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)
