from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.database import get_db
from app.pagination import paginate_items
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, redirect_with_message, unique_values


router = APIRouter()


@router.get("/products", response_class=HTMLResponse)
def products_page(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    partner_id: str | None = None,
    page: int = 1,
    per_page: int | None = None,
    db: Session = Depends(get_db),
):
    all_products = crud.list_products(db)
    products = [
        product
        for product in all_products
        if contains_search(
            search,
            product.product_code,
            product.product_name,
            product.authorization_number,
            product.authorization_country_code,
            ", ".join(
                link.substance.substance_name
                for link in product.substance_links
                if link.substance
            ),
        )
        and (not status_filter or product.authorization_status == status_filter)
        and (not partner_id or product.mah_partner_id == partner_id)
    ]
    filters = {
        "search": search or "",
        "status_filter": status_filter or "",
        "partner_id": partner_id or "",
        "active": active_filters(
            search=search,
            status_filter=status_filter,
            partner_id=partner_id,
        ),
    }
    page_products, pagination = paginate_items(products, page, per_page)
    return templates.TemplateResponse(
        request,
        "products.html",
        {
            "request": request,
            "products": page_products,
            "partners": crud.list_partners(db),
            "status_options": unique_values(
                [product.authorization_status for product in all_products]
            ),
            "filters": filters,
            "total_count": len(all_products),
            "filtered_count": len(products),
            "pagination": pagination,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
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
    if not product_code.strip() or not product_name.strip():
        return redirect_with_message("/products", validation="Product code and name are required.")
    try:
        crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=product_code.strip(),
                product_name=product_name.strip(),
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
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/products", error="Product could not be saved.")
    return redirect_with_message("/products", message="Product saved.")


@router.post(
    "/products/{product_id}/edit",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def edit_product_form(
    product_id: str,
    product_code: str = Form(...),
    product_name: str = Form(...),
    dosage_form: str | None = Form(None),
    strength: str | None = Form(None),
    route: str | None = Form(None),
    mah_partner_id: str | None = Form(None),
    authorization_number: str | None = Form(None),
    authorization_country_code: str | None = Form(None),
    authorization_status: str | None = Form(None),
    is_company_product: bool = Form(False),
    db: Session = Depends(get_db),
):
    product = crud.get_product(db, product_id)
    if not product:
        return redirect_with_message("/products", error="Product not found.")
    if not product_code.strip() or not product_name.strip():
        return redirect_with_message("/products", validation="Product code and name are required.")
    try:
        crud.update_product(
            db,
            product,
            schemas.ProductCreate(
                product_code=product_code.strip(),
                product_name=product_name.strip(),
                dosage_form=dosage_form,
                strength=strength,
                route=route,
                mah_partner_id=mah_partner_id,
                authorization_number=authorization_number,
                authorization_country_code=authorization_country_code,
                authorization_status=authorization_status,
                is_company_product=is_company_product,
            ),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/products", error="Product could not be saved.")
    return redirect_with_message("/products", message="Product saved.")


@router.post(
    "/products/{product_id}/delete",
    dependencies=[Depends(require_any_permission("manage_reference_data", "soft_delete"))],
)
def delete_product_form(
    product_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    db: Session = Depends(get_db),
):
    product = crud.get_product(db, product_id)
    if not product or product.is_deleted:
        return redirect_with_message("/products", error="Product not found.")
    current_user = getattr(request.state, "current_user", None)
    crud.delete_product(
        db,
        product,
        deleted_by_user_id=current_user.id if current_user else None,
        delete_reason=delete_reason,
    )
    return redirect_with_message("/products", message="Product deleted.")


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
