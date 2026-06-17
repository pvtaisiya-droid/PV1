from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission, require_permission
from app.database import get_db
from app.models import PSURPartnerRequest
from app.statuses import status_codes
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, in_date_range, redirect_with_message


router = APIRouter()

PSUR_TYPE_OPTIONS = ["PSUR", "PBRER", "Local Safety Report"]
PSUR_STATUS_OPTIONS = status_codes("psur")
SECTION_STATUS_OPTIONS = ["Not Started", "Draft", "Under Review", "Approved"]
PARTNER_REQUEST_TYPES = [
    "Cases",
    "Reconciliation",
    "Regulatory Actions",
    "Sales Data",
    "Product Information",
    "Other",
]
PARTNER_REQUEST_STATUSES = ["Not Sent", "Sent", "Received", "Overdue", "Closed"]
DOCUMENT_TYPES = [
    "Draft",
    "Final",
    "Appendix",
    "Partner Response",
    "Submission Confirmation",
    "Other",
]


def current_user_id(request: Request) -> str | None:
    user = getattr(request.state, "current_user", None)
    return user.id if user else None


def optional_date(value: str | None) -> str | None:
    return value or None


def psur_reference_context(db: Session) -> dict:
    return {
        "substances": crud.list_substances(db),
        "products": crud.list_products(db),
        "partners": crud.list_partners(db),
        "contacts": crud.list_contract_contacts(db),
        "users": crud.list_users(db),
        "psur_type_options": PSUR_TYPE_OPTIONS,
        "psur_status_options": PSUR_STATUS_OPTIONS,
        "section_status_options": SECTION_STATUS_OPTIONS,
        "partner_request_types": PARTNER_REQUEST_TYPES,
        "partner_request_statuses": PARTNER_REQUEST_STATUSES,
        "document_types": DOCUMENT_TYPES,
    }


@router.get("/psur", response_class=HTMLResponse)
def psur_page(
    request: Request,
    search: str | None = None,
    active_substance_id: str | None = None,
    product_id: str | None = None,
    status_filter: str | None = None,
    responsible_user_id: str | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    overdue_only: bool = False,
    db: Session = Depends(get_db),
):
    today = date.today()
    all_plans = crud.list_psur_plans(db)
    plans = [
        plan
        for plan in all_plans
        if contains_search(
            search,
            plan.psur_type,
            plan.status,
            plan.active_substance.substance_name if plan.active_substance else "",
            plan.product.product_name if plan.product else "",
            plan.responsible_user.full_name if plan.responsible_user else "",
            plan.responsible_user.email if plan.responsible_user else "",
        )
        and (not active_substance_id or plan.active_substance_id == active_substance_id)
        and (not product_id or plan.product_id == product_id)
        and (not status_filter or plan.status == status_filter)
        and (not responsible_user_id or plan.responsible_user_id == responsible_user_id)
        and in_date_range(plan.reporting_period_start, period_from, None)
        and in_date_range(plan.reporting_period_end, None, period_to)
        and in_date_range(plan.due_date_submission, due_from, due_to)
        and (
            not overdue_only
            or (
                plan.due_date_submission
                and plan.due_date_submission < today
                and plan.status not in {"Submitted", "Archived"}
            )
        )
    ]
    filters = {
        "search": search or "",
        "active_substance_id": active_substance_id or "",
        "product_id": product_id or "",
        "status_filter": status_filter or "",
        "responsible_user_id": responsible_user_id or "",
        "period_from": period_from or "",
        "period_to": period_to or "",
        "due_from": due_from or "",
        "due_to": due_to or "",
        "overdue_only": overdue_only,
        "active": active_filters(
            search=search,
            active_substance_id=active_substance_id,
            product_id=product_id,
            status_filter=status_filter,
            responsible_user_id=responsible_user_id,
            period_from=period_from,
            period_to=period_to,
            due_from=due_from,
            due_to=due_to,
            overdue_only=overdue_only,
        ),
    }
    return templates.TemplateResponse(
        request,
        "psur.html",
        {
            "request": request,
            "active_page": "psur",
            "plans": plans,
            "filters": filters,
            "total_count": len(all_plans),
            "stats": crud.psur_dashboard_stats(db),
            "today": today,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            **psur_reference_context(db),
        },
    )


@router.post("/psur", dependencies=[Depends(require_permission("create"))])
def create_psur_plan_form(
    request: Request,
    active_substance_id: str = Form(...),
    product_id: str | None = Form(None),
    psur_type: str = Form("PSUR"),
    reporting_period_start: str = Form(...),
    reporting_period_end: str = Form(...),
    data_lock_point: str = Form(...),
    due_date_internal: str | None = Form(None),
    due_date_submission: str | None = Form(None),
    frequency: str | None = Form(None),
    responsible_user_id: str | None = Form(None),
    reviewer_user_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not crud.get_substance(db, active_substance_id):
        return redirect_with_message("/psur", validation="Active substance is required.")
    if product_id and not crud.get_product(db, product_id):
        return redirect_with_message("/psur", validation="Product not found.")
    try:
        plan = crud.create_psur_plan(
            db,
            schemas.PSURPlanCreate(
                active_substance_id=active_substance_id,
                product_id=product_id or None,
                psur_type=psur_type,
                reporting_period_start=reporting_period_start,
                reporting_period_end=reporting_period_end,
                data_lock_point=data_lock_point,
                due_date_internal=optional_date(due_date_internal),
                due_date_submission=optional_date(due_date_submission),
                frequency=frequency,
                responsible_user_id=responsible_user_id or None,
                reviewer_user_id=reviewer_user_id or None,
            ),
            created_by_user_id=current_user_id(request),
        )
    except (IntegrityError, ValueError):
        db.rollback()
        return redirect_with_message("/psur", error="PSUR plan could not be saved.")
    return redirect_with_message(f"/psur/{plan.id}", message="PSUR plan saved.")


@router.get("/psur/{psur_plan_id}", response_class=HTMLResponse)
def psur_detail(
    psur_plan_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="PSUR plan not found")
    return templates.TemplateResponse(
        request,
        "psur_detail.html",
        {
            "request": request,
            "active_page": "psur",
            "plan": plan,
            "tasks": crud.list_psur_tasks(db, plan.id),
            "audit_entries": crud.list_psur_audit_entries(db, plan),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            **psur_reference_context(db),
        },
    )


@router.post("/psur/{psur_plan_id}", dependencies=[Depends(require_permission("edit"))])
def update_psur_plan_form(
    psur_plan_id: str,
    request: Request,
    product_id: str | None = Form(None),
    psur_type: str = Form("PSUR"),
    reporting_period_start: str = Form(...),
    reporting_period_end: str = Form(...),
    data_lock_point: str = Form(...),
    due_date_internal: str | None = Form(None),
    due_date_submission: str | None = Form(None),
    frequency: str | None = Form(None),
    responsible_user_id: str | None = Form(None),
    reviewer_user_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        return redirect_with_message("/psur", error="PSUR plan not found.")
    crud.update_psur_plan(
        db,
        plan,
        schemas.PSURPlanUpdate(
            product_id=product_id or None,
            psur_type=psur_type,
            reporting_period_start=reporting_period_start,
            reporting_period_end=reporting_period_end,
            data_lock_point=data_lock_point,
            due_date_internal=optional_date(due_date_internal),
            due_date_submission=optional_date(due_date_submission),
            frequency=frequency,
            responsible_user_id=responsible_user_id or None,
            reviewer_user_id=reviewer_user_id or None,
        ),
        changed_by_user_id=current_user_id(request),
    )
    return redirect_with_message(f"/psur/{psur_plan_id}", message="PSUR plan saved.")


@router.post(
    "/psur/{psur_plan_id}/status",
    dependencies=[Depends(require_any_permission("edit", "approve"))],
)
def update_psur_status_form(
    psur_plan_id: str,
    request: Request,
    status_value: str = Form(...),
    change_reason: str | None = Form(None),
    db: Session = Depends(get_db),
):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        return redirect_with_message("/psur", error="PSUR plan not found.")
    crud.update_psur_status(
        db,
        plan,
        schemas.PSURStatusUpdate(status=status_value, change_reason=change_reason),
        changed_by_user_id=current_user_id(request),
    )
    return redirect_with_message(f"/psur/{psur_plan_id}", message="PSUR status updated.")


@router.post("/psur/{psur_plan_id}/delete", dependencies=[Depends(require_permission("soft_delete"))])
def delete_psur_plan_form(
    psur_plan_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    db: Session = Depends(get_db),
):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        return redirect_with_message("/psur", error="PSUR plan not found.")
    crud.delete_psur_plan(
        db,
        plan,
        deleted_by_user_id=current_user_id(request),
        delete_reason=delete_reason,
    )
    return redirect_with_message("/psur", message="PSUR plan archived.")


@router.post(
    "/psur/{psur_plan_id}/products",
    dependencies=[Depends(require_any_permission("create", "edit"))],
)
def add_psur_product_form(
    psur_plan_id: str,
    product_id: str = Form(...),
    country: str | None = Form(None),
    marketing_authorisation_number: str | None = Form(None),
    included_in_report: bool = Form(False),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        return redirect_with_message("/psur", error="PSUR plan not found.")
    if not crud.get_product(db, product_id):
        return redirect_with_message(f"/psur/{psur_plan_id}#products", validation="Product not found.")
    try:
        crud.add_psur_product(
            db,
            plan,
            schemas.PSURProductCreate(
                product_id=product_id,
                country=country,
                marketing_authorisation_number=marketing_authorisation_number,
                included_in_report=included_in_report,
                comment=comment,
            ),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message(f"/psur/{psur_plan_id}#products", error="Product could not be saved.")
    return redirect_with_message(f"/psur/{psur_plan_id}#products", message="Product saved.")


@router.post("/psur/{psur_plan_id}/find-cases", dependencies=[Depends(require_permission("edit"))])
def find_psur_cases_form(psur_plan_id: str, db: Session = Depends(get_db)):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        return redirect_with_message("/psur", error="PSUR plan not found.")
    created = crud.find_and_link_psur_cases(db, plan)
    return redirect_with_message(
        f"/psur/{psur_plan_id}#cases",
        message=f"{len(created)} ICSR cases linked.",
    )


@router.post("/psur/{psur_plan_id}/cases/{psur_case_id}", dependencies=[Depends(require_permission("edit"))])
def update_psur_case_form(
    psur_plan_id: str,
    psur_case_id: str,
    request: Request,
    case_included: bool = Form(False),
    reason_excluded: str | None = Form(None),
    seriousness: str | None = Form(None),
    listedness: str | None = Form(None),
    case_origin: str | None = Form(None),
    assessment_comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    psur_case = crud.get_psur_case(db, psur_case_id)
    if not psur_case or psur_case.psur_plan_id != psur_plan_id:
        return redirect_with_message(f"/psur/{psur_plan_id}#cases", error="Case not found.")
    crud.update_psur_case(
        db,
        psur_case,
        schemas.PSURCaseUpdate(
            case_included=case_included,
            reason_excluded=reason_excluded,
            seriousness=seriousness,
            listedness=listedness,
            case_origin=case_origin,
            assessment_comment=assessment_comment,
        ),
        changed_by_user_id=current_user_id(request),
    )
    return redirect_with_message(f"/psur/{psur_plan_id}#cases", message="Case saved.")


@router.post(
    "/psur/{psur_plan_id}/partner-requests",
    dependencies=[Depends(require_any_permission("create", "create_related_requests"))],
)
def create_partner_requests_form(
    psur_plan_id: str,
    request: Request,
    partner_ids: list[str] = Form([]),
    request_type: str = Form("Cases"),
    request_date: str | None = Form(None),
    due_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        return redirect_with_message("/psur", error="PSUR plan not found.")
    if not partner_ids:
        return redirect_with_message(
            f"/psur/{psur_plan_id}#partner-requests",
            validation="Select at least one partner.",
        )
    created = crud.create_psur_partner_requests(
        db,
        plan,
        partner_ids=partner_ids,
        request_type=request_type,
        request_date=optional_date(request_date),
        due_date=optional_date(due_date),
        created_by_user_id=current_user_id(request),
    )
    return redirect_with_message(
        f"/psur/{psur_plan_id}#partner-requests",
        message=f"{len(created)} partner requests created.",
    )


@router.post(
    "/psur/{psur_plan_id}/partner-requests/{request_id}",
    dependencies=[Depends(require_any_permission("edit", "comment"))],
)
def update_partner_request_form(
    psur_plan_id: str,
    request_id: str,
    request: Request,
    partner_id: str = Form(...),
    contact_person_id: str | None = Form(None),
    request_type: str = Form("Cases"),
    request_date: str | None = Form(None),
    due_date: str | None = Form(None),
    status_value: str = Form("Not Sent"),
    response_summary: str | None = Form(None),
    document_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        return redirect_with_message("/psur", error="PSUR plan not found.")
    request_row = (
        db.query(PSURPartnerRequest)
        .filter(
            PSURPartnerRequest.id == request_id,
            PSURPartnerRequest.psur_plan_id == psur_plan_id,
            PSURPartnerRequest.is_deleted.is_(False),
        )
        .first()
    )
    if not request_row:
        return redirect_with_message(f"/psur/{psur_plan_id}#partner-requests", error="Request not found.")
    crud.update_psur_partner_request(
        db,
        request_row,
        schemas.PSURPartnerRequestCreate(
            partner_id=partner_id,
            contact_person_id=contact_person_id or None,
            request_type=request_type,
            request_date=optional_date(request_date),
            due_date=optional_date(due_date),
            status=status_value,
            response_summary=response_summary,
            document_id=document_id or None,
        ),
        changed_by_user_id=current_user_id(request),
    )
    return redirect_with_message(f"/psur/{psur_plan_id}#partner-requests", message="Request saved.")


@router.post(
    "/psur/{psur_plan_id}/sections/{section_id}",
    dependencies=[Depends(require_any_permission("edit", "comment", "approve"))],
)
def update_section_form(
    psur_plan_id: str,
    section_id: str,
    request: Request,
    section_status: str = Form("Draft"),
    assigned_to: str | None = Form(None),
    reviewed_by: str | None = Form(None),
    section_text: str | None = Form(None),
    comment: str | None = Form(None),
    human_confirmed: bool = Form(False),
    db: Session = Depends(get_db),
):
    section = crud.get_psur_section(db, section_id)
    if not section or section.psur_plan_id != psur_plan_id:
        return redirect_with_message(f"/psur/{psur_plan_id}#sections", error="Section not found.")
    crud.update_psur_section(
        db,
        section,
        schemas.PSURSectionUpdate(
            section_status=section_status,
            assigned_to=assigned_to or None,
            reviewed_by=reviewed_by or None,
            section_text=section_text,
            comment=comment,
            human_confirmed=human_confirmed,
        ),
        changed_by_user_id=current_user_id(request),
    )
    return redirect_with_message(f"/psur/{psur_plan_id}#sections", message="Section saved.")


@router.post(
    "/psur/{psur_plan_id}/sections/{section_id}/generate",
    dependencies=[Depends(require_any_permission("edit", "comment"))],
)
def generate_section_draft_form(
    psur_plan_id: str,
    section_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    plan = crud.get_psur_plan(db, psur_plan_id)
    section = crud.get_psur_section(db, section_id)
    if not plan or not section or section.psur_plan_id != psur_plan_id:
        return redirect_with_message(f"/psur/{psur_plan_id}#sections", error="Section not found.")
    crud.generate_psur_section_draft(
        db,
        plan,
        section,
        changed_by_user_id=current_user_id(request),
    )
    return redirect_with_message(
        f"/psur/{psur_plan_id}#sections",
        message="Draft generated for human review.",
    )


@router.post(
    "/psur/{psur_plan_id}/documents",
    dependencies=[Depends(require_any_permission("upload", "create"))],
)
def add_psur_document_form(
    psur_plan_id: str,
    request: Request,
    document_type: str = Form("Draft"),
    file_name: str = Form(...),
    file_path: str | None = Form(None),
    version_number: str | None = Form(None),
    is_final: bool = Form(False),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        return redirect_with_message("/psur", error="PSUR plan not found.")
    if not file_name.strip():
        return redirect_with_message(f"/psur/{psur_plan_id}#documents", validation="File name is required.")
    crud.add_psur_document(
        db,
        plan,
        schemas.PSURDocumentCreate(
            document_type=document_type,
            file_name=file_name.strip(),
            file_path=file_path,
            version_number=version_number,
            is_final=is_final,
            comment=comment,
        ),
        uploaded_by_user_id=current_user_id(request),
    )
    return redirect_with_message(f"/psur/{psur_plan_id}#documents", message="Document saved.")


@router.post(
    "/psur/{psur_plan_id}/tasks",
    dependencies=[Depends(require_any_permission("create", "create_related_requests", "comment"))],
)
def add_psur_task_form(
    psur_plan_id: str,
    request: Request,
    title: str = Form(...),
    description: str | None = Form(None),
    status_value: str = Form("Open"),
    priority: str = Form("Normal"),
    due_date: str | None = Form(None),
    assigned_to_user_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        return redirect_with_message("/psur", error="PSUR plan not found.")
    if not title.strip():
        return redirect_with_message(f"/psur/{psur_plan_id}#tasks", validation="Task title is required.")
    crud.create_task(
        db,
        schemas.TaskCreate(
            title=title.strip(),
            description=description,
            status=status_value,
            priority=priority,
            due_date=optional_date(due_date),
            assigned_to_user_id=assigned_to_user_id or None,
            related_entity_type="PSUR",
            related_entity_id=psur_plan_id,
        ),
        created_by_user_id=current_user_id(request),
    )
    return redirect_with_message(f"/psur/{psur_plan_id}#tasks", message="Task saved.")


@router.get("/api/psur", response_model=list[schemas.PSURPlanRead])
def api_list_psur_plans(db: Session = Depends(get_db)):
    return crud.list_psur_plans(db)


@router.post(
    "/api/psur",
    response_model=schemas.PSURPlanRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("create"))],
)
def api_create_psur_plan(
    payload: schemas.PSURPlanCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    return crud.create_psur_plan(db, payload, created_by_user_id=current_user_id(request))


@router.get("/api/psur/{psur_plan_id}", response_model=schemas.PSURPlanRead)
def api_get_psur_plan(psur_plan_id: str, db: Session = Depends(get_db)):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="PSUR plan not found")
    return plan


@router.get("/api/psur/{psur_plan_id}/cases/export.csv", dependencies=[Depends(require_permission("export"))])
def api_export_psur_cases(psur_plan_id: str, db: Session = Depends(get_db)):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="PSUR plan not found")
    return Response(
        crud.export_psur_cases_csv(plan),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=psur_case_line_listing.csv"},
    )


@router.get(
    "/api/psur/{psur_plan_id}/partner-requests/export.csv",
    dependencies=[Depends(require_permission("export"))],
)
def api_export_psur_partner_requests(psur_plan_id: str, db: Session = Depends(get_db)):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="PSUR plan not found")
    return Response(
        crud.export_psur_partner_requests_csv(plan),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=psur_partner_requests.csv"},
    )


@router.get("/api/psur/{psur_plan_id}/audit/export.csv", dependencies=[Depends(require_permission("export"))])
def api_export_psur_audit(psur_plan_id: str, db: Session = Depends(get_db)):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="PSUR plan not found")
    return Response(
        crud.export_psur_audit_csv(db, plan),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=psur_audit_trail.csv"},
    )


@router.get("/api/psur/{psur_plan_id}/summary.rtf", dependencies=[Depends(require_permission("export"))])
def api_export_psur_summary(psur_plan_id: str, db: Session = Depends(get_db)):
    plan = crud.get_psur_plan(db, psur_plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="PSUR plan not found")
    return Response(
        crud.export_psur_summary_rtf(plan),
        media_type="application/rtf",
        headers={"Content-Disposition": "attachment; filename=psur_plan_summary.rtf"},
    )
