from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_permission
from app.database import get_db
from app.statuses import status_codes
from app.templating import templates


router = APIRouter()
SUBMISSION_STATUS_OPTIONS = status_codes("submission")


@router.get("/submissions", response_class=HTMLResponse)
def submissions_page(request: Request, db: Session = Depends(get_db)):
    psur_plans = crud.list_psur_plans(db)
    return templates.TemplateResponse(
        request,
        "submissions.html",
        {
            "request": request,
            "submissions": crud.list_submissions(db),
            "cases": crud.list_cases(db),
            "partners": crud.list_partners(db),
            "psur_plans": psur_plans,
            "psur_by_id": {plan.id: plan for plan in psur_plans},
            "status_options": SUBMISSION_STATUS_OPTIONS,
            "active_page": "submissions",
        },
    )


@router.post("/submissions", dependencies=[Depends(require_permission("create"))])
def create_submission_form(
    expected_object_version: int = Form(..., gt=0),
    submission_object_type: str = Form("case"),
    case_id: str | None = Form(None),
    pbrer_id: str | None = Form(None),
    submission_number: str | None = Form(None),
    recipient_partner_id: str | None = Form(None),
    recipient_type: str = Form("partner"),
    submission_type: str | None = Form("icsr"),
    submission_format: str | None = Form("email"),
    submission_status: str = Form("planned"),
    due_date: str | None = Form(None),
    submitted_at: str | None = Form(None),
    acknowledged_at: str | None = Form(None),
    acknowledgement_code: str | None = Form(None),
    db: Session = Depends(get_db),
):
    selected_case_id = (case_id or "").strip() or None
    selected_pbrer_id = (pbrer_id or "").strip() or None
    if submission_object_type == "pbrer":
        selected_case_id = None
    else:
        submission_object_type = "case"
        selected_pbrer_id = None
    try:
        crud.create_submission(
            db,
            schemas.SubmissionCreateRequest(
                expected_object_version=expected_object_version,
                submission_number=(submission_number or "").strip() or None,
                submission_object_type=submission_object_type,
                case_id=selected_case_id,
                pbrer_id=selected_pbrer_id,
                recipient_partner_id=recipient_partner_id,
                recipient_type=recipient_type,
                submission_type=submission_type,
                submission_format=submission_format,
                submission_status=submission_status,
                due_date=due_date or None,
                submitted_at=submitted_at or None,
                acknowledged_at=acknowledged_at or None,
                acknowledgement_code=(acknowledgement_code or "").strip() or None,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/submissions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/submissions/{submission_id}/status", dependencies=[Depends(require_permission("edit"))])
def update_submission_status_form(
    submission_id: str,
    expected_version: int = Form(..., gt=0),
    submission_status: str = Form(...),
    submission_number: str | None = Form(None),
    submitted_at: str | None = Form(None),
    acknowledged_at: str | None = Form(None),
    acknowledgement_code: str | None = Form(None),
    error_message: str | None = Form(None),
    db: Session = Depends(get_db),
):
    submission = crud.get_submission(db, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        crud.update_submission_status(
            db,
            submission,
            schemas.SubmissionStatusUpdate(
                expected_version=expected_version,
                submission_status=submission_status,
                submission_number=(submission_number or "").strip() or None,
                submitted_at=submitted_at or None,
                acknowledged_at=acknowledged_at or None,
                acknowledgement_code=(acknowledgement_code or "").strip() or None,
                error_message=error_message,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/submissions", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/api/submissions", response_model=list[schemas.SubmissionRead])
def api_list_submissions(db: Session = Depends(get_db)):
    return crud.list_submissions(db)


@router.post(
    "/api/submissions",
    response_model=schemas.SubmissionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("create"))],
)
def api_create_submission(payload: schemas.SubmissionCreateRequest, db: Session = Depends(get_db)):
    try:
        return crud.create_submission(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch(
    "/api/submissions/{submission_id}/status",
    response_model=schemas.SubmissionRead,
    dependencies=[Depends(require_permission("edit"))],
)
def api_update_submission_status(
    submission_id: str,
    payload: schemas.SubmissionStatusUpdate,
    db: Session = Depends(get_db),
):
    submission = crud.get_submission(db, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        return crud.update_submission_status(db, submission, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
