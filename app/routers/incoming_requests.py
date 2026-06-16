from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app.ai.incoming_requests import analyze_incoming_request_mock
from app.auth import require_any_permission
from app.database import get_db
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, redirect_with_message


router = APIRouter()

INCOMING_REQUEST_STATUSES = [
    "new",
    "analyzed",
    "needs_review",
    "confirmed",
    "converted_to_icsr",
    "closed",
]


@router.get("/incoming-requests", response_class=HTMLResponse)
def incoming_requests_page(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
):
    return render_page(
        request,
        db,
        search=search,
        status_filter=status_filter,
    )


@router.post("/incoming-requests/analyze", response_class=HTMLResponse)
def analyze_incoming_request_form(
    request: Request,
    source_text: str = Form(...),
    db: Session = Depends(get_db),
):
    if not source_text.strip():
        return redirect_with_message(
            "/incoming-requests",
            validation="Source text is required.",
        )
    draft = analyze_incoming_request_mock(
        source_text,
        partners=crud.list_partners(db),
        products=crud.list_products(db),
    )
    return render_page(
        request,
        db,
        source_text=source_text,
        draft=draft,
        message="Mock GPT analysis prepared. Review and confirm before saving.",
    )


@router.post(
    "/incoming-requests/confirm",
    dependencies=[Depends(require_any_permission("create", "edit"))],
)
def confirm_incoming_request_form(
    request: Request,
    source_text: str = Form(...),
    request_type: str | None = Form(None),
    partner_id: str | None = Form(None),
    product_id: str | None = Form(None),
    active_substance: str | None = Form(None),
    possible_icsr: str = Form("no"),
    patient_information: str | None = Form(None),
    adverse_event: str | None = Form(None),
    seriousness: str | None = Form(None),
    seriousness_criteria: str | None = Form(None),
    missing_information: str | None = Form(None),
    recommended_next_action: str | None = Form(None),
    validity_assessment: str | None = Form(None),
    gpt_json_output: str | None = Form(None),
    status_value: str = Form("confirmed"),
    db: Session = Depends(get_db),
):
    if not source_text.strip():
        return redirect_with_message(
            "/incoming-requests",
            validation="Source text is required.",
        )
    if status_value not in INCOMING_REQUEST_STATUSES:
        return redirect_with_message(
            "/incoming-requests",
            validation="Invalid incoming request status.",
        )
    current_user = getattr(request.state, "current_user", None)
    crud.create_incoming_request(
        db,
        schemas.IncomingRequestCreate(
            source_text=source_text,
            request_type=request_type,
            partner_id=partner_id or None,
            product_id=product_id or None,
            active_substance=active_substance,
            possible_icsr=possible_icsr,
            patient_information=patient_information,
            adverse_event=adverse_event,
            seriousness=seriousness,
            seriousness_criteria=seriousness_criteria,
            missing_information=missing_information,
            recommended_next_action=recommended_next_action,
            validity_assessment=validity_assessment,
            gpt_json_output=gpt_json_output,
            status=status_value,
        ),
        created_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message("/incoming-requests", message="Incoming request saved.")


@router.post(
    "/incoming-requests/{request_id}/status",
    dependencies=[Depends(require_any_permission("edit", "comment"))],
)
def update_incoming_request_status_form(
    request_id: str,
    request: Request,
    status_value: str = Form(...),
    db: Session = Depends(get_db),
):
    row = crud.get_incoming_request(db, request_id)
    if not row:
        return redirect_with_message("/incoming-requests", error="Incoming request not found.")
    if status_value not in INCOMING_REQUEST_STATUSES:
        return redirect_with_message(
            "/incoming-requests",
            validation="Invalid incoming request status.",
        )
    current_user = getattr(request.state, "current_user", None)
    crud.update_incoming_request_status(
        db,
        row,
        status=status_value,
        changed_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message("/incoming-requests", message="Incoming request status saved.")


def render_page(
    request: Request,
    db: Session,
    *,
    search: str | None = None,
    status_filter: str | None = None,
    source_text: str = "",
    draft: dict | None = None,
    message: str | None = None,
):
    all_requests = crud.list_incoming_requests(db)
    incoming_requests = [
        row
        for row in all_requests
        if contains_search(
            search,
            row.source_text,
            row.request_type,
            row.active_substance,
            row.patient_information,
            row.adverse_event,
            row.missing_information,
            row.recommended_next_action,
            row.partner.partner_name if row.partner else "",
            row.product.product_name if row.product else "",
        )
        and (not status_filter or row.status == status_filter)
    ]
    filters = {
        "search": search or "",
        "status_filter": status_filter or "",
        "active": active_filters(search=search, status_filter=status_filter),
    }
    return templates.TemplateResponse(
        request,
        "incoming_requests.html",
        {
            "request": request,
            "active_page": "incoming_requests",
            "incoming_requests": incoming_requests,
            "partners": crud.list_partners(db),
            "products": crud.list_products(db),
            "status_options": INCOMING_REQUEST_STATUSES,
            "filters": filters,
            "total_count": len(all_requests),
            "source_text": source_text,
            "draft": draft,
            "message": message or request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )
