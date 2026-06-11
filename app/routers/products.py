from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, schemas
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
            "substances": crud.list_substances(db),
            "active_page": "products",
        },
    )


@router.post("/products")
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


@router.post("/substances")
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
    return RedirectResponse("/products", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/api/products", response_model=list[schemas.ProductRead])
def api_list_products(db: Session = Depends(get_db)):
    return crud.list_products(db)


@router.post("/api/products", response_model=schemas.ProductRead, status_code=status.HTTP_201_CREATED)
def api_create_product(payload: schemas.ProductCreate, db: Session = Depends(get_db)):
    return crud.create_product(db, payload)


@router.get("/api/substances", response_model=list[schemas.SubstanceRead])
def api_list_substances(db: Session = Depends(get_db)):
    return crud.list_substances(db)


@router.post("/api/substances", response_model=schemas.SubstanceRead, status_code=status.HTTP_201_CREATED)
def api_create_substance(payload: schemas.SubstanceCreate, db: Session = Depends(get_db)):
    return crud.create_substance(db, payload)


@router.post(
    "/api/product-substances",
    response_model=schemas.ProductSubstanceRead,
    status_code=status.HTTP_201_CREATED,
)
def api_create_product_substance(
    payload: schemas.ProductSubstanceCreate,
    db: Session = Depends(get_db),
):
    product = crud.get_product(db, payload.product_id)
    substance = crud.get_substance(db, payload.substance_id)
    if not product or not substance:
        raise HTTPException(status_code=404, detail="Product or substance not found")
    return crud.create_product_substance(db, payload)
