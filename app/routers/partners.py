from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db
from app.templating import templates


router = APIRouter()


@router.get("/partners", response_class=HTMLResponse)
def partners_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "partners.html",
        {
            "request": request,
            "partners": crud.list_partners(db),
            "active_page": "partners",
        },
    )


@router.post("/partners")
def create_partner_form(
    partner_code: str = Form(...),
    partner_name: str = Form(...),
    partner_type: str = Form("partner"),
    country_code: str | None = Form(None),
    region: str | None = Form(None),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    address: str | None = Form(None),
    pv_responsible_person: str | None = Form(None),
    sdea_required: bool = Form(False),
    db: Session = Depends(get_db),
):
    crud.create_partner(
        db,
        schemas.PartnerCreate(
            partner_code=partner_code,
            partner_name=partner_name,
            partner_type=partner_type,
            country_code=country_code,
            region=region,
            email=email,
            phone=phone,
            address=address,
            pv_responsible_person=pv_responsible_person,
            sdea_required=sdea_required,
        ),
    )
    return RedirectResponse("/partners", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/api/partners", response_model=list[schemas.PartnerRead])
def api_list_partners(db: Session = Depends(get_db)):
    return crud.list_partners(db)


@router.post("/api/partners", response_model=schemas.PartnerRead, status_code=status.HTTP_201_CREATED)
def api_create_partner(payload: schemas.PartnerCreate, db: Session = Depends(get_db)):
    return crud.create_partner(db, payload)


@router.get("/api/partners/{partner_id}", response_model=schemas.PartnerRead)
def api_get_partner(partner_id: str, db: Session = Depends(get_db)):
    partner = crud.get_partner(db, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    return partner
