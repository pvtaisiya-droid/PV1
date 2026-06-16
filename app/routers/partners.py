from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.database import get_db
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, redirect_with_message


router = APIRouter()
PARTNER_STATUS_OPTIONS = ["archive", "fn", "registration_in_progress"]


@router.get("/partners", response_class=HTMLResponse)
def partners_page(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
):
    all_partners = crud.list_partners(db)
    partners = [
        partner
        for partner in all_partners
        if contains_search(search, partner.partner_code, partner.partner_name, partner.email)
        and (not status_filter or partner.partner_type == status_filter)
    ]
    filters = {
        "search": search or "",
        "status_filter": status_filter or "",
        "active": active_filters(search=search, status_filter=status_filter),
    }
    return templates.TemplateResponse(
        request,
        "partners.html",
        {
            "request": request,
            "partners": partners,
            "status_options": PARTNER_STATUS_OPTIONS,
            "filters": filters,
            "total_count": len(all_partners),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "active_page": "partners",
        },
    )


@router.post(
    "/partners",
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def create_partner_form(
    partner_code: str = Form(...),
    partner_name: str = Form(...),
    partner_type: str = Form("fn"),
    reconciliation_frequency: str = Form("not_conducted"),
    db: Session = Depends(get_db),
):
    if not partner_code.strip() or not partner_name.strip():
        return redirect_with_message("/partners", validation="Partner code and name are required.")
    try:
        crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=partner_code.strip(),
                partner_name=partner_name.strip(),
                partner_type=partner_type,
                reconciliation_frequency=reconciliation_frequency,
            ),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/partners", error="Partner could not be saved.")
    return redirect_with_message("/partners", message="Partner saved.")


@router.post(
    "/partners/{partner_id}/edit",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def edit_partner_form(
    partner_id: str,
    partner_code: str = Form(...),
    partner_name: str = Form(...),
    partner_type: str = Form("fn"),
    reconciliation_frequency: str = Form("not_conducted"),
    db: Session = Depends(get_db),
):
    partner = crud.get_partner(db, partner_id)
    if not partner:
        return redirect_with_message("/partners", error="Partner not found.")
    if not partner_code.strip() or not partner_name.strip():
        return redirect_with_message("/partners", validation="Partner code and name are required.")
    try:
        crud.update_partner(
            db,
            partner,
            schemas.PartnerCreate(
                partner_code=partner_code.strip(),
                partner_name=partner_name.strip(),
                partner_type=partner_type,
                reconciliation_frequency=reconciliation_frequency,
            ),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/partners", error="Partner could not be saved.")
    return redirect_with_message("/partners", message="Partner saved.")


@router.post(
    "/partners/{partner_id}/delete",
    dependencies=[Depends(require_any_permission("manage_reference_data", "soft_delete"))],
)
def delete_partner_form(
    partner_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    db: Session = Depends(get_db),
):
    partner = crud.get_partner(db, partner_id)
    if not partner:
        return redirect_with_message("/partners", error="Partner not found.")
    current_user = getattr(request.state, "current_user", None)
    crud.delete_partner(
        db,
        partner,
        deleted_by_user_id=current_user.id if current_user else None,
        delete_reason=delete_reason,
    )
    return redirect_with_message("/partners", message="Partner deleted.")


@router.get("/partners/{partner_id}", response_class=HTMLResponse)
def partner_detail_page(
    partner_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    partner = crud.get_partner(db, partner_id)
    if not partner:
        return redirect_with_message("/partners", error="Partner not found.")
    contacts = [
        contact for contact in crud.list_contract_contacts(db) if contact.partner_id == partner.id
    ]
    contracts = [
        contract for contract in crud.list_contracts(db) if contract.partner_id == partner.id
    ]
    contract_product_ids = {contract.product_id for contract in contracts}
    products = [
        product
        for product in crud.list_products(db)
        if product.mah_partner_id == partner.id or product.id in contract_product_ids
    ]
    reconciliations = [
        reconciliation
        for reconciliation in crud.list_partner_reconciliations(db)
        if reconciliation.partner_id == partner.id
    ]
    documents = [
        document
        for document in crud.list_attachments(db)
        if document_partner_id(document) == partner.id
    ]
    tasks = [
        task
        for task in crud.list_tasks(db)
        if task.related_entity_type in {"Partner", "partner"}
        and task.related_entity_id == partner.id
    ]
    audit_entries = [
        entry
        for entry in crud.list_audit_entries(db)
        if entry.entity_type == "Partner" and entry.entity_id == partner.id
    ]
    return templates.TemplateResponse(
        request,
        "partner_detail.html",
        {
            "request": request,
            "active_page": "partners",
            "partner": partner,
            "contacts": contacts,
            "products": products,
            "contracts": contracts,
            "reconciliations": reconciliations,
            "documents": documents,
            "tasks": tasks,
            "audit_entries": audit_entries,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.get("/api/partners", response_model=list[schemas.PartnerRead])
def api_list_partners(db: Session = Depends(get_db)):
    return crud.list_partners(db)


@router.post(
    "/api/partners",
    response_model=schemas.PartnerRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def api_create_partner(payload: schemas.PartnerCreate, db: Session = Depends(get_db)):
    return crud.create_partner(db, payload)


@router.get("/api/partners/{partner_id}", response_model=schemas.PartnerRead)
def api_get_partner(partner_id: str, db: Session = Depends(get_db)):
    partner = crud.get_partner(db, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    return partner


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
