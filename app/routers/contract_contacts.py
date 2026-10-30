from app import models
from app.concurrency import protect_record
from app.unit_of_work import atomic_write
from fastapi import Query, APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.concurrency import require_version
from app.database import get_db
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, redirect_with_message


router = APIRouter()


CONTACT_TYPE_OPTIONS = [
    ("pv", "Pharmacovigilance"),
    ("quality", "Quality"),
    ("regulatory", "Regulatory affairs"),
    ("medical_information", "Medical information"),
    ("manufacturing_site", "Manufacturing site"),
    ("logistics_supply", "Logistics / supply"),
    ("legal_contracts", "Contracts / legal"),
    ("management", "Management / director"),
    ("administrative", "Administrative contact"),
    ("accounting", "Accounting / billing"),
    ("other", "Other"),
]
CONTACT_TYPE_VALUES = {value for value, _label in CONTACT_TYPE_OPTIONS}

RECONCILIATION_PARTICIPATION_OPTIONS = [
    ("primary", "Main reconciliation contact"),
    ("cc", "Reconciliation copy"),
    ("none", "Does not participate"),
]
RECONCILIATION_PARTICIPATION_VALUES = {
    value for value, _label in RECONCILIATION_PARTICIPATION_OPTIONS
}


@router.get("/contract-contacts", response_class=HTMLResponse)
def contract_contacts_page(
    request: Request,
    search: str | None = None,
    partner_id: str | None = None,
    status_filter: str | None = None,
    duplicate_id: str | None = None,
    db: Session = Depends(get_db),
):
    all_contacts = crud.list_contract_contacts(db)
    partner_contact_preview = sorted(
        all_contacts,
        key=lambda contact: (
            not contact.is_current,
            (contact.last_name or "").lower(),
            (contact.first_name or "").lower(),
            (contact.patronymic or "").lower(),
        ),
    )
    duplicate_contact = crud.get_contract_contact(db, duplicate_id) if duplicate_id else None
    contacts = [
        contact
        for contact in all_contacts
        if contains_search(
            search,
            contact.last_name,
            contact.first_name,
            contact.patronymic,
            contact.email,
            contact.position,
            contact.mobile_phone,
            contact.work_phone,
            contact.extension_phone,
            contact.company_website,
            contact.address,
            contact.partner.partner_name if contact.partner else "",
        )
        and (not partner_id or contact.partner_id == partner_id)
        and (
            not status_filter
            or (status_filter == "current" and contact.is_current)
            or (status_filter == "inactive" and not contact.is_current)
        )
    ]
    all_documents = crud.list_attachments(db)
    filters = {
        "search": search or "",
        "partner_id": partner_id or "",
        "status_filter": status_filter or "",
        "active": active_filters(
            search=search,
            partner_id=partner_id,
            status_filter=status_filter,
        ),
    }
    return templates.TemplateResponse(
        request,
        "contract_contacts.html",
        {
            "request": request,
            "contacts": contacts,
            "all_contacts": all_contacts,
            "partner_contact_preview": partner_contact_preview,
            "partners": crud.list_partners(db),
            "filters": filters,
            "total_count": len(all_contacts),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "active_page": "contract_contacts",
            "duplicate_contact": duplicate_contact,
            "contact_type_options": CONTACT_TYPE_OPTIONS,
            "reconciliation_participation_options": RECONCILIATION_PARTICIPATION_OPTIONS,
            "contact_type_labels": dict(CONTACT_TYPE_OPTIONS),
            "documents_by_contact": {
                contact.id: [
                    document
                    for document in all_documents
                    if document.related_object_type == "PartnerResponsiblePerson"
                    and document.related_object_id == contact.id
                ]
                for contact in contacts
            },
        },
    )


@router.post(
    "/contract-contacts",
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
@atomic_write
def create_contract_contact_form(
    partner_id: str = Form(...),
    last_name: str = Form(...),
    first_name: str = Form(...),
    patronymic: str | None = Form(None),
    email: str | None = Form(None),
    position: str = Form(...),
    mobile_phone: str | None = Form(None),
    work_phone: str | None = Form(None),
    extension_phone: str | None = Form(None),
    company_website: str | None = Form(None),
    address: str | None = Form(None),
    contact_status: str = Form("current"),
    reconciliation_participation: str = Form("primary"),
    contact_type: str | None = Form("pv"),
    is_billing_contact: str | None = Form(None),
    comments: str | None = Form(None),
    expected_partner_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Partner, partner_id, expected_partner_version)
    try:
        validate_partner(db, partner_id)
        crud.create_contract_contact(
            db,
            build_contract_contact_payload(
                partner_id=partner_id,
                last_name=last_name,
                first_name=first_name,
                patronymic=patronymic,
                email=email,
                position=position,
                mobile_phone=mobile_phone,
                work_phone=work_phone,
                extension_phone=extension_phone,
                company_website=company_website,
                address=address,
                contact_status=contact_status,
                reconciliation_participation=reconciliation_participation,
                contact_type=contact_type,
                is_billing_contact=is_billing_contact == "yes",
                comments=comments,
            ),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/contract-contacts", validation=str(exc))
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/contract-contacts", error="Contact could not be saved.")
    return redirect_with_message("/contract-contacts", message="Contact saved.")


@router.post(
    "/contract-contacts/{contact_id}/edit",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def edit_contract_contact_form(
    contact_id: str,
    partner_id: str = Form(...),
    last_name: str = Form(...),
    first_name: str = Form(...),
    patronymic: str | None = Form(None),
    email: str | None = Form(None),
    position: str = Form(...),
    mobile_phone: str | None = Form(None),
    work_phone: str | None = Form(None),
    extension_phone: str | None = Form(None),
    company_website: str | None = Form(None),
    address: str | None = Form(None),
    contact_status: str = Form("current"),
    reconciliation_participation: str = Form("primary"),
    contact_type: str | None = Form("pv"),
    is_billing_contact: str | None = Form(None),
    comments: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    contact = crud.get_contract_contact(db, contact_id)
    if not contact:
        return redirect_with_message("/contract-contacts", error="Contact not found.")
    require_version(contact, expected_version)
    try:
        validate_partner(db, partner_id)
        crud.update_contract_contact(
            db,
            contact,
            build_contract_contact_payload(
                partner_id=partner_id,
                last_name=last_name,
                first_name=first_name,
                patronymic=patronymic,
                email=email,
                position=position,
                mobile_phone=mobile_phone,
                work_phone=work_phone,
                extension_phone=extension_phone,
                company_website=company_website,
                address=address,
                contact_status=contact_status,
                reconciliation_participation=reconciliation_participation,
                contact_type=contact_type,
                is_billing_contact=is_billing_contact == "yes",
                comments=comments,
            ),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/contract-contacts", validation=str(exc))
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/contract-contacts", error="Contact could not be saved.")
    return redirect_with_message("/contract-contacts", message="Contact saved.")


@router.post(
    "/contract-contacts/{contact_id}/delete",
    dependencies=[Depends(require_any_permission("manage_reference_data", "soft_delete"))],
)
def delete_contract_contact_form(
    contact_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    contact = crud.get_contract_contact(db, contact_id)
    if not contact:
        return redirect_with_message("/contract-contacts", error="Contact not found.")
    require_version(contact, expected_version)
    current_user = getattr(request.state, "current_user", None)
    crud.delete_contract_contact(
        db,
        contact,
        deleted_by_user_id=current_user.id if current_user else None,
        delete_reason=delete_reason,
    )
    return redirect_with_message("/contract-contacts", message="Contact deleted.")


@router.get(
    "/api/contract-contacts",
    response_model=list[schemas.ContractContactRead],
)
def api_list_contract_contacts(db: Session = Depends(get_db)):
    return crud.list_contract_contacts(db)


@router.post(
    "/api/contract-contacts",
    response_model=schemas.ContractContactRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
@atomic_write
def api_create_contract_contact(
    payload: schemas.ContractContactCreate,
    expected_partner_version: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Partner, payload.partner_id, expected_partner_version)
    validate_partner(db, payload.partner_id)
    try:
        return crud.create_contract_contact(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/api/contract-contacts/{contact_id}",
    response_model=schemas.ContractContactRead,
)
def api_get_contract_contact(contact_id: str, db: Session = Depends(get_db)):
    contact = crud.get_contract_contact(db, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contract contact not found")
    return contact


def validate_partner(db: Session, partner_id: str) -> None:
    if not crud.get_partner(db, partner_id):
        raise HTTPException(status_code=404, detail="Partner not found")


def optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def build_contract_contact_payload(
    *,
    partner_id: str,
    last_name: str,
    first_name: str,
    patronymic: str | None,
    email: str | None,
    position: str,
    mobile_phone: str | None,
    work_phone: str | None,
    extension_phone: str | None,
    company_website: str | None,
    address: str | None,
    contact_status: str,
    reconciliation_participation: str,
    contact_type: str | None,
    is_billing_contact: bool,
    comments: str | None,
) -> schemas.ContractContactCreate:
    last_name_value = last_name.strip()
    first_name_value = first_name.strip()
    position_value = position.strip()
    if not last_name_value or not first_name_value:
        raise ValueError("Last name and first name are required.")
    if not position_value:
        raise ValueError("Position is required.")

    contact_type_value = optional_text(contact_type) or "pv"
    if contact_type_value not in CONTACT_TYPE_VALUES:
        raise ValueError("Invalid contact type.")
    if contact_status not in {"current", "inactive"}:
        raise ValueError("Invalid contact status.")
    if reconciliation_participation not in RECONCILIATION_PARTICIPATION_VALUES:
        raise ValueError("Invalid reconciliation participation.")

    is_current = contact_status == "current"
    participation = reconciliation_participation if is_current else "none"

    return schemas.ContractContactCreate(
        partner_id=partner_id,
        last_name=last_name_value,
        first_name=first_name_value,
        patronymic=optional_text(patronymic),
        email=optional_text(email),
        position=position_value,
        mobile_phone=optional_text(mobile_phone),
        work_phone=optional_text(work_phone),
        extension_phone=optional_text(extension_phone),
        company_website=optional_text(company_website),
        address=optional_text(address),
        is_current=is_current,
        is_pv_contact=contact_type_value == "pv",
        is_reconciliation_recipient=participation == "primary",
        cc_reconciliation=participation == "cc",
        is_primary=participation == "primary",
        is_billing_contact=is_billing_contact,
        contact_type=contact_type_value,
        comments=optional_text(comments),
    )
