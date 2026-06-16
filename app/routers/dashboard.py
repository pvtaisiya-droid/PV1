from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.templating import templates


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "stats": crud.dashboard_stats(db),
            "upcoming_deadlines": crud.dashboard_upcoming_deadlines(db),
            "overdue_tasks": crud.dashboard_overdue_tasks(db),
            "recent_changes": crud.dashboard_recent_changes(db),
            "active_page": "dashboard",
        },
    )
