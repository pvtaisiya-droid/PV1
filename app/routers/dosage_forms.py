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


def optional_int(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def dosage_form_payload(
    *,
    name_ru: str,
    name_en: str | None,
    group_ru: str | None,
    group_en: str | None,
    is_active: bool,
    sort_order: str | None,
) -> schemas.DosageFormCreate:
    return schemas.DosageFormCreate(
        name_ru=name_ru.strip(),
        name_en=name_en,
        group_ru=group_ru,
        group_en=group_en,
        is_active=is_active,
        sort_order=optional_int(sort_order),
    )


@router.get("/dosage-forms", response_class=HTMLResponse)
def dosage_forms_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "dosage_forms.html",
        {
            "request": request,
            "dosage_forms": crud.list_dosage_forms(db),
            "active_page": "dosage_forms",
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/dosage-forms",
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def create_dosage_form(
    name_ru: str = Form(...),
    name_en: str | None = Form(None),
    group_ru: str | None = Form(None),
    group_en: str | None = Form(None),
    sort_order: str | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        crud.create_dosage_form(
            db,
            dosage_form_payload(
                name_ru=name_ru,
                name_en=name_en,
                group_ru=group_ru,
                group_en=group_en,
                is_active=True,
                sort_order=sort_order,
            ),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/dosage-forms", validation=str(exc))
    except IntegrityError:
        db.rollback()
        return redirect_with_message(
            "/dosage-forms",
            error="Dosage form could not be saved.",
        )
    return redirect_with_message("/dosage-forms", message="Dosage form saved.")


@router.post(
    "/dosage-forms/{dosage_form_id}/edit",
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def edit_dosage_form(
    dosage_form_id: int,
    name_ru: str = Form(...),
    name_en: str | None = Form(None),
    group_ru: str | None = Form(None),
    group_en: str | None = Form(None),
    is_active: bool = Form(False),
    sort_order: str | None = Form(None),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    dosage_form = crud.get_dosage_form(db, dosage_form_id)
    if not dosage_form:
        return redirect_with_message("/dosage-forms", error="Dosage form not found.")
    require_version(dosage_form, expected_version)
    try:
        crud.update_dosage_form(
            db,
            dosage_form,
            dosage_form_payload(
                name_ru=name_ru,
                name_en=name_en,
                group_ru=group_ru,
                group_en=group_en,
                is_active=is_active,
                sort_order=sort_order,
            ),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/dosage-forms", validation=str(exc))
    except IntegrityError:
        db.rollback()
        return redirect_with_message(
            "/dosage-forms",
            error="Dosage form could not be saved.",
        )
    return redirect_with_message("/dosage-forms", message="Dosage form saved.")


@router.post(
    "/dosage-forms/{dosage_form_id}/deactivate",
    dependencies=[Depends(require_any_permission("manage_reference_data", "soft_delete"))],
)
def deactivate_dosage_form(dosage_form_id: int, expected_version: int = Form(..., ge=1), db: Session = Depends(get_db)):
    dosage_form = crud.get_dosage_form(db, dosage_form_id)
    if not dosage_form:
        return redirect_with_message("/dosage-forms", error="Dosage form not found.")
    require_version(dosage_form, expected_version)
    crud.deactivate_dosage_form(db, dosage_form)
    return redirect_with_message("/dosage-forms", message="Dosage form deactivated.")


@router.get("/api/dosage-forms", response_model=list[schemas.DosageFormRead])
def api_list_dosage_forms(
    include_inactive: bool = True,
    db: Session = Depends(get_db),
):
    return crud.list_dosage_forms(db, include_inactive=include_inactive)


@router.post(
    "/api/dosage-forms",
    response_model=schemas.DosageFormRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def api_create_dosage_form(
    payload: schemas.DosageFormCreate,
    db: Session = Depends(get_db),
):
    try:
        return crud.create_dosage_form(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    "/api/dosage-forms/{dosage_form_id}",
    response_model=schemas.DosageFormRead,
    dependencies=[Depends(require_any_permission("manage_reference_data", "edit"))],
)
def api_update_dosage_form(
    dosage_form_id: int,
    payload: schemas.DosageFormCreate,
    expected_version: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    dosage_form = crud.get_dosage_form(db, dosage_form_id)
    if not dosage_form:
        raise HTTPException(status_code=404, detail="Dosage form not found")
    require_version(dosage_form, expected_version)
    try:
        return crud.update_dosage_form(db, dosage_form, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete(
    "/api/dosage-forms/{dosage_form_id}",
    response_model=schemas.DosageFormRead,
    dependencies=[Depends(require_any_permission("manage_reference_data", "soft_delete"))],
)
def api_deactivate_dosage_form(dosage_form_id: int, expected_version: int = Query(..., ge=1), db: Session = Depends(get_db)):
    dosage_form = crud.get_dosage_form(db, dosage_form_id)
    if not dosage_form:
        raise HTTPException(status_code=404, detail="Dosage form not found")
    require_version(dosage_form, expected_version)
    return crud.deactivate_dosage_form(db, dosage_form)
