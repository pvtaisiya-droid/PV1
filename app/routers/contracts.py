from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.database import get_db
from app.templating import templates


router = APIRouter()


@router.get("/contracts", response_class=HTMLResponse)
def contracts_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "contracts.html",
        {
            "request": request,
            "contracts": crud.list_contracts(db),
            "partners": crud.list_partners(db),
            "products": crud.list_products(db),
            "active_page": "contracts",
        },
    )


@router.post(
    "/contracts",
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def create_contract_form(
    partner_id: str = Form(...),
    product_id: str = Form(...),
    contract_type: str = Form("pharmacovigilance_agreement"),
    contract_number: str = Form(...),
    contract_date: date = Form(...),
    valid_until: date = Form(...),
    db: Session = Depends(get_db),
):
    validate_contract_references(db, partner_id, product_id)
    crud.create_contract(
        db,
        schemas.ContractCreate(
            partner_id=partner_id,
            product_id=product_id,
            contract_type=contract_type,
            contract_number=contract_number,
            contract_date=contract_date,
            valid_until=valid_until,
        ),
    )
    return RedirectResponse("/contracts", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/api/contracts", response_model=list[schemas.ContractRead])
def api_list_contracts(db: Session = Depends(get_db)):
    return crud.list_contracts(db)


@router.post(
    "/api/contracts",
    response_model=schemas.ContractRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def api_create_contract(payload: schemas.ContractCreate, db: Session = Depends(get_db)):
    validate_contract_references(db, payload.partner_id, payload.product_id)
    return crud.create_contract(db, payload)


@router.get("/api/contracts/{contract_id}", response_model=schemas.ContractRead)
def api_get_contract(contract_id: str, db: Session = Depends(get_db)):
    contract = crud.get_contract(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


def validate_contract_references(db: Session, partner_id: str, product_id: str) -> None:
    if not crud.get_partner(db, partner_id):
        raise HTTPException(status_code=404, detail="Partner not found")
    if not crud.get_product(db, product_id):
        raise HTTPException(status_code=404, detail="Product not found")
