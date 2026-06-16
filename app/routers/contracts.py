from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.database import get_db
from app.templating import templates
from app.ui_helpers import redirect_with_message


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
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
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
    try:
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
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/contracts", validation=str(exc))
    return redirect_with_message("/contracts", message="Contract saved.")


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
    try:
        return crud.create_contract(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
