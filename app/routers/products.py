from app import models
from app.concurrency import protect_record
from app.unit_of_work import atomic_write
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission, require_permission
from app.concurrency import require_version
from app.database import get_db
from app.pagination import paginate_items
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, redirect_with_message


router = APIRouter()

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

PRODUCT_STATUS_OPTIONS = ["registration_active", "registration_in_progress", "ma_change"]
PRODUCT_STATUS_ALIASES = {"authorized": "registration_active"}
REGISTRATION_STATUS_OPTIONS = [
    "active",
    "registration_active",
    "registration_in_progress",
    "suspended",
    "cancelled",
    "replaced",
    "archived",
]


def normalize_product_status(value: str | None) -> str | None:
    status = (value or "").strip()
    return PRODUCT_STATUS_ALIASES.get(status, status) or None


def optional_int(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def optional_date(value: str | None) -> date | None:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


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
    all_documents = crud.list_attachments(db)
    for product in all_products:
        product.partner_link_ids = crud.product_partner_ids(product)
        product.has_partner_links = bool(product.partner_link_ids)
        product.registration_certificate_rows = crud.product_registration_certificates(product)
        product.current_registration = crud.current_product_registration(product)

    def matches_text_and_status(product) -> bool:
        registration_numbers = ", ".join(
            certificate.registration_number
            for certificate in product.registration_certificate_rows
        )
        return contains_search(
            search,
            product.product_code,
            product.product_name,
            crud.product_dosage_form_name(product),
            product.authorization_number,
            registration_numbers,
            product.comment,
            ", ".join(
                link.substance.substance_name
                for link in product.substance_links
                if link.substance
            ),
        ) and (
            not status_filter
            or normalize_product_status(product.authorization_status) == status_filter
        )

    products = [
        product
        for product in all_products
        if matches_text_and_status(product)
        and (not partner_id or partner_id in product.partner_link_ids)
    ]
    unlinked_products = [
        product
        for product in all_products
        if matches_text_and_status(product) and not product.has_partner_links
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
            "contracts": crud.list_contracts(db),
            "substances": crud.list_substances(db),
            "dosage_forms": crud.list_dosage_forms(db),
            "active_dosage_forms": crud.list_active_dosage_forms(db),
            "unlinked_products": unlinked_products,
            "status_options": PRODUCT_STATUS_OPTIONS,
            "registration_status_options": REGISTRATION_STATUS_OPTIONS,
            "documents_by_product": {
                product.id: [
                    document
                    for document in all_documents
                    if document.product_id == product.id
                    or (
                        document.related_object_type == "Product"
                        and document.related_object_id == product.id
                    )
                ]
                for product in all_products
            },
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
@atomic_write
def create_product_form(
    product_name: str = Form(...),
    dosage_form_id: str | None = Form(None),
    strength: str | None = Form(None),
    active_substance_id: str | None = Form(None),
    mah_partner_id: str | None = Form(None),
    authorization_number: str | None = Form(None),
    authorization_status: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not product_name.strip():
        return redirect_with_message("/products", validation="Product name is required.")
    try:
        product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_name=product_name.strip(),
                dosage_form_id=optional_int(dosage_form_id),
                strength=strength,
                active_substance_id=active_substance_id,
                mah_partner_id=mah_partner_id,
                authorization_number=authorization_number,
                authorization_status=normalize_product_status(authorization_status),
                comment=comment,
            ),
        )
        if authorization_number and authorization_number.strip():
            crud.create_product_registration_certificate(
                db,
                schemas.ProductRegistrationCertificateCreate(
                    product_id=product.id,
                    registration_number=authorization_number.strip(),
                    status=normalize_product_status(authorization_status) or "active",
                    dosage_form=crud.product_dosage_form_name(product) or product.dosage_form,
                    strength=strength,
                    comment=comment,
                ),
            )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/products", validation=str(exc))
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/products", error="Product could not be saved.")
    return redirect_with_message("/products", message="Product saved.")


@router.post(
    "/products/{product_id}/registrations",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
@atomic_write
def create_product_registration_form(
    product_id: str,
    registration_number: str = Form(...),
    registration_date: str | None = Form(None),
    valid_until: str | None = Form(None),
    status: str | None = Form("active"),
    holder: str | None = Form(None),
    country_code: str | None = Form(None),
    dosage_form: str | None = Form(None),
    strength: str | None = Form(None),
    comment: str | None = Form(None),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Product, product_id, expected_version)
    try:
        crud.create_product_registration_certificate(
            db,
            schemas.ProductRegistrationCertificateCreate(
                product_id=product_id,
                registration_number=registration_number,
                registration_date=optional_date(registration_date),
                valid_until=optional_date(valid_until),
                status=status or "active",
                holder=holder,
                country_code=country_code,
                dosage_form=dosage_form,
                strength=strength,
                comment=comment,
            ),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/products", validation=str(exc))
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/products", error="Registration certificate could not be saved.")
    return redirect_with_message("/products", message="Registration certificate saved.")


@router.post(
    "/products/registrations/{certificate_id}/edit",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def edit_product_registration_form(
    certificate_id: str,
    registration_number: str = Form(...),
    registration_date: str | None = Form(None),
    valid_until: str | None = Form(None),
    status: str | None = Form("active"),
    holder: str | None = Form(None),
    country_code: str | None = Form(None),
    dosage_form: str | None = Form(None),
    strength: str | None = Form(None),
    comment: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    certificate = crud.get_product_registration_certificate(db, certificate_id)
    if not certificate:
        return redirect_with_message("/products", error="Registration certificate not found.")
    require_version(certificate, expected_version)
    try:
        crud.update_product_registration_certificate(
            db,
            certificate,
            schemas.ProductRegistrationCertificateCreate(
                product_id=certificate.product_id,
                registration_number=registration_number,
                registration_date=optional_date(registration_date),
                valid_until=optional_date(valid_until),
                status=status or "active",
                holder=holder,
                country_code=country_code,
                dosage_form=dosage_form,
                strength=strength,
                comment=comment,
            ),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/products", validation=str(exc))
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/products", error="Registration certificate could not be saved.")
    return redirect_with_message("/products", message="Registration certificate saved.")


@router.post(
    "/products/registrations/{certificate_id}/delete",
    dependencies=[Depends(require_any_permission("manage_reference_data", "soft_delete"))],
)
def delete_product_registration_form(
    certificate_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    certificate = crud.get_product_registration_certificate(db, certificate_id)
    if not certificate:
        return redirect_with_message("/products", error="Registration certificate not found.")
    require_version(certificate, expected_version)
    current_user = getattr(request.state, "current_user", None)
    crud.delete_product_registration_certificate(
        db,
        certificate,
        deleted_by_user_id=current_user.id if current_user else None,
        delete_reason=delete_reason,
    )
    return redirect_with_message("/products", message="Registration certificate deleted.")


@router.post(
    "/products/{product_id}/edit",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def edit_product_form(
    product_id: str,
    product_name: str = Form(...),
    dosage_form_id: str | None = Form(None),
    strength: str | None = Form(None),
    active_substance_id: str | None = Form(None),
    mah_partner_id: str | None = Form(None),
    authorization_number: str | None = Form(None),
    authorization_status: str | None = Form(None),
    comment: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    product = crud.get_product(db, product_id)
    if not product:
        return redirect_with_message("/products", error="Product not found.")
    require_version(product, expected_version)
    if not product_name.strip():
        return redirect_with_message("/products", validation="Product name is required.")
    try:
        product_payload = {
            "product_name": product_name.strip(),
            "strength": strength,
            "mah_partner_id": mah_partner_id,
            "authorization_number": product.authorization_number,
            "authorization_status": product.authorization_status,
            "comment": comment,
        }
        if dosage_form_id or product.dosage_form_id:
            product_payload["dosage_form_id"] = optional_int(dosage_form_id)
        else:
            product_payload["dosage_form"] = product.dosage_form
        product = crud.update_product(
            db,
            product,
            schemas.ProductCreate(**product_payload),
            commit=False,
        )
        crud.set_product_active_substance(db, product, active_substance_id, commit=False)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/products", validation=str(exc))
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/products", error="Product could not be saved.")
    return redirect_with_message("/products", message="Product saved.")


@router.post(
    "/products/{product_id}/contract-links",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def link_product_contract_form(
    product_id: str,
    contract_id: str = Form(...),
    expected_version: int = Form(..., ge=1),
    expected_contract_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    product = crud.get_product(db, product_id)
    if not product or product.is_deleted:
        return redirect_with_message("/products", error="Product not found.")
    contract = crud.get_contract(db, contract_id)
    if not contract:
        return redirect_with_message("/products", error="Contract not found.")
    require_version(product, expected_version)
    require_version(contract, expected_contract_version)
    try:
        crud.link_product_to_contract(db, product, contract)
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/products", validation=str(exc))
    return redirect_with_message("/products", message="Product saved.")


@router.post(
    "/products/{product_id}/partner-link",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def link_product_partner_form(
    product_id: str,
    mah_partner_id: str = Form(...),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    product = crud.get_product(db, product_id)
    if not product or product.is_deleted:
        return redirect_with_message("/products", error="Product not found.")
    if not crud.get_partner(db, mah_partner_id):
        return redirect_with_message("/products", error="Partner not found.")
    require_version(product, expected_version)
    try:
        crud.update_product(
            db,
            product,
            schemas.ProductCreate(
                product_code=product.product_code,
                product_name=product.product_name,
                product_name_normalized=product.product_name_normalized,
                dosage_form=product.dosage_form,
                strength=product.strength,
                route=product.route,
                mah_partner_id=mah_partner_id,
                authorization_number=product.authorization_number,
                authorization_country_code=product.authorization_country_code,
                authorization_status=product.authorization_status,
                is_company_product=product.is_company_product,
            ),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/products", error="Product could not be saved.")
    return redirect_with_message("/products", message="Product saved.")


@router.post(
    "/products/{product_id}/contract-links/{contract_id}/delete",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def unlink_product_contract_form(
    product_id: str,
    contract_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    expected_contract_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    product = crud.get_product(db, product_id)
    if not product or product.is_deleted:
        return redirect_with_message("/products", error="Product not found.")
    contract = crud.get_contract(db, contract_id)
    if not contract:
        return redirect_with_message("/products", error="Contract not found.")
    require_version(product, expected_version)
    require_version(contract, expected_contract_version)
    current_user = getattr(request.state, "current_user", None)
    try:
        crud.unlink_product_from_contract(
            db,
            product,
            contract,
            deleted_by_user_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/products", validation=str(exc))
    return redirect_with_message("/products", message="Product saved.")


@router.post(
    "/products/{product_id}/delete",
    dependencies=[Depends(require_any_permission("manage_reference_data", "soft_delete"))],
)
def delete_product_form(
    product_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    product = crud.get_product(db, product_id)
    if not product or product.is_deleted:
        return redirect_with_message("/products", error="Product not found.")
    require_version(product, expected_version)
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


@router.get(
    "/api/products/export.xlsx",
    dependencies=[Depends(require_permission("export"))],
)
def api_export_partner_products_xlsx(
    partner_id: str,
    db: Session = Depends(get_db),
):
    partner = crud.get_partner(db, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found.")
    return Response(
        crud.export_partner_products_xlsx(db, partner),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": "attachment; filename=dra_product_list.xlsx"},
    )


@router.post(
    "/api/products",
    response_model=schemas.ProductRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def api_create_product(payload: schemas.ProductCreate, db: Session = Depends(get_db)):
    try:
        return crud.create_product(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
