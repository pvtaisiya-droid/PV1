from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.database import get_db
from app.templating import templates


router = APIRouter()


@router.get("/contract-contacts", response_class=HTMLResponse)
def contract_contacts_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "contract_contacts.html",
        {
            "request": request,
            "contacts": crud.list_contract_contacts(db),
            "partners": crud.list_partners(db),
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
    validate_partner(db, partner_id)
    crud.create_contract_contact(
        db,
        schemas.ContractContactCreate(
            partner_id=partner_id,
            last_name=last_name,
            first_name=first_name,
            patronymic=patronymic,
            email=email,
            position=position,
            is_current=is_current,
        ),
    )
    return RedirectResponse("/contract-contacts", status_code=status.HTTP_303_SEE_OTHER)


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
    return crud.create_contract_contact(db, payload)


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
