from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db
from app.templating import templates


router = APIRouter()


@router.get("/safety-reports", response_class=HTMLResponse)
def safety_reports_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "safety_reports.html",
        {
            "request": request,
            "reports": crud.list_safety_reports(db),
            "partners": crud.list_partners(db),
            "active_page": "safety_reports",
        },
    )


@router.post("/safety-reports")
def create_safety_report_form(
    source_type: str | None = Form("email"),
    partner_id: str | None = Form(None),
    reporter_name: str | None = Form(None),
    reporter_email: str | None = Form(None),
    reporter_country_code: str | None = Form(None),
    raw_subject: str | None = Form(None),
    raw_text: str | None = Form(None),
    db: Session = Depends(get_db),
):
    crud.create_safety_report(
        db,
        schemas.SafetyReportCreate(
            source_type=source_type,
            partner_id=partner_id,
            reporter_name=reporter_name,
            reporter_email=reporter_email,
            reporter_country_code=reporter_country_code,
            raw_subject=raw_subject,
            raw_text=raw_text,
        ),
    )
    return RedirectResponse("/safety-reports", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/safety-reports/{report_id}", response_class=HTMLResponse)
def safety_report_detail(report_id: str, request: Request, db: Session = Depends(get_db)):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    return templates.TemplateResponse(
        request,
        "safety_report_detail.html",
        {
            "request": request,
            "report": report,
            "active_page": "safety_reports",
        },
    )


@router.post("/safety-reports/{report_id}/triage")
def triage_safety_report_form(
    report_id: str,
    triage_status: str = Form(...),
    triage_comment: str | None = Form(None),
    is_valid_icsr: bool = Form(False),
    minimum_criteria_patient: bool = Form(False),
    minimum_criteria_reporter: bool = Form(False),
    minimum_criteria_product: bool = Form(False),
    minimum_criteria_event: bool = Form(False),
    db: Session = Depends(get_db),
):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    crud.triage_safety_report(
        db,
        report,
        schemas.TriageUpdate(
            triage_status=triage_status,
            triage_comment=triage_comment,
            is_valid_icsr=is_valid_icsr,
            minimum_criteria_patient=minimum_criteria_patient,
            minimum_criteria_reporter=minimum_criteria_reporter,
            minimum_criteria_product=minimum_criteria_product,
            minimum_criteria_event=minimum_criteria_event,
        ),
    )
    return RedirectResponse(f"/safety-reports/{report_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/safety-reports/{report_id}/create-case")
def create_case_from_report_form(report_id: str, db: Session = Depends(get_db)):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    case = crud.create_case_from_report(db, report)
    return RedirectResponse(f"/cases/{case.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/api/safety-reports", response_model=list[schemas.SafetyReportRead])
def api_list_safety_reports(db: Session = Depends(get_db)):
    return crud.list_safety_reports(db)


@router.post(
    "/api/safety-reports",
    response_model=schemas.SafetyReportRead,
    status_code=status.HTTP_201_CREATED,
)
def api_create_safety_report(payload: schemas.SafetyReportCreate, db: Session = Depends(get_db)):
    return crud.create_safety_report(db, payload)


@router.get("/api/safety-reports/{report_id}", response_model=schemas.SafetyReportRead)
def api_get_safety_report(report_id: str, db: Session = Depends(get_db)):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    return report


@router.patch("/api/safety-reports/{report_id}/triage", response_model=schemas.SafetyReportRead)
def api_triage_safety_report(
    report_id: str,
    payload: schemas.TriageUpdate,
    db: Session = Depends(get_db),
):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    return crud.triage_safety_report(db, report, payload)


@router.post("/api/safety-reports/{report_id}/create-case", response_model=schemas.CaseRead)
def api_create_case_from_report(report_id: str, db: Session = Depends(get_db)):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    return crud.create_case_from_report(db, report)
