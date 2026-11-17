from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    SESSION_COOKIE,
    authenticate_user,
    clear_login_failures,
    clear_session_cookie,
    create_auth_session,
    login_is_rate_limited,
    record_login_failure,
    revoke_auth_session,
    set_session_cookie,
)
from app.database import get_db
from app.templating import templates


router = APIRouter()


def safe_next_url(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def login_page_response(
    request: Request,
    *,
    login: str = "",
    next_url: str = "/",
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "login": login,
            "next_url": safe_next_url(next_url),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if getattr(request.state, "current_user", None):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return login_page_response(request, next_url=next)


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("/"),
    db: Session = Depends(get_db),
):
    if login_is_rate_limited(request, login):
        return login_page_response(
            request,
            login=login,
            next_url=next_url,
            error="Слишком много попыток входа. Повторите через 15 минут.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    user = authenticate_user(db, login, password)
    if not user:
        record_login_failure(request, login)
        return login_page_response(
            request,
            login=login,
            next_url=next_url,
            error="Неверный логин или пароль.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    clear_login_failures(request, login)
    raw_token = create_auth_session(db, user)
    response = RedirectResponse(
        safe_next_url(next_url),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_session_cookie(response, raw_token)
    return response


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    revoke_auth_session(db, request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response
