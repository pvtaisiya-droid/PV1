from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission, require_permission
from app.database import get_db
from app.pagination import paginate_items
from app.statuses import status_codes
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, in_date_range, redirect_with_message


router = APIRouter()
TRIAGE_STATUS_OPTIONS = status_codes("triage")


@router.get("/safety-reports", response_class=HTMLResponse)
def safety_reports_page(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    partner_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    per_page: int | None = None,
    db: Session = Depends(get_db),
):
    all_reports = crud.list_safety_reports(db)
    reports = [
        report
        for report in all_reports
        if contains_search(
            search,
            report.safety_report_number,
            report.raw_subject,
            report.raw_text,
            report.reporter_name,
            report.reporter_email,
            report.partner.partner_name if report.partner else "",
        )
        and (not status_filter or report.triage_status == status_filter)
        and (not partner_id or report.partner_id == partner_id)
        and in_date_range(report.received_date, date_from, date_to)
    ]
    filters = {
        "search": search or "",
        "status_filter": status_filter or "",
        "partner_id": partner_id or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "active": active_filters(
            search=search,
            status_filter=status_filter,
            partner_id=partner_id,
            date_from=date_from,
            date_to=date_to,
        ),
    }
    page_reports, pagination = paginate_items(reports, page, per_page)
    return templates.TemplateResponse(
        request,
        "safety_reports.html",
        {
            "request": request,
            "reports": page_reports,
            "partners": crud.list_partners(db),
            "status_options": TRIAGE_STATUS_OPTIONS,
            "filters": filters,
            "total_count": len(all_reports),
            "filtered_count": len(reports),
            "pagination": pagination,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "active_page": "safety_reports",
        },
    )


@router.post("/safety-reports", dependencies=[Depends(require_permission("create"))])
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
    return redirect_with_message("/safety-reports", message="Safety report saved.")


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
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "active_page": "safety_reports",
        },
    )


@router.post(
    "/safety-reports/{report_id}/triage",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
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
    return redirect_with_message(f"/safety-reports/{report_id}", message="Safety report saved.")


@router.post(
    "/safety-reports/{report_id}/create-case",
    dependencies=[Depends(require_any_permission("create", "icsr_workflow"))],
)
def create_case_from_report_form(report_id: str, db: Session = Depends(get_db)):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    case = crud.create_case_from_report(db, report)
    return redirect_with_message(f"/cases/{case.id}", message="Case saved.")


@router.get("/api/safety-reports", response_model=list[schemas.SafetyReportRead])
def api_list_safety_reports(db: Session = Depends(get_db)):
    return crud.list_safety_reports(db)


@router.post(
    "/api/safety-reports",
    response_model=schemas.SafetyReportRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("create"))],
)
def api_create_safety_report(payload: schemas.SafetyReportCreate, db: Session = Depends(get_db)):
    return crud.create_safety_report(db, payload)


@router.get("/api/safety-reports/{report_id}", response_model=schemas.SafetyReportRead)
def api_get_safety_report(report_id: str, db: Session = Depends(get_db)):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    return report


@router.patch(
    "/api/safety-reports/{report_id}/triage",
    response_model=schemas.SafetyReportRead,
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def api_triage_safety_report(
    report_id: str,
    payload: schemas.TriageUpdate,
    db: Session = Depends(get_db),
):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    return crud.triage_safety_report(db, report, payload)


@router.post(
    "/api/safety-reports/{report_id}/create-case",
    response_model=schemas.CaseRead,
    dependencies=[Depends(require_any_permission("create", "icsr_workflow"))],
)
def api_create_case_from_report(report_id: str, db: Session = Depends(get_db)):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    return crud.create_case_from_report(db, report)
