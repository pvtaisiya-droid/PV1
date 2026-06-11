from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db
from app.templating import templates


router = APIRouter()


@router.get("/submissions", response_class=HTMLResponse)
def submissions_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "submissions.html",
        {
            "request": request,
            "submissions": crud.list_submissions(db),
            "cases": crud.list_cases(db),
            "partners": crud.list_partners(db),
            "active_page": "submissions",
        },
    )


@router.post("/submissions")
def create_submission_form(
    case_id: str = Form(...),
    recipient_partner_id: str | None = Form(None),
    recipient_type: str = Form("partner"),
    submission_type: str | None = Form("icsr"),
    submission_format: str | None = Form("email"),
    submission_status: str = Form("planned"),
    due_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        crud.create_submission(
            db,
            schemas.SubmissionCreate(
                case_id=case_id,
                recipient_partner_id=recipient_partner_id,
                recipient_type=recipient_type,
                submission_type=submission_type,
                submission_format=submission_format,
                submission_status=submission_status,
                due_date=due_date or None,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/submissions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/submissions/{submission_id}/status")
def update_submission_status_form(
    submission_id: str,
    submission_status: str = Form(...),
    error_message: str | None = Form(None),
    db: Session = Depends(get_db),
):
    submission = crud.get_submission(db, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    crud.update_submission_status(
        db,
        submission,
        schemas.SubmissionStatusUpdate(
            submission_status=submission_status,
            error_message=error_message,
        ),
    )
    return RedirectResponse("/submissions", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/api/submissions", response_model=list[schemas.SubmissionRead])
def api_list_submissions(db: Session = Depends(get_db)):
    return crud.list_submissions(db)


@router.post(
    "/api/submissions",
    response_model=schemas.SubmissionRead,
    status_code=status.HTTP_201_CREATED,
)
def api_create_submission(payload: schemas.SubmissionCreate, db: Session = Depends(get_db)):
    try:
        return crud.create_submission(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/submissions/{submission_id}/status", response_model=schemas.SubmissionRead)
def api_update_submission_status(
    submission_id: str,
    payload: schemas.SubmissionStatusUpdate,
    db: Session = Depends(get_db),
):
    submission = crud.get_submission(db, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    return crud.update_submission_status(db, submission, payload)
