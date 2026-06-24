from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.database import get_db
from app.templating import templates
from app.ui_helpers import redirect_with_message


router = APIRouter()


@router.get("/substances", response_class=HTMLResponse)
def substances_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "substances.html",
        {
            "request": request,
            "substances": crud.list_substances(db),
            "products": crud.list_products(db),
            "active_page": "substances",
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/substances",
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def create_substance_form(
    substance_name: str = Form(...),
    inn_name: str | None = Form(None),
    cas_number: str | None = Form(None),
    atc_code: str | None = Form(None),
    substance_type: str | None = Form("active"),
    db: Session = Depends(get_db),
):
    if not substance_name.strip():
        return redirect_with_message("/substances", validation="Substance name is required.")
    try:
        crud.create_substance(
            db,
            schemas.SubstanceCreate(
                substance_name=substance_name.strip(),
                inn_name=inn_name,
                cas_number=cas_number,
                atc_code=atc_code,
                substance_type=substance_type,
            ),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/substances", error="Substance could not be saved.")
    return redirect_with_message("/substances", message="Substance saved.")


@router.post(
    "/substances/{substance_id}/edit",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def edit_substance_form(
    substance_id: str,
    substance_name: str = Form(...),
    inn_name: str | None = Form(None),
    cas_number: str | None = Form(None),
    atc_code: str | None = Form(None),
    substance_type: str | None = Form("active"),
    db: Session = Depends(get_db),
):
    substance = crud.get_substance(db, substance_id)
    if not substance:
        return redirect_with_message("/substances", error="Substance not found.")
    if not substance_name.strip():
        return redirect_with_message("/substances", validation="Substance name is required.")
    try:
        crud.update_substance(
            db,
            substance,
            schemas.SubstanceCreate(
                substance_name=substance_name.strip(),
                inn_name=inn_name,
                cas_number=cas_number,
                atc_code=atc_code,
                substance_type=substance_type,
            ),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/substances", error="Substance could not be saved.")
    return redirect_with_message("/substances", message="Substance saved.")


@router.post(
    "/substances/{substance_id}/delete",
    dependencies=[Depends(require_any_permission("manage_reference_data", "soft_delete"))],
)
def delete_substance_form(
    substance_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    db: Session = Depends(get_db),
):
    substance = crud.get_substance(db, substance_id)
    if not substance:
        return redirect_with_message("/substances", error="Substance not found.")
    current_user = getattr(request.state, "current_user", None)
    crud.delete_substance(
        db,
        substance,
        deleted_by_user_id=current_user.id if current_user else None,
        delete_reason=delete_reason,
    )
    return redirect_with_message("/substances", message="Substance deleted.")


@router.post(
    "/product-substances",
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def create_product_substance_form(
    product_id: str = Form(...),
    substance_id: str = Form(...),
    substance_role: str = Form("active"),
    is_primary: bool = Form(True),
    db: Session = Depends(get_db),
):
    validate_product_substance_references(db, product_id, substance_id)
    crud.create_product_substance(
        db,
        schemas.ProductSubstanceCreate(
            product_id=product_id,
            substance_id=substance_id,
            substance_role=substance_role,
            is_primary=is_primary,
        ),
    )
    return redirect_with_message("/substances", message="Substance linked to product.")


@router.get("/api/substances", response_model=list[schemas.SubstanceRead])
def api_list_substances(db: Session = Depends(get_db)):
    return crud.list_substances(db)


@router.post(
    "/api/substances",
    response_model=schemas.SubstanceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def api_create_substance(payload: schemas.SubstanceCreate, db: Session = Depends(get_db)):
    return crud.create_substance(db, payload)


@router.get("/api/substances/{substance_id}", response_model=schemas.SubstanceRead)
def api_get_substance(substance_id: str, db: Session = Depends(get_db)):
    substance = crud.get_substance(db, substance_id)
    if not substance:
        raise HTTPException(status_code=404, detail="Substance not found")
    return substance


@router.put(
    "/api/substances/{substance_id}",
    response_model=schemas.SubstanceRead,
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def api_update_substance(
    substance_id: str,
    payload: schemas.SubstanceCreate,
    db: Session = Depends(get_db),
):
    substance = crud.get_substance(db, substance_id)
    if not substance:
        raise HTTPException(status_code=404, detail="Substance not found")
    return crud.update_substance(db, substance, payload)


@router.delete(
    "/api/substances/{substance_id}",
    response_model=schemas.SubstanceRead,
    dependencies=[Depends(require_any_permission("manage_reference_data", "soft_delete"))],
)
def api_delete_substance(
    substance_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    substance = crud.get_substance(db, substance_id)
    if not substance:
        raise HTTPException(status_code=404, detail="Substance not found")
    current_user = getattr(request.state, "current_user", None)
    return crud.delete_substance(
        db,
        substance,
        deleted_by_user_id=current_user.id if current_user else None,
    )


@router.post(
    "/api/product-substances",
    response_model=schemas.ProductSubstanceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def api_create_product_substance(
    payload: schemas.ProductSubstanceCreate,
    db: Session = Depends(get_db),
):
    validate_product_substance_references(db, payload.product_id, payload.substance_id)
    return crud.create_product_substance(db, payload)


def validate_product_substance_references(
    db: Session,
    product_id: str,
    substance_id: str,
) -> None:
    if not crud.get_product(db, product_id):
        raise HTTPException(status_code=404, detail="Product not found")
    if not crud.get_substance(db, substance_id):
        raise HTTPException(status_code=404, detail="Substance not found")
