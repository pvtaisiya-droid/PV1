from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.database import get_db
from app.templating import templates


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
    crud.create_substance(
        db,
        schemas.SubstanceCreate(
            substance_name=substance_name,
            inn_name=inn_name,
            cas_number=cas_number,
            atc_code=atc_code,
            substance_type=substance_type,
        ),
    )
    return RedirectResponse("/substances", status_code=status.HTTP_303_SEE_OTHER)


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
    return RedirectResponse("/substances", status_code=status.HTTP_303_SEE_OTHER)


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
