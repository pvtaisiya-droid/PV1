from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.concurrency import require_version
from app.database import get_db
from app.templating import templates
from app.ui_helpers import redirect_with_message


router = APIRouter()


@router.get("/contracts", response_class=HTMLResponse)
def contracts_page(request: Request, db: Session = Depends(get_db)):
    search = (request.query_params.get("search") or "").strip()
    selected_partner_id = (request.query_params.get("partner_id") or "").strip()
    selected_partner = crud.get_partner(db, selected_partner_id) if selected_partner_id else None
    contracts = crud.list_contracts(
        db,
        partner_id=selected_partner_id or None,
        search=search or None,
    )
    all_documents = crud.list_attachments(db)
    return templates.TemplateResponse(
        request,
        "contracts.html",
        {
            "request": request,
            "contracts": contracts,
            "selected_contract_tree": build_contract_tree(contracts) if selected_partner else [],
            "selected_partner": selected_partner,
            "base_contracts": crud.list_base_contracts(db),
            "partners": crud.list_partners(db),
            "products": crud.list_products(db),
            "accounting_services": crud.list_accounting_services(db),
            "accounting_tariffs_by_contract": {
                contract.id: crud.list_contract_accounting_tariffs(db, contract.id)
                for contract in contracts
            },
            "documents_by_contract": {
                contract.id: [
                    document
                    for document in all_documents
                    if document.related_object_type == "Contract"
                    and document.related_object_id == contract.id
                ]
                for contract in contracts
            },
            "filters": {
                "search": search,
                "partner_id": selected_partner_id,
            },
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
    product_id: str | None = Form(None),
    product_ids: list[str] | None = Form(None),
    contract_type: str = Form("pharmacovigilance_agreement"),
    parent_contract_id: str | None = Form(None),
    contract_number: str = Form(...),
    contract_date: date = Form(...),
    valid_until: date = Form(...),
    db: Session = Depends(get_db),
):
    try:
        selected_product_ids = selected_form_ids(product_ids, product_id)
        if contract_type != "additional_agreement":
            validate_contract_references(db, partner_id, selected_product_ids)
        crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=partner_id,
                product_id=selected_product_ids[0] if selected_product_ids else None,
                product_ids=selected_product_ids,
                parent_contract_id=parent_contract_id,
                contract_type=contract_type,
                contract_number=contract_number,
                contract_date=contract_date,
                valid_until=valid_until,
            ),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/contracts", validation=str(exc))
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/contracts", error="Contract could not be saved.")
    return redirect_with_message("/contracts", message="Contract saved.")


@router.post(
    "/contracts/{contract_id}/edit",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def edit_contract_form(
    contract_id: str,
    partner_id: str = Form(...),
    product_id: str | None = Form(None),
    product_ids: list[str] | None = Form(None),
    contract_type: str = Form("pharmacovigilance_agreement"),
    parent_contract_id: str | None = Form(None),
    contract_number: str = Form(...),
    contract_date: date = Form(...),
    valid_until: date = Form(...),
    is_active: bool = Form(False),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    contract = crud.get_contract(db, contract_id)
    if not contract:
        return redirect_with_message("/contracts", error="Contract not found.")
    require_version(contract, expected_version)
    try:
        if is_active:
            today = date.today()
            if contract_date > today:
                contract_date = today
            if valid_until < today:
                valid_until = today
        selected_product_ids = selected_form_ids(product_ids, product_id)
        if contract_type != "additional_agreement":
            validate_contract_references(db, partner_id, selected_product_ids)
        crud.update_contract(
            db,
            contract,
            schemas.ContractUpdate(
                partner_id=partner_id,
                product_id=selected_product_ids[0] if selected_product_ids else None,
                product_ids=selected_product_ids,
                parent_contract_id=parent_contract_id,
                contract_type=contract_type,
                contract_number=contract_number,
                contract_date=contract_date,
                valid_until=valid_until,
            ),
            is_active=is_active,
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/contracts", validation=str(exc))
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/contracts", error="Contract could not be saved.")
    return redirect_with_message("/contracts", message="Contract saved.")


@router.post(
    "/contracts/{contract_id}/delete",
    dependencies=[Depends(require_any_permission("manage_reference_data", "soft_delete"))],
)
def delete_contract_form(
    contract_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    contract = crud.get_contract(db, contract_id)
    if not contract:
        return redirect_with_message("/contracts", error="Contract not found.")
    require_version(contract, expected_version)
    current_user = getattr(request.state, "current_user", None)
    try:
        crud.delete_contract(
            db,
            contract,
            deleted_by_user_id=current_user.id if current_user else None,
            delete_reason=delete_reason,
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/contracts", validation=str(exc))
    return redirect_with_message("/contracts", message="Contract deleted.")


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
    try:
        if payload.contract_type != "additional_agreement":
            validate_contract_references(db, payload.partner_id, payload.product_ids)
        return crud.create_contract(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/contracts/{contract_id}", response_model=schemas.ContractRead)
def api_get_contract(contract_id: str, db: Session = Depends(get_db)):
    contract = crud.get_contract(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


@router.put(
    "/api/contracts/{contract_id}",
    response_model=schemas.ContractRead,
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def api_update_contract(
    contract_id: str,
    payload: schemas.ContractUpdate,
    expected_version: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    contract = crud.get_contract(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    require_version(contract, expected_version)
    try:
        if payload.contract_type != "additional_agreement":
            validate_contract_references(db, payload.partner_id, payload.product_ids)
        return crud.update_contract(db, contract, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete(
    "/api/contracts/{contract_id}",
    response_model=schemas.ContractRead,
    dependencies=[Depends(require_any_permission("manage_reference_data", "soft_delete"))],
)
def api_delete_contract(
    contract_id: str,
    request: Request,
    expected_version: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    contract = crud.get_contract(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    require_version(contract, expected_version)
    current_user = getattr(request.state, "current_user", None)
    try:
        return crud.delete_contract(
            db,
            contract,
            deleted_by_user_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def selected_form_ids(values: list[str] | None, first_value: str | None = None) -> list[str]:
    selected_ids: list[str] = []
    first_value = (first_value or "").strip()
    if first_value:
        selected_ids.append(first_value)
    for value in values or []:
        value = (value or "").strip()
        if value and value not in selected_ids:
            selected_ids.append(value)
    return selected_ids


def build_contract_tree(contracts: list) -> list[dict]:
    additional_by_parent: dict[str, list] = {}
    unattached: list = []
    base_contracts: list = []
    for contract in contracts:
        if contract.contract_type == "additional_agreement":
            if contract.parent_contract_id:
                additional_by_parent.setdefault(contract.parent_contract_id, []).append(contract)
            else:
                unattached.append(contract)
            continue
        base_contracts.append(contract)

    tree = [
        {
            "contract": contract,
            "additional_agreements": additional_by_parent.pop(contract.id, []),
        }
        for contract in base_contracts
    ]
    for rows in additional_by_parent.values():
        unattached.extend(rows)
    if unattached:
        tree.append({"contract": None, "additional_agreements": unattached})
    return tree


def validate_contract_references(db: Session, partner_id: str, product_ids: list[str]) -> None:
    if not crud.get_partner(db, partner_id):
        raise HTTPException(status_code=404, detail="Partner not found")
    for product_id in product_ids:
        if not crud.get_product(db, product_id):
            raise HTTPException(status_code=404, detail="Product not found")
