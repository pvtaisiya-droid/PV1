from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_permission
from app.database import get_db
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, in_date_range, redirect_with_message


router = APIRouter()
CASE_STATUS_OPTIONS = [
    "new",
    "triage",
    "data_entry",
    "medical_review",
    "qc",
    "ready_for_submission",
    "submitted",
    "closed",
    "reopened",
]


@router.get("/cases", response_class=HTMLResponse)
def cases_page(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    partner_id: str | None = None,
    product_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    all_cases = crud.list_cases(db)
    cases = [
        case
        for case in all_cases
        if contains_search(
            search,
            case.case_number,
            case.worldwide_case_id,
            case.narrative,
            case.partner.partner_name if case.partner else "",
            ", ".join(
                row.reported_product_name or (row.product.product_name if row.product else "")
                for row in case.case_products
            ),
            ", ".join(reaction.reported_term for reaction in case.reactions),
        )
        and (not status_filter or case.workflow_status == status_filter)
        and (not partner_id or case.partner_id == partner_id)
        and (
            not product_id
            or any(row.product_id == product_id for row in case.case_products)
        )
        and in_date_range(case.initial_received_date, date_from, date_to)
    ]
    filters = {
        "search": search or "",
        "status_filter": status_filter or "",
        "partner_id": partner_id or "",
        "product_id": product_id or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "active": active_filters(
            search=search,
            status_filter=status_filter,
            partner_id=partner_id,
            product_id=product_id,
            date_from=date_from,
            date_to=date_to,
        ),
    }
    return templates.TemplateResponse(
        request,
        "cases.html",
        {
            "request": request,
            "cases": cases,
            "partners": crud.list_partners(db),
            "products": crud.list_products(db),
            "status_options": CASE_STATUS_OPTIONS,
            "filters": filters,
            "total_count": len(all_cases),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "active_page": "cases",
        },
    )


@router.get(
    "/cases/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("create"))],
)
def new_case_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "case_new.html",
        {
            "request": request,
            "partners": crud.list_partners(db),
            "reports": crud.list_safety_reports(db),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "active_page": "cases",
        },
    )


@router.post("/cases", dependencies=[Depends(require_permission("create"))])
def create_case_form(
    case_number: str | None = Form(None),
    worldwide_case_id: str | None = Form(None),
    safety_report_id: str | None = Form(None),
    partner_id: str | None = Form(None),
    case_type: str | None = Form("spontaneous"),
    report_type: str | None = Form(None),
    initial_received_date: str | None = Form(None),
    latest_received_date: str | None = Form(None),
    country_of_occurrence: str | None = Form(None),
    seriousness: str | None = Form("non-serious"),
    narrative: str | None = Form(None),
    workflow_status: str = Form("new"),
    due_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        case = crud.create_case(
            db,
            schemas.CaseCreate(
                case_number=case_number,
                worldwide_case_id=worldwide_case_id,
                safety_report_id=safety_report_id,
                partner_id=partner_id,
                case_type=case_type,
                report_type=report_type,
                initial_received_date=initial_received_date or None,
                latest_received_date=latest_received_date or None,
                country_of_occurrence=country_of_occurrence,
                seriousness=seriousness,
                narrative=narrative,
                workflow_status=workflow_status,
                due_date=due_date or None,
            ),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/cases/new", error="Case could not be saved.")
    return redirect_with_message(f"/cases/{case.id}", message="Case saved.")


@router.get("/cases/{case_id}", response_class=HTMLResponse)
def case_detail(case_id: str, request: Request, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return templates.TemplateResponse(
        request,
        "case_detail.html",
        {
            "request": request,
            "case": case,
            "products": crud.list_products(db),
            "partners": crud.list_partners(db),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "active_page": "cases",
        },
    )


@router.post("/cases/{case_id}/status", dependencies=[Depends(require_permission("edit"))])
def change_case_status_form(
    case_id: str,
    workflow_status: str = Form(...),
    change_reason: str | None = Form(None),
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.update_case_status(
            db,
            case,
            schemas.CaseStatusUpdate(workflow_status=workflow_status, change_reason=change_reason),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}", message="Case saved.")


@router.post("/cases/{case_id}/delete", dependencies=[Depends(require_permission("soft_delete"))])
def delete_case_form(
    case_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        return redirect_with_message("/cases", error="Case not found.")
    current_user = getattr(request.state, "current_user", None)
    try:
        crud.delete_case(
            db,
            case,
            deleted_by_user_id=current_user.id if current_user else None,
            delete_reason=delete_reason,
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}", validation=str(exc))
    return redirect_with_message("/cases", message="Case deleted.")


@router.post("/cases/{case_id}/patients", dependencies=[Depends(require_permission("create"))])
def add_patient_form(
    case_id: str,
    patient_initials: str | None = Form(None),
    patient_identifier: str | None = Form(None),
    sex: str | None = Form(None),
    age_value: float | None = Form(None),
    age_unit: str | None = Form("years"),
    weight_kg: float | None = Form(None),
    height_cm: float | None = Form(None),
    pregnancy_status: str | None = Form(None),
    medical_history_text: str | None = Form(None),
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.add_patient(
            db,
            case,
            schemas.PatientCreate(
                patient_initials=patient_initials,
                patient_identifier=patient_identifier,
                sex=sex,
                age_value=age_value,
                age_unit=age_unit,
                weight_kg=weight_kg,
                height_cm=height_cm,
                pregnancy_status=pregnancy_status,
                medical_history_text=medical_history_text,
            ),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}", message="Patient saved.")


@router.post("/cases/{case_id}/products", dependencies=[Depends(require_permission("create"))])
def add_case_product_form(
    case_id: str,
    product_id: str | None = Form(None),
    reported_product_name: str | None = Form(None),
    active_substance_text: str | None = Form(None),
    drug_role: str = Form("suspect"),
    dose_value: str | None = Form(None),
    dose_unit: str | None = Form(None),
    route: str | None = Form(None),
    frequency: str | None = Form(None),
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.add_case_product(
            db,
            case,
            schemas.CaseProductCreate(
                product_id=product_id,
                reported_product_name=reported_product_name,
                active_substance_text=active_substance_text,
                drug_role=drug_role,
                dose_value=dose_value,
                dose_unit=dose_unit,
                route=route,
                frequency=frequency,
            ),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}", message="Product saved.")


@router.post("/cases/{case_id}/reactions", dependencies=[Depends(require_permission("create"))])
def add_reaction_form(
    case_id: str,
    reported_term: str = Form(...),
    verbatim_term: str | None = Form(None),
    meddra_pt_code: str | None = Form(None),
    meddra_pt_name: str | None = Form(None),
    outcome: str | None = Form(None),
    is_serious: bool = Form(False),
    seriousness_death: bool = Form(False),
    seriousness_life_threatening: bool = Form(False),
    seriousness_hospitalization: bool = Form(False),
    seriousness_disability: bool = Form(False),
    seriousness_congenital_anomaly: bool = Form(False),
    seriousness_other_medically_important: bool = Form(False),
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.add_reaction(
            db,
            case,
            schemas.ReactionCreate(
                reported_term=reported_term,
                verbatim_term=verbatim_term,
                meddra_pt_code=meddra_pt_code,
                meddra_pt_name=meddra_pt_name,
                outcome=outcome,
                is_serious=is_serious,
                seriousness_death=seriousness_death,
                seriousness_life_threatening=seriousness_life_threatening,
                seriousness_hospitalization=seriousness_hospitalization,
                seriousness_disability=seriousness_disability,
                seriousness_congenital_anomaly=seriousness_congenital_anomaly,
                seriousness_other_medically_important=seriousness_other_medically_important,
            ),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}", message="Reaction saved.")


@router.post("/cases/{case_id}/followups", dependencies=[Depends(require_permission("create"))])
def add_followup_form(
    case_id: str,
    received_date: str | None = Form(None),
    source_type: str | None = Form(None),
    description: str | None = Form(None),
    significant_new_information: bool = Form(False),
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.add_followup(
            db,
            case,
            schemas.FollowUpCreate(
                received_date=received_date or None,
                source_type=source_type,
                description=description,
                significant_new_information=significant_new_information,
            ),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}", message="Follow-up saved.")


@router.post("/cases/{case_id}/submissions", dependencies=[Depends(require_permission("create"))])
def add_submission_form(
    case_id: str,
    recipient_partner_id: str | None = Form(None),
    recipient_type: str = Form("partner"),
    submission_type: str | None = Form("icsr"),
    submission_format: str | None = Form("email"),
    submission_status: str = Form("planned"),
    due_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.create_submission_for_case(
            db,
            case,
            schemas.SubmissionCreate(
                recipient_partner_id=recipient_partner_id,
                recipient_type=recipient_type,
                submission_type=submission_type,
                submission_format=submission_format,
                submission_status=submission_status,
                due_date=due_date or None,
            ),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}", message="Submission saved.")


@router.get("/api/cases/export.csv", dependencies=[Depends(require_permission("export"))])
def api_export_cases_csv(db: Session = Depends(get_db)):
    return Response(
        crud.export_cases_csv(db),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=case_line_listing.csv"},
    )


@router.get("/api/cases", response_model=list[schemas.CaseRead])
def api_list_cases(db: Session = Depends(get_db)):
    return crud.list_cases(db)


@router.post(
    "/api/cases",
    response_model=schemas.CaseRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("create"))],
)
def api_create_case(payload: schemas.CaseCreate, db: Session = Depends(get_db)):
    return crud.create_case(db, payload)


@router.get("/api/cases/{case_id}", response_model=schemas.CaseRead)
def api_get_case(case_id: str, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/api/cases/{case_id}/overview", response_model=schemas.CaseOverview)
def api_get_case_overview(case_id: str, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return crud.case_overview(case)


@router.patch(
    "/api/cases/{case_id}/status",
    response_model=schemas.CaseRead,
    dependencies=[Depends(require_permission("edit"))],
)
def api_update_case_status(
    case_id: str,
    payload: schemas.CaseStatusUpdate,
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.update_case_status(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/patients",
    response_model=schemas.PatientRead,
    dependencies=[Depends(require_permission("create"))],
)
def api_add_patient(
    case_id: str,
    payload: schemas.PatientCreate,
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.add_patient(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/products",
    response_model=schemas.CaseProductRead,
    dependencies=[Depends(require_permission("create"))],
)
def api_add_case_product(
    case_id: str,
    payload: schemas.CaseProductCreate,
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.add_case_product(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/reactions",
    response_model=schemas.ReactionRead,
    dependencies=[Depends(require_permission("create"))],
)
def api_add_reaction(
    case_id: str,
    payload: schemas.ReactionCreate,
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.add_reaction(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/followups",
    response_model=schemas.FollowUpRead,
    dependencies=[Depends(require_permission("create"))],
)
def api_add_followup(
    case_id: str,
    payload: schemas.FollowUpCreate,
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.add_followup(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/submissions",
    response_model=schemas.SubmissionRead,
    dependencies=[Depends(require_permission("create"))],
)
def api_add_submission(
    case_id: str,
    payload: schemas.SubmissionCreate,
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.create_submission_for_case(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
