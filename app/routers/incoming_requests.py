from app.concurrency import snapshot_token, protect_snapshot, ONBOARDING_INPUTS
from datetime import date

from fastapi import Query, APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from pydantic import PositiveInt, TypeAdapter, ValidationError
from app.concurrency import require_version

from app import crud, schemas
from app.ai.incoming_requests import analyze_incoming_request_draft
from app.auth import require_any_permission, require_permission
from app.database import get_db
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, redirect_with_message


router = APIRouter()
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

INCOMING_REQUEST_STATUSES = [
    "new",
    "triage_in_progress",
    "awaiting_information",
    "triage_completed",
    "in_triage",
    "valid_icsr",
    "invalid",
    "duplicate",
    "non_safety",
    "analyzed",
    "needs_review",
    "confirmed",
    "converted_to_case",
    "converted_to_icsr",
    "linked_to_case",
    "closed_no_case",
    "closed",
]

TRIAGE_STATUS_OPTIONS = ["not_started", "in_progress", "completed", "not_required"]
SAFETY_RELEVANCE_OPTIONS = ["safety_related", "not_safety_related", "unclear"]
POTENTIAL_ICSR_OPTIONS = ["yes", "no", "unclear"]
VALIDITY_STATUS_OPTIONS = ["valid_icsr", "invalid_icsr", "pending_information", "not_applicable"]
TRIAGE_RESULT_OPTIONS = [
    "create_case",
    "no_case_required",
    "request_follow_up",
    "medical_information_only",
    "quality_complaint_only",
    "duplicate",
    "other",
]
CASE_LINK_TYPE_OPTIONS = ["manual_link", "follow_up", "duplicate", "related"]
QUICK_FILTER_OPTIONS = [
    "new",
    "awaiting_triage",
    "valid_icsr",
    "awaiting_follow_up",
    "closed_no_case",
    "converted_to_case",
    "linked_to_case",
    "without_case",
]


@router.get("/incoming-requests", response_class=HTMLResponse)
def incoming_requests_page(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    quick_filter: str | None = None,
    db: Session = Depends(get_db),
):
    return render_page(
        request,
        db,
        search=search,
        status_filter=status_filter,
        quick_filter=quick_filter,
    )


@router.get(
    "/incoming-requests/export.csv",
    dependencies=[Depends(require_permission("export"))],
)
def export_incoming_requests_csv(
    dosage_form_language: str = "ru",
    db: Session = Depends(get_db),
):
    return Response(
        crud.export_incoming_requests_csv(
            db,
            dosage_form_language=dosage_form_language,
        ),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=safety_message_journal.csv"},
    )


@router.get(
    "/incoming-requests/export.xlsx",
    dependencies=[Depends(require_permission("export"))],
)
def export_incoming_requests_xlsx(
    dosage_form_language: str = "ru",
    db: Session = Depends(get_db),
):
    return Response(
        crud.export_incoming_requests_xlsx(
            db,
            dosage_form_language=dosage_form_language,
        ),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": "attachment; filename=safety_message_journal.xlsx"},
    )


@router.get("/gpt-analysis", response_class=HTMLResponse)
def gpt_analysis_page(request: Request, db: Session = Depends(get_db)):
    return render_gpt_analysis_page(request, db)


@router.post("/gpt-analysis/analyze", response_class=HTMLResponse,
             dependencies=[Depends(require_permission("view"))])
def analyze_gpt_analysis_form(
    request: Request,
    source_text: str = Form(...),
    db: Session = Depends(get_db),
):
    if not source_text.strip():
        return render_gpt_analysis_page(
            request,
            db,
            validation="Source text is required.",
        )
    draft = analyze_incoming_request_draft(
        source_text,
        partners=crud.list_partners(db),
        products=crud.list_products(db),
    )
    return render_gpt_analysis_page(
        request,
        db,
        source_text=source_text,
        draft=draft,
        message="Analysis draft prepared. Review and confirm before saving.",
    )


@router.post(
    "/gpt-analysis/confirm",
    dependencies=[Depends(require_any_permission("create", "edit"))],
)
def confirm_gpt_analysis_form(
    source_text: str = Form(...),
    request_type: str | None = Form(None),
    partner_id: str | None = Form(None),
    product_id: str | None = Form(None),
    product_ids: list[str] | None = Form(None),
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
    db: Session = Depends(get_db),
):
    if not source_text.strip():
        return redirect_with_message("/gpt-analysis", validation="Source text is required.")
    selected_product_ids = normalized_product_ids(product_id, product_ids)
    report = crud.create_safety_report(
        db,
        schemas.SafetyReportCreate(
            source_type="gpt_analysis",
            message_direction="needs_review",
            partner_id=partner_id or None,
            product_ids=selected_product_ids,
            raw_subject=gpt_analysis_subject(request_type, possible_icsr),
            raw_text=gpt_analysis_raw_text(
                source_text=source_text,
                request_type=request_type,
                product_ids=", ".join(selected_product_ids),
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
            ),
            triage_status="new",
            triage_comment=validity_assessment,
        ),
    )
    return redirect_with_message(f"/safety-reports/{report.id}", message="Safety report saved.")


@router.post("/incoming-requests/analyze", response_class=HTMLResponse,
             dependencies=[Depends(require_permission("view"))])
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
    draft = analyze_incoming_request_draft(
        source_text,
        partners=crud.list_partners(db),
        products=crud.list_products(db),
    )
    return render_page(
        request,
        db,
        source_text=source_text,
        draft=draft,
        message="Analysis draft prepared. Review and confirm before saving.",
    )


@router.post(
    "/incoming-requests/register",
    dependencies=[Depends(require_any_permission("create", "edit"))],
)
def register_incoming_request_form(
    request: Request,
    source_text: str = Form(...),
    received_date: str | None = Form(None),
    source_type: str | None = Form(None),
    source_name: str | None = Form(None),
    channel: str | None = Form(None),
    message_category: str | None = Form(None),
    request_type: str | None = Form("safety_message"),
    partner_id: str | None = Form(None),
    product_id: str | None = Form(None),
    product_ids: list[str] | None = Form(None),
    active_substance: str | None = Form(None),
    reporter_name: str | None = Form(None),
    reporter_contact: str | None = Form(None),
    possible_icsr: str = Form("no"),
    patient_information: str | None = Form(None),
    adverse_event: str | None = Form(None),
    seriousness: str | None = Form(None),
    missing_information: str | None = Form(None),
    status_value: str = Form("new"),
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
    try:
        crud.create_incoming_request(
            db,
            schemas.IncomingRequestCreate(
            source_text=source_text,
            received_date=optional_date(received_date),
            source_type=source_type,
            source_name=source_name,
            channel=channel,
            message_category=message_category,
            request_type=request_type,
            partner_id=partner_id or None,
            product_id=product_id or None,
            product_ids=normalized_product_ids(product_id, product_ids),
            active_substance=active_substance,
            reporter_name=reporter_name,
            reporter_contact=reporter_contact,
            possible_icsr=possible_icsr,
            patient_information=patient_information,
            adverse_event=adverse_event,
            seriousness=seriousness,
            missing_information=missing_information,
            status=status_value,
            ),
            created_by_user_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        return redirect_with_message("/incoming-requests", validation=str(exc))
    return redirect_with_message("/incoming-requests", message="Safety message saved.")


@router.post(
    "/incoming-requests/{request_id}/products",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def update_incoming_request_products_form(
    request_id: str,
    request: Request,
    product_ids: list[str] | None = Form(None),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    row = crud.get_incoming_request(db, request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Incoming request not found")
    require_version(row, expected_version)
    current_user = getattr(request.state, "current_user", None)
    try:
        crud.update_incoming_request_products(
            db,
            row,
            product_ids or [],
            changed_by_user_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        return redirect_with_message(
            "/incoming-requests",
            validation=str(exc),
        )
    return redirect_with_message(
        "/incoming-requests",
        message="Safety message saved.",
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
    product_ids: list[str] | None = Form(None),
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
    try:
        crud.create_incoming_request(
            db,
            schemas.IncomingRequestCreate(
            source_text=source_text,
            request_type=request_type,
            partner_id=partner_id or None,
            product_id=product_id or None,
            product_ids=normalized_product_ids(product_id, product_ids),
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
    except ValueError as exc:
        return redirect_with_message("/incoming-requests", validation=str(exc))
    return redirect_with_message("/incoming-requests", message="Safety message saved.")


@router.post(
    "/incoming-requests/{request_id}/create-case",
    dependencies=[Depends(require_any_permission("create", "icsr_workflow"))],
)
def create_case_from_incoming_request_form(
    request_id: str,
    case_number: str | None = Form(None),
    case_type: str | None = Form("spontaneous"),
    report_type: str | None = Form(None),
    initial_received_date: str | None = Form(None),
    latest_received_date: str | None = Form(None),
    seriousness: str | None = Form(None),
    narrative: str | None = Form(None),
    workflow_status: str = Form("data_entry"),
    expected_version: int = Form(..., gt=0),
    expected_inputs: str = Form(..., min_length=64, max_length=64),
    db: Session = Depends(get_db),
):
    row = crud.get_incoming_request(db, request_id)
    if not row:
        return redirect_with_message("/incoming-requests", error="Safety message not found.")
    require_version(row, expected_version)
    protect_snapshot(db, ONBOARDING_INPUTS, expected_inputs)
    try:
        case = crud.create_case_from_incoming_request(
            db,
            row,
            {
                "case_number": case_number,
                "case_type": case_type,
                "report_type": report_type,
                "initial_received_date": optional_date(initial_received_date),
                "latest_received_date": optional_date(latest_received_date),
                "seriousness": seriousness,
                "narrative": narrative,
                "workflow_status": workflow_status,
            },
        )
    except ValueError as exc:
        return redirect_with_message("/incoming-requests", validation=str(exc))
    return redirect_with_message(f"/cases/{case.id}", message="Case saved.")


@router.get(
    "/incoming-requests/{request_id}/case-preview",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("create", "icsr_workflow"))],
)
def case_preview_from_incoming_request(
    request_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    row = crud.get_incoming_request(db, request_id)
    if not row:
        return redirect_with_message("/incoming-requests", error="Safety message not found.")
    validation = crud.incoming_request_case_creation_error(row)
    if validation:
        return redirect_with_message("/incoming-requests", validation=validation)
    received_date = row.received_date or (row.created_at.date() if row.created_at else date.today())
    seriousness = "serious" if (row.seriousness or "").strip().lower() == "serious" else "non-serious"
    return templates.TemplateResponse(
        request,
        "incoming_case_preview.html",
        {
            "request": request,
            "input_snapshot": snapshot_token(db, ONBOARDING_INPUTS),
            "active_page": "incoming_requests",
            "item": row,
            "case_defaults": {
                "case_type": "spontaneous",
                "report_type": row.request_type or "safety_message",
                "initial_received_date": received_date,
                "latest_received_date": received_date,
                "seriousness": seriousness,
                "workflow_status": "data_entry",
                "narrative": crud.incoming_request_case_narrative(row),
            },
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/incoming-requests/{request_id}/triage",
    dependencies=[Depends(require_any_permission("edit", "comment", "icsr_workflow"))],
)
def save_incoming_request_triage_form(
    request_id: str,
    request: Request,
    triage_status: str = Form("in_progress"),
    safety_relevance: str | None = Form(None),
    potential_icsr: str | None = Form(None),
    validity_status: str | None = Form(None),
    has_identifiable_patient: bool = Form(False),
    has_identifiable_reporter: bool = Form(False),
    has_suspect_product: bool = Form(False),
    has_adverse_event: bool = Form(False),
    triage_result: str | None = Form(None),
    triage_comment: str | None = Form(None),
    manual_override: bool = Form(False),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    row = crud.get_incoming_request(db, request_id)
    if not row:
        return redirect_with_message("/incoming-requests", error="Safety message not found.")
    require_version(row, expected_version)
    current_user = getattr(request.state, "current_user", None)
    try:
        crud.save_incoming_request_triage(
            db,
            row,
            schemas.IncomingRequestTriageUpdate(
                triage_status=triage_status,
                safety_relevance=safety_relevance or None,
                potential_icsr=potential_icsr or None,
                validity_status=validity_status or None,
                has_identifiable_patient=has_identifiable_patient,
                has_identifiable_reporter=has_identifiable_reporter,
                has_suspect_product=has_suspect_product,
                has_adverse_event=has_adverse_event,
                triage_result=triage_result or None,
                triage_comment=triage_comment,
                manual_override=manual_override,
            ),
            changed_by_user_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        return redirect_with_message("/incoming-requests", validation=str(exc))
    return redirect_with_message("/incoming-requests", message="Triage saved.")


@router.post(
    "/incoming-requests/{request_id}/link-case",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def link_incoming_request_to_case_form(
    request_id: str,
    request: Request,
    case_id: str = Form(...),
    link_type: str = Form("manual_link"),
    comment: str | None = Form(None),
    expected_version: int = Form(..., gt=0),
    expected_case_versions: str = Form(...),
    db: Session = Depends(get_db),
):
    row = crud.get_incoming_request(db, request_id)
    if not row:
        return redirect_with_message("/incoming-requests", error="Safety message not found.")
    require_version(row, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        return redirect_with_message("/incoming-requests", error="Case not found.")
    try:
        versions = TypeAdapter(dict[str, PositiveInt]).validate_json(expected_case_versions)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid case versions") from exc
    if case_id not in versions:
        raise HTTPException(status_code=422, detail="Missing selected case version")
    require_version(case, versions[case_id])
    current_user = getattr(request.state, "current_user", None)
    try:
        crud.link_incoming_request_to_case(
            db,
            row,
            case,
            schemas.IncomingRequestCaseLinkCreate(
                case_id=case_id,
                link_type=link_type,
                comment=comment,
            ),
            changed_by_user_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        return redirect_with_message("/incoming-requests", validation=str(exc))
    return redirect_with_message("/incoming-requests", message="Safety message linked to case.")


@router.post(
    "/incoming-requests/{request_id}/status",
    dependencies=[Depends(require_any_permission("edit", "comment"))],
)
def update_incoming_request_status_form(
    request_id: str,
    request: Request,
    status_value: str = Form(...),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    row = crud.get_incoming_request(db, request_id)
    if not row:
        return redirect_with_message("/incoming-requests", error="Safety message not found.")
    require_version(row, expected_version)
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
    return redirect_with_message("/incoming-requests", message="Safety message status saved.")


@router.get("/api/incoming-requests", response_model=list[schemas.IncomingRequestRead])
def api_list_incoming_requests(db: Session = Depends(get_db)):
    rows = crud.list_incoming_requests(db)
    token = snapshot_token(db, ONBOARDING_INPUTS)
    for row in rows:
        row.input_snapshot = token
    return rows


@router.put(
    "/api/incoming-requests/{request_id}/products",
    response_model=schemas.IncomingRequestRead,
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def api_update_incoming_request_products(
    request_id: str,
    request: Request,
    payload: schemas.IncomingRequestProductsUpdate,
    db: Session = Depends(get_db),
):
    row = crud.get_incoming_request(db, request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Incoming request not found")
    require_version(row, payload.expected_version)
    current_user = getattr(request.state, "current_user", None)
    try:
        return crud.update_incoming_request_products(
            db,
            row,
            payload.product_ids,
            changed_by_user_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch(
    "/api/incoming-requests/{request_id}/triage",
    response_model=schemas.IncomingRequestRead,
    dependencies=[Depends(require_any_permission("edit", "comment", "icsr_workflow"))],
)
def api_save_incoming_request_triage(
    request_id: str,
    payload: schemas.VersionedIncomingRequestTriageUpdate,
    db: Session = Depends(get_db),
):
    row = crud.get_incoming_request(db, request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Safety message not found")
    require_version(row, payload.expected_version)
    try:
        return crud.save_incoming_request_triage(db, row, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/incoming-requests/{request_id}/create-case",
    response_model=schemas.CaseRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_any_permission("create", "icsr_workflow"))],
)
def api_create_case_from_incoming_request(
    request_id: str,
    payload: schemas.IncomingRequestCaseVersion,
    db: Session = Depends(get_db),
):
    row = crud.get_incoming_request(db, request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Safety message not found")
    require_version(row, payload.expected_version)
    protect_snapshot(db, ONBOARDING_INPUTS, payload.expected_inputs)
    try:
        return crud.create_case_from_incoming_request(db, row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/incoming-requests/{request_id}/case-links",
    response_model=schemas.IncomingRequestCaseLinkRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def api_link_incoming_request_to_case(
    request_id: str,
    payload: schemas.VersionedIncomingRequestCaseLinkCreate,
    db: Session = Depends(get_db),
):
    row = crud.get_incoming_request(db, request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Safety message not found")
    require_version(row, payload.expected_version)
    case = crud.get_case(db, payload.case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    require_version(case, payload.expected_case_version)
    try:
        return crud.link_incoming_request_to_case(db, row, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def render_gpt_analysis_page(
    request: Request,
    db: Session,
    *,
    source_text: str = "",
    draft: dict | None = None,
    message: str | None = None,
    validation: str | None = None,
):
    return templates.TemplateResponse(
        request,
        "gpt_analysis.html",
        {
            "request": request,
            "input_snapshot": snapshot_token(db, ONBOARDING_INPUTS),
            "active_page": "gpt_analysis",
            "partners": crud.list_partners(db),
            "products": crud.list_products(db),
            "source_text": source_text,
            "draft": draft,
            "message": message or request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": validation or request.query_params.get("validation"),
        },
    )


def gpt_analysis_subject(request_type: str | None, possible_icsr: str | None) -> str:
    suffix = "possible ICSR" if possible_icsr == "yes" else (request_type or "safety message")
    return f"GPT analysis draft: {suffix}"[:255]


def gpt_analysis_raw_text(**values: str | None) -> str:
    labels = [
        ("source_text", "Source text"),
        ("request_type", "Request type"),
        ("product_ids", "Product IDs"),
        ("active_substance", "Active substance"),
        ("possible_icsr", "Possible ICSR"),
        ("patient_information", "Patient information"),
        ("adverse_event", "Adverse event"),
        ("seriousness", "Seriousness"),
        ("seriousness_criteria", "Seriousness criteria"),
        ("missing_information", "Missing information"),
        ("recommended_next_action", "Recommended next action"),
        ("validity_assessment", "Validity assessment"),
        ("gpt_json_output", "GPT JSON output"),
    ]
    return "\n\n".join(
        f"{label}:\n{value}"
        for key, label in labels
        if (value := (values.get(key) or "").strip())
    )


def render_page(
    request: Request,
    db: Session,
    *,
    search: str | None = None,
    status_filter: str | None = None,
    quick_filter: str | None = None,
    source_text: str = "",
    draft: dict | None = None,
    message: str | None = None,
):
    if draft and draft.get("product_id") and not draft.get("product_ids"):
        draft["product_ids"] = [draft["product_id"]]
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
            *row.product_names,
            row.message_number,
            row.source_type,
            row.source_name,
            row.channel,
            row.message_category,
            row.reporter_name,
            row.reporter_contact,
        )
        and (not status_filter or row.status == status_filter)
        and quick_filter_matches(row, quick_filter)
    ]
    filters = {
        "search": search or "",
        "status_filter": status_filter or "",
        "quick_filter": quick_filter or "",
        "active": active_filters(
            search=search,
            status_filter=status_filter,
            quick_filter=quick_filter,
        ),
    }
    return templates.TemplateResponse(
        request,
        "incoming_requests.html",
        {
            "request": request,
            "input_snapshot": snapshot_token(db, ONBOARDING_INPUTS),
            "active_page": "incoming_requests",
            "incoming_requests": incoming_requests,
            "partners": crud.list_partners(db),
            "products": crud.list_products(db),
            "cases": crud.list_cases(db),
            "case_versions": {case.id: case.version for case in crud.list_cases(db)},
            "status_options": INCOMING_REQUEST_STATUSES,
            "triage_status_options": TRIAGE_STATUS_OPTIONS,
            "safety_relevance_options": SAFETY_RELEVANCE_OPTIONS,
            "potential_icsr_options": POTENTIAL_ICSR_OPTIONS,
            "validity_status_options": VALIDITY_STATUS_OPTIONS,
            "triage_result_options": TRIAGE_RESULT_OPTIONS,
            "case_link_type_options": CASE_LINK_TYPE_OPTIONS,
            "quick_filter_options": QUICK_FILTER_OPTIONS,
            "filters": filters,
            "total_count": len(all_requests),
            "source_text": source_text,
            "draft": draft,
            "message": message or request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


def normalized_product_ids(
    product_id: str | None,
    product_ids: list[str] | None,
) -> list[str]:
    normalized: list[str] = []
    for value in product_ids or []:
        value = (value or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    if product_id and product_id not in normalized:
        normalized.insert(0, product_id)
    return normalized


def optional_date(value: str | None) -> date | None:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def quick_filter_matches(row, quick_filter: str | None) -> bool:
    if not quick_filter:
        return True
    active_links = [link for link in row.case_links if not link.is_deleted]
    if quick_filter == "new":
        return row.status == "new"
    if quick_filter == "awaiting_triage":
        return row.triage_status in {"not_started", "in_progress"}
    if quick_filter == "valid_icsr":
        return row.validity_status == "valid_icsr" and row.triage_result == "create_case"
    if quick_filter == "awaiting_follow_up":
        return row.triage_result == "request_follow_up" or row.status == "awaiting_information"
    if quick_filter == "closed_no_case":
        return row.status == "closed_no_case" or row.triage_result == "no_case_required"
    if quick_filter == "converted_to_case":
        return row.status == "converted_to_case"
    if quick_filter == "linked_to_case":
        return bool(active_links)
    if quick_filter == "without_case":
        return not active_links
    return True
