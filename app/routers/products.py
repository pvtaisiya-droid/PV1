from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.database import get_db
from app.templating import templates


router = APIRouter()


@router.get("/products", response_class=HTMLResponse)
def products_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "products.html",
        {
            "request": request,
            "products": crud.list_products(db),
            "partners": crud.list_partners(db),
            "active_page": "products",
        },
    )


@router.post(
    "/products",
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def create_product_form(
    product_code: str = Form(...),
    product_name: str = Form(...),
    dosage_form: str | None = Form(None),
    strength: str | None = Form(None),
    route: str | None = Form(None),
    active_substance: str | None = Form(None),
    mah_partner_id: str | None = Form(None),
    authorization_number: str | None = Form(None),
    authorization_country_code: str | None = Form(None),
    authorization_status: str | None = Form(None),
    is_company_product: bool = Form(False),
    db: Session = Depends(get_db),
):
    crud.create_product(
        db,
        schemas.ProductCreate(
            product_code=product_code,
            product_name=product_name,
            dosage_form=dosage_form,
            strength=strength,
            route=route,
            active_substance=active_substance,
            mah_partner_id=mah_partner_id,
            authorization_number=authorization_number,
            authorization_country_code=authorization_country_code,
            authorization_status=authorization_status,
            is_company_product=is_company_product,
        ),
    )
    return RedirectResponse("/products", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/api/products", response_model=list[schemas.ProductRead])
def api_list_products(db: Session = Depends(get_db)):
    return crud.list_products(db)


@router.post(
    "/api/products",
    response_model=schemas.ProductRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def api_create_product(payload: schemas.ProductCreate, db: Session = Depends(get_db)):
    return crud.create_product(db, payload)
