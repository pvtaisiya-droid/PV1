from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.templating import templates


router = APIRouter()


PLACEHOLDER_PAGES = {
    "psur": {
        "title": "PSUR / PBRER",
        "description": "Periodic safety reports planning and tracking module.",
    },
    "rmp": {
        "title": "RMP",
        "description": "Risk management plan module.",
    },
    "psmf": {
        "title": "PSMF",
        "description": "Pharmacovigilance system master file module.",
    },
    "documents": {
        "title": "Documents",
        "description": "Document registry and controlled attachments module.",
    },
    "audit_log": {
        "title": "Audit Log",
        "description": "Central audit trail view.",
    },
    "settings": {
        "title": "Settings",
        "description": "System settings module.",
    },
}


def render_placeholder(request: Request, active_page: str) -> HTMLResponse:
    page = PLACEHOLDER_PAGES[active_page]
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        {
            "request": request,
            "active_page": active_page,
            "page_title": page["title"],
            "page_description": page["description"],
        },
    )


@router.get("/psur", response_class=HTMLResponse)
def psur_page(request: Request):
    return render_placeholder(request, "psur")


@router.get("/rmp", response_class=HTMLResponse)
def rmp_page(request: Request):
    return render_placeholder(request, "rmp")


@router.get("/psmf", response_class=HTMLResponse)
def psmf_page(request: Request):
    return render_placeholder(request, "psmf")


@router.get("/documents", response_class=HTMLResponse)
def documents_page(request: Request):
    return render_placeholder(request, "documents")


@router.get("/audit-log", response_class=HTMLResponse)
def audit_log_page(request: Request):
    return render_placeholder(request, "audit_log")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return render_placeholder(request, "settings")
