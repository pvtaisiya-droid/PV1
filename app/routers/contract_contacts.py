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


@router.get("/contract-contacts", response_class=HTMLResponse)
def contract_contacts_page(
    request: Request,
    search: str | None = None,
    partner_id: str | None = None,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
):
    all_contacts = crud.list_contract_contacts(db)
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
            contact.partner.partner_name if contact.partner else "",
        )
        and (not partner_id or contact.partner_id == partner_id)
        and (
            not status_filter
            or (status_filter == "current" and contact.is_current)
            or (status_filter == "inactive" and not contact.is_current)
        )
    ]
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
            "partners": crud.list_partners(db),
            "filters": filters,
            "total_count": len(all_contacts),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "active_page": "contract_contacts",
        },
    )


@router.post(
    "/contract-contacts",
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def create_contract_contact_form(
    partner_id: str = Form(...),
    last_name: str = Form(...),
    first_name: str = Form(...),
    patronymic: str | None = Form(None),
    email: str = Form(...),
    position: str = Form(...),
    is_current: bool = Form(True),
    db: Session = Depends(get_db),
):
    if not last_name.strip() or not first_name.strip() or not email.strip():
        return redirect_with_message(
            "/contract-contacts",
            validation="Last name, first name and email are required.",
        )
    try:
        validate_partner(db, partner_id)
        crud.create_contract_contact(
            db,
            schemas.ContractContactCreate(
                partner_id=partner_id,
                last_name=last_name.strip(),
                first_name=first_name.strip(),
                patronymic=patronymic,
                email=email.strip(),
                position=position.strip(),
                is_current=is_current,
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
    email: str = Form(...),
    position: str = Form(...),
    is_current: bool = Form(True),
    db: Session = Depends(get_db),
):
    contact = crud.get_contract_contact(db, contact_id)
    if not contact:
        return redirect_with_message("/contract-contacts", error="Contact not found.")
    if not last_name.strip() or not first_name.strip() or not email.strip():
        return redirect_with_message(
            "/contract-contacts",
            validation="Last name, first name and email are required.",
        )
    try:
        validate_partner(db, partner_id)
        crud.update_contract_contact(
            db,
            contact,
            schemas.ContractContactCreate(
                partner_id=partner_id,
                last_name=last_name.strip(),
                first_name=first_name.strip(),
                patronymic=patronymic,
                email=email.strip(),
                position=position.strip(),
                is_current=is_current,
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
    db: Session = Depends(get_db),
):
    contact = crud.get_contract_contact(db, contact_id)
    if not contact:
        return redirect_with_message("/contract-contacts", error="Contact not found.")
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
def api_create_contract_contact(
    payload: schemas.ContractContactCreate,
    db: Session = Depends(get_db),
):
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
