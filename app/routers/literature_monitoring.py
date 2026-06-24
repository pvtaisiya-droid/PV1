from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission, require_permission
from app.database import get_db
from app.pagination import paginate_items
from app.psmf import list_psmf_components
from app.routers.placeholders import store_document_file
from app.templating import templates
from app.ui_helpers import active_filters, contains_search, in_date_range, redirect_with_message


router = APIRouter()

PLAN_FREQUENCY_OPTIONS = ["weekly", "monthly", "quarterly", "on_demand"]
PLAN_STATUS_OPTIONS = ["active", "paused", "archived"]
LOG_STATUS_OPTIONS = ["draft", "completed", "reviewed", "closed"]
SEARCH_RESULT_OPTIONS = [
    "nothing_found",
    "potential_icsr",
    "signal",
    "medical_information",
]
RELEVANCE_OPTIONS = ["not_relevant", "relevant", "needs_assessment"]
RESULT_TYPE_OPTIONS = [
    "potential_icsr",
    "signal",
    "medical_information",
    "safety_information",
    "other",
]
PV_DECISION_OPTIONS = [
    "no_action",
    "create_icsr",
    "add_to_rmp",
    "add_to_psur",
    "add_to_psmf",
    "discuss_required",
]
PROCESSING_STATUS_OPTIONS = ["new", "under_review", "processed", "closed"]


def current_user_id(request: Request) -> str | None:
    user = getattr(request.state, "current_user", None)
    return user.id if user else None


def optional_date(value: str | None) -> str | None:
    return value or None


def optional_int(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def selected_ids(values: list[str] | None) -> list[str]:
    return [value for value in values or [] if value]


def product_names(product_links) -> str:
    return ", ".join(
        link.product.product_name if link.product else link.product_id
        for link in product_links
        if not link.is_deleted
    )


def literature_reference_context(db: Session) -> dict:
    return {
        "partners": crud.list_partners(db),
        "products": crud.list_products(db),
        "substances": crud.list_substances(db),
        "users": crud.list_users(db),
        "plans_all": crud.list_literature_plans(db),
        "logs_all": crud.list_literature_search_logs(db),
        "psur_plans": crud.list_psur_plans(db),
        "psmf_components": list_psmf_components(db),
        "plan_frequency_options": PLAN_FREQUENCY_OPTIONS,
        "plan_status_options": PLAN_STATUS_OPTIONS,
        "log_status_options": LOG_STATUS_OPTIONS,
        "search_result_options": SEARCH_RESULT_OPTIONS,
        "relevance_options": RELEVANCE_OPTIONS,
        "result_type_options": RESULT_TYPE_OPTIONS,
        "pv_decision_options": PV_DECISION_OPTIONS,
        "processing_status_options": PROCESSING_STATUS_OPTIONS,
    }


def filter_literature_plans(
    plans,
    *,
    search: str | None = None,
    partner_id: str | None = None,
    product_id: str | None = None,
    status_filter: str | None = None,
    responsible_user_id: str | None = None,
):
    return [
        plan
        for plan in plans
        if contains_search(
            search,
            plan.partner.partner_name if plan.partner else "",
            product_names(plan.products),
            plan.active_substance.substance_name if plan.active_substance else "",
            plan.monitoring_sources,
            plan.search_strategy,
            plan.keywords,
            plan.territory,
            plan.responsible_user.full_name if plan.responsible_user else "",
        )
        and (not partner_id or plan.partner_id == partner_id)
        and (not product_id or any(link.product_id == product_id for link in plan.products))
        and (not status_filter or plan.status == status_filter)
        and (not responsible_user_id or plan.responsible_user_id == responsible_user_id)
    ]


def filter_literature_logs(
    logs,
    *,
    search: str | None = None,
    partner_id: str | None = None,
    product_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search_source: str | None = None,
    result_filter: str | None = None,
    responsible_user_id: str | None = None,
):
    return [
        log
        for log in logs
        if contains_search(
            search,
            log.plan.partner.partner_name if log.plan and log.plan.partner else "",
            log.partner.partner_name if log.partner else "",
            product_names(log.products),
            log.search_source,
            log.search_strategy,
            log.summary,
            log.comment,
            log.searched_by.full_name if log.searched_by else "",
        )
        and (not partner_id or (log.partner_id or (log.plan.partner_id if log.plan else None)) == partner_id)
        and (not product_id or any(link.product_id == product_id for link in log.products))
        and in_date_range(log.search_date, date_from, date_to)
        and (not search_source or search_source.lower() in (log.search_source or "").lower())
        and (not result_filter or log.result == result_filter)
        and (
            not responsible_user_id
            or log.searched_by_user_id == responsible_user_id
            or (log.plan and log.plan.responsible_user_id == responsible_user_id)
        )
    ]


def filter_literature_results(
    results,
    *,
    search: str | None = None,
    partner_id: str | None = None,
    product_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    result_type: str | None = None,
    processing_status: str | None = None,
    pv_decision: str | None = None,
):
    return [
        result
        for result in results
        if contains_search(
            search,
            result.publication_title,
            result.authors,
            result.journal_source,
            result.doi,
            result.url,
            result.abstract,
            result.specialist_comment,
            result.partner.partner_name if result.partner else "",
            product_names(result.products),
        )
        and (not partner_id or result.partner_id == partner_id)
        and (not product_id or any(link.product_id == product_id for link in result.products))
        and in_date_range(
            result.publication_date
            or (result.search_log.search_date if result.search_log else None)
            or result.created_at,
            date_from,
            date_to,
        )
        and (not result_type or result.result_type == result_type)
        and (not processing_status or result.processing_status == processing_status)
        and (not pv_decision or result.pv_decision == pv_decision)
    ]


@router.get("/literature-monitoring", response_class=HTMLResponse)
def literature_monitoring_page(
    request: Request,
    tab: str = "plan",
    search: str | None = None,
    partner_id: str | None = None,
    product_id: str | None = None,
    status_filter: str | None = None,
    responsible_user_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search_source: str | None = None,
    result_filter: str | None = None,
    result_type: str | None = None,
    processing_status: str | None = None,
    pv_decision: str | None = None,
    page: int = 1,
    per_page: int | None = None,
    db: Session = Depends(get_db),
):
    if tab not in {"plan", "log", "results"}:
        tab = "plan"

    all_plans = crud.list_literature_plans(db)
    all_logs = crud.list_literature_search_logs(db)
    all_results = crud.list_literature_results(db)
    plans = filter_literature_plans(
        all_plans,
        search=search if tab == "plan" else None,
        partner_id=partner_id if tab == "plan" else None,
        product_id=product_id if tab == "plan" else None,
        status_filter=status_filter if tab == "plan" else None,
        responsible_user_id=responsible_user_id if tab == "plan" else None,
    )
    logs = filter_literature_logs(
        all_logs,
        search=search if tab == "log" else None,
        partner_id=partner_id if tab == "log" else None,
        product_id=product_id if tab == "log" else None,
        date_from=date_from if tab == "log" else None,
        date_to=date_to if tab == "log" else None,
        search_source=search_source if tab == "log" else None,
        result_filter=result_filter if tab == "log" else None,
        responsible_user_id=responsible_user_id if tab == "log" else None,
    )
    results = filter_literature_results(
        all_results,
        search=search if tab == "results" else None,
        partner_id=partner_id if tab == "results" else None,
        product_id=product_id if tab == "results" else None,
        date_from=date_from if tab == "results" else None,
        date_to=date_to if tab == "results" else None,
        result_type=result_type if tab == "results" else None,
        processing_status=processing_status if tab == "results" else None,
        pv_decision=pv_decision if tab == "results" else None,
    )

    active_items = {"plan": plans, "log": logs, "results": results}[tab]
    page_items, pagination = paginate_items(active_items, page, per_page)
    if tab == "plan":
        plans = page_items
    elif tab == "log":
        logs = page_items
    else:
        results = page_items

    filters = {
        "search": search or "",
        "partner_id": partner_id or "",
        "product_id": product_id or "",
        "status_filter": status_filter or "",
        "responsible_user_id": responsible_user_id or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "search_source": search_source or "",
        "result_filter": result_filter or "",
        "result_type": result_type or "",
        "processing_status": processing_status or "",
        "pv_decision": pv_decision or "",
        "active": active_filters(
            search=search,
            partner_id=partner_id,
            product_id=product_id,
            status_filter=status_filter,
            responsible_user_id=responsible_user_id,
            date_from=date_from,
            date_to=date_to,
            search_source=search_source,
            result_filter=result_filter,
            result_type=result_type,
            processing_status=processing_status,
            pv_decision=pv_decision,
        ),
    }
    return templates.TemplateResponse(
        request,
        "literature_monitoring.html",
        {
            "request": request,
            "active_page": "literature_monitoring",
            "tab": tab,
            "plans": plans,
            "logs": logs,
            "results": results,
            "total_count": len({"plan": all_plans, "log": all_logs, "results": all_results}[tab]),
            "filtered_count": len(active_items),
            "pagination": pagination,
            "stats": crud.literature_dashboard_stats(db),
            "filters": filters,
            "product_names": product_names,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            **literature_reference_context(db),
        },
    )


@router.post("/literature-monitoring/plans", dependencies=[Depends(require_permission("create"))])
def create_literature_plan_form(
    request: Request,
    partner_id: str = Form(...),
    product_ids: list[str] | None = Form(None),
    active_substance_id: str | None = Form(None),
    monitoring_sources: str = Form(...),
    frequency: str = Form("monthly"),
    search_strategy: str | None = Form(None),
    keywords: str | None = Form(None),
    territory: str | None = Form(None),
    responsible_user_id: str | None = Form(None),
    start_date: str = Form(...),
    end_date: str | None = Form(None),
    status_value: str = Form("active"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not selected_ids(product_ids):
        return redirect_with_message(
            "/literature-monitoring?tab=plan",
            validation="Select at least one product.",
        )
    try:
        plan = crud.create_literature_plan(
            db,
            schemas.LiteratureMonitoringPlanCreate(
                partner_id=partner_id,
                product_ids=selected_ids(product_ids),
                active_substance_id=active_substance_id or None,
                monitoring_sources=monitoring_sources,
                frequency=frequency,
                search_strategy=search_strategy,
                keywords=keywords,
                territory=territory,
                responsible_user_id=responsible_user_id or None,
                start_date=start_date,
                end_date=optional_date(end_date),
                status=status_value,
                comment=comment,
            ),
            created_by_user_id=current_user_id(request),
        )
    except (IntegrityError, ValueError) as exc:
        db.rollback()
        return redirect_with_message(
            "/literature-monitoring?tab=plan",
            error=f"Literature monitoring plan could not be saved. {exc}",
        )
    return redirect_with_message(
        f"/literature-monitoring?tab=plan#plan-{plan.id}",
        message="Literature monitoring plan saved.",
    )


@router.post("/literature-monitoring/plans/{plan_id}", dependencies=[Depends(require_permission("edit"))])
def update_literature_plan_form(
    plan_id: str,
    request: Request,
    partner_id: str = Form(...),
    product_ids: list[str] | None = Form(None),
    active_substance_id: str | None = Form(None),
    monitoring_sources: str = Form(...),
    frequency: str = Form("monthly"),
    search_strategy: str | None = Form(None),
    keywords: str | None = Form(None),
    territory: str | None = Form(None),
    responsible_user_id: str | None = Form(None),
    start_date: str = Form(...),
    end_date: str | None = Form(None),
    status_value: str = Form("active"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    plan = crud.get_literature_plan(db, plan_id)
    if not plan:
        return redirect_with_message("/literature-monitoring?tab=plan", error="Plan not found.")
    if not selected_ids(product_ids):
        return redirect_with_message(
            f"/literature-monitoring?tab=plan#plan-{plan_id}",
            validation="Select at least one product.",
        )
    try:
        crud.update_literature_plan(
            db,
            plan,
            schemas.LiteratureMonitoringPlanCreate(
                partner_id=partner_id,
                product_ids=selected_ids(product_ids),
                active_substance_id=active_substance_id or None,
                monitoring_sources=monitoring_sources,
                frequency=frequency,
                search_strategy=search_strategy,
                keywords=keywords,
                territory=territory,
                responsible_user_id=responsible_user_id or None,
                start_date=start_date,
                end_date=optional_date(end_date),
                status=status_value,
                comment=comment,
            ),
            changed_by_user_id=current_user_id(request),
        )
    except (IntegrityError, ValueError) as exc:
        db.rollback()
        return redirect_with_message(
            f"/literature-monitoring?tab=plan#plan-{plan_id}",
            error=f"Literature monitoring plan could not be saved. {exc}",
        )
    return redirect_with_message(
        f"/literature-monitoring?tab=plan#plan-{plan_id}",
        message="Literature monitoring plan saved.",
    )


@router.post(
    "/literature-monitoring/plans/{plan_id}/archive",
    dependencies=[Depends(require_any_permission("edit", "soft_delete"))],
)
def archive_literature_plan_form(
    plan_id: str,
    request: Request,
    archive_comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    plan = crud.get_literature_plan(db, plan_id)
    if not plan:
        return redirect_with_message("/literature-monitoring?tab=plan", error="Plan not found.")
    crud.archive_literature_plan(
        db,
        plan,
        changed_by_user_id=current_user_id(request),
        comment=archive_comment,
    )
    return redirect_with_message("/literature-monitoring?tab=plan", message="Plan archived.")


@router.post("/literature-monitoring/logs", dependencies=[Depends(require_permission("create"))])
def create_literature_log_form(
    request: Request,
    plan_id: str = Form(...),
    product_ids: list[str] | None = Form(None),
    partner_id: str | None = Form(None),
    active_substance_id: str | None = Form(None),
    search_date: str = Form(...),
    searched_by_user_id: str | None = Form(None),
    period_start: str = Form(...),
    period_end: str = Form(...),
    search_source: str = Form(...),
    search_strategy: str | None = Form(None),
    publications_found: int = Form(0),
    relevant_publications: int = Form(0),
    result_value: str = Form("nothing_found"),
    status_value: str = Form("completed"),
    summary: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        log = crud.create_literature_search_log(
            db,
            schemas.LiteratureSearchLogCreate(
                plan_id=plan_id,
                product_ids=selected_ids(product_ids),
                partner_id=partner_id or None,
                active_substance_id=active_substance_id or None,
                search_date=search_date,
                searched_by_user_id=searched_by_user_id or None,
                period_start=period_start,
                period_end=period_end,
                search_source=search_source,
                search_strategy=search_strategy,
                publications_found=publications_found,
                relevant_publications=relevant_publications,
                result=result_value,
                status=status_value,
                summary=summary,
                comment=comment,
            ),
            created_by_user_id=current_user_id(request),
        )
    except (IntegrityError, ValueError) as exc:
        db.rollback()
        return redirect_with_message(
            "/literature-monitoring?tab=log",
            error=f"Search log record could not be saved. {exc}",
        )
    message = "Search log record saved."
    if log.result != "nothing_found":
        message = "Search log record saved. Add a publication in Results."
    return redirect_with_message(
        f"/literature-monitoring?tab=log#log-{log.id}",
        message=message,
    )


@router.post("/literature-monitoring/logs/{log_id}", dependencies=[Depends(require_permission("edit"))])
def update_literature_log_form(
    log_id: str,
    request: Request,
    plan_id: str = Form(...),
    product_ids: list[str] | None = Form(None),
    partner_id: str | None = Form(None),
    active_substance_id: str | None = Form(None),
    search_date: str = Form(...),
    searched_by_user_id: str | None = Form(None),
    period_start: str = Form(...),
    period_end: str = Form(...),
    search_source: str = Form(...),
    search_strategy: str | None = Form(None),
    publications_found: int = Form(0),
    relevant_publications: int = Form(0),
    result_value: str = Form("nothing_found"),
    status_value: str = Form("completed"),
    summary: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    search_log = crud.get_literature_search_log(db, log_id)
    if not search_log:
        return redirect_with_message("/literature-monitoring?tab=log", error="Log record not found.")
    try:
        crud.update_literature_search_log(
            db,
            search_log,
            schemas.LiteratureSearchLogCreate(
                plan_id=plan_id,
                product_ids=selected_ids(product_ids),
                partner_id=partner_id or None,
                active_substance_id=active_substance_id or None,
                search_date=search_date,
                searched_by_user_id=searched_by_user_id or None,
                period_start=period_start,
                period_end=period_end,
                search_source=search_source,
                search_strategy=search_strategy,
                publications_found=publications_found,
                relevant_publications=relevant_publications,
                result=result_value,
                status=status_value,
                summary=summary,
                comment=comment,
            ),
            changed_by_user_id=current_user_id(request),
        )
    except (IntegrityError, ValueError) as exc:
        db.rollback()
        return redirect_with_message(
            f"/literature-monitoring?tab=log#log-{log_id}",
            error=f"Search log record could not be saved. {exc}",
        )
    return redirect_with_message(
        f"/literature-monitoring?tab=log#log-{log_id}",
        message="Search log record saved.",
    )


def store_optional_upload(upload: UploadFile | None) -> dict | None:
    if not upload or not (upload.filename or "").strip():
        return None
    stored_file = store_document_file(upload)
    return {
        "original_name": upload.filename,
        "content_type": upload.content_type,
        **stored_file,
    }


def create_literature_attachment(
    db: Session,
    result,
    upload_info: dict | None,
    *,
    document_kind: str,
    uploaded_by_user_id: str | None,
):
    if not upload_info:
        return None
    first_product_id = next(
        (link.product_id for link in result.products if link.product_id),
        None,
    )
    return crud.create_attachment(
        db,
        file_name=upload_info["original_name"],
        attachment_type=document_kind,
        document_title=f"{result.publication_title} - {document_kind}",
        document_type="literature",
        related_object_type="LiteratureResult",
        related_object_id=result.id,
        partner_id=result.partner_id,
        product_id=first_product_id,
        mime_type=upload_info.get("content_type") or "application/octet-stream",
        file_size_bytes=upload_info.get("file_size_bytes"),
        storage_path=upload_info.get("storage_path"),
        status="active",
        comment=f"Uploaded for literature result {result.id}.",
        checksum_sha256=upload_info.get("checksum_sha256"),
        uploaded_by_user_id=uploaded_by_user_id,
    )


@router.post(
    "/literature-monitoring/results",
    dependencies=[Depends(require_any_permission("create", "upload"))],
)
def create_literature_result_form(
    request: Request,
    search_log_id: str | None = Form(None),
    plan_id: str = Form(...),
    product_ids: list[str] | None = Form(None),
    partner_id: str | None = Form(None),
    active_substance_id: str | None = Form(None),
    publication_title: str = Form(...),
    authors: str | None = Form(None),
    journal_source: str | None = Form(None),
    publication_year: str | None = Form(None),
    publication_date: str | None = Form(None),
    doi: str | None = Form(None),
    url: str | None = Form(None),
    abstract: str | None = Form(None),
    relevance: str = Form("needs_assessment"),
    result_type: str = Form("other"),
    pv_decision: str = Form("no_action"),
    processing_status: str = Form("new"),
    specialist_comment: str | None = Form(None),
    article_pdf: UploadFile | None = File(None),
    search_screenshot: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    try:
        article_upload = store_optional_upload(article_pdf)
        screenshot_upload = store_optional_upload(search_screenshot)
    except ValueError as exc:
        return redirect_with_message("/literature-monitoring?tab=results", validation=str(exc))
    try:
        result = crud.create_literature_result(
            db,
            schemas.LiteratureResultCreate(
                search_log_id=search_log_id or None,
                plan_id=plan_id,
                product_ids=selected_ids(product_ids),
                partner_id=partner_id or None,
                active_substance_id=active_substance_id or None,
                publication_title=publication_title,
                authors=authors,
                journal_source=journal_source,
                publication_year=optional_int(publication_year),
                publication_date=optional_date(publication_date),
                doi=doi,
                url=url,
                abstract=abstract,
                relevance=relevance,
                result_type=result_type,
                pv_decision=pv_decision,
                processing_status=processing_status,
                specialist_comment=specialist_comment,
            ),
            created_by_user_id=current_user_id(request),
        )
        pdf_doc = create_literature_attachment(
            db,
            result,
            article_upload,
            document_kind="article_pdf",
            uploaded_by_user_id=current_user_id(request),
        )
        screenshot_doc = create_literature_attachment(
            db,
            result,
            screenshot_upload,
            document_kind="search_screenshot",
            uploaded_by_user_id=current_user_id(request),
        )
        if pdf_doc or screenshot_doc:
            result = crud.get_literature_result(db, result.id)
            crud.set_literature_result_documents(
                db,
                result,
                article_pdf_document_id=pdf_doc.id if pdf_doc else None,
                screenshot_document_id=screenshot_doc.id if screenshot_doc else None,
                changed_by_user_id=current_user_id(request),
            )
    except (IntegrityError, ValueError) as exc:
        db.rollback()
        return redirect_with_message(
            "/literature-monitoring?tab=results",
            error=f"Literature result could not be saved. {exc}",
        )
    return redirect_with_message(
        f"/literature-monitoring?tab=results#result-{result.id}",
        message="Literature result saved.",
    )


@router.post("/literature-monitoring/results/{result_id}", dependencies=[Depends(require_permission("edit"))])
def update_literature_result_form(
    result_id: str,
    request: Request,
    search_log_id: str | None = Form(None),
    plan_id: str = Form(...),
    product_ids: list[str] | None = Form(None),
    partner_id: str | None = Form(None),
    active_substance_id: str | None = Form(None),
    publication_title: str = Form(...),
    authors: str | None = Form(None),
    journal_source: str | None = Form(None),
    publication_year: str | None = Form(None),
    publication_date: str | None = Form(None),
    doi: str | None = Form(None),
    url: str | None = Form(None),
    abstract: str | None = Form(None),
    relevance: str = Form("needs_assessment"),
    result_type: str = Form("other"),
    pv_decision: str = Form("no_action"),
    processing_status: str = Form("new"),
    specialist_comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    result = crud.get_literature_result(db, result_id)
    if not result:
        return redirect_with_message("/literature-monitoring?tab=results", error="Result not found.")
    try:
        crud.update_literature_result(
            db,
            result,
            schemas.LiteratureResultCreate(
                search_log_id=search_log_id or None,
                plan_id=plan_id,
                product_ids=selected_ids(product_ids),
                partner_id=partner_id or None,
                active_substance_id=active_substance_id or None,
                publication_title=publication_title,
                authors=authors,
                journal_source=journal_source,
                publication_year=optional_int(publication_year),
                publication_date=optional_date(publication_date),
                doi=doi,
                url=url,
                abstract=abstract,
                relevance=relevance,
                result_type=result_type,
                pv_decision=pv_decision,
                processing_status=processing_status,
                specialist_comment=specialist_comment,
                article_pdf_document_id=result.article_pdf_document_id,
                screenshot_document_id=result.screenshot_document_id,
                linked_case_id=result.linked_case_id,
                linked_psur_plan_id=result.linked_psur_plan_id,
                linked_psmf_component_id=result.linked_psmf_component_id,
                rmp_reference=result.rmp_reference,
            ),
            changed_by_user_id=current_user_id(request),
        )
    except (IntegrityError, ValueError) as exc:
        db.rollback()
        return redirect_with_message(
            f"/literature-monitoring?tab=results#result-{result_id}",
            error=f"Literature result could not be saved. {exc}",
        )
    return redirect_with_message(
        f"/literature-monitoring?tab=results#result-{result_id}",
        message="Literature result saved.",
    )


@router.post(
    "/literature-monitoring/results/{result_id}/documents",
    dependencies=[Depends(require_any_permission("upload", "edit"))],
)
def upload_literature_result_documents_form(
    result_id: str,
    request: Request,
    article_pdf: UploadFile | None = File(None),
    search_screenshot: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    result = crud.get_literature_result(db, result_id)
    if not result:
        return redirect_with_message("/literature-monitoring?tab=results", error="Result not found.")
    try:
        article_upload = store_optional_upload(article_pdf)
        screenshot_upload = store_optional_upload(search_screenshot)
    except ValueError as exc:
        return redirect_with_message(
            f"/literature-monitoring?tab=results#result-{result_id}",
            validation=str(exc),
        )
    if not article_upload and not screenshot_upload:
        return redirect_with_message(
            f"/literature-monitoring?tab=results#result-{result_id}",
            validation="Select a file to upload.",
        )
    pdf_doc = create_literature_attachment(
        db,
        result,
        article_upload,
        document_kind="article_pdf",
        uploaded_by_user_id=current_user_id(request),
    )
    screenshot_doc = create_literature_attachment(
        db,
        result,
        screenshot_upload,
        document_kind="search_screenshot",
        uploaded_by_user_id=current_user_id(request),
    )
    result = crud.get_literature_result(db, result.id)
    crud.set_literature_result_documents(
        db,
        result,
        article_pdf_document_id=pdf_doc.id if pdf_doc else None,
        screenshot_document_id=screenshot_doc.id if screenshot_doc else None,
        changed_by_user_id=current_user_id(request),
    )
    return redirect_with_message(
        f"/literature-monitoring?tab=results#result-{result_id}",
        message="Document uploaded.",
    )


@router.post(
    "/literature-monitoring/results/{result_id}/create-icsr",
    dependencies=[Depends(require_any_permission("create", "icsr_workflow"))],
)
def create_case_from_literature_result_form(
    result_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    result = crud.get_literature_result(db, result_id)
    if not result:
        return redirect_with_message("/literature-monitoring?tab=results", error="Result not found.")
    case = crud.create_case_from_literature_result(
        db,
        result,
        created_by_user_id=current_user_id(request),
    )
    return redirect_with_message(
        f"/cases/{case.id}",
        message="ICSR draft created from literature publication.",
    )


@router.post(
    "/literature-monitoring/results/{result_id}/link",
    dependencies=[Depends(require_permission("edit"))],
)
def link_literature_result_form(
    result_id: str,
    request: Request,
    target: str = Form(...),
    target_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    result = crud.get_literature_result(db, result_id)
    if not result:
        return redirect_with_message("/literature-monitoring?tab=results", error="Result not found.")
    try:
        crud.link_literature_result_to_module(
            db,
            result,
            target=target,
            target_id=target_id,
            changed_by_user_id=current_user_id(request),
        )
    except ValueError as exc:
        return redirect_with_message(
            f"/literature-monitoring?tab=results#result-{result_id}",
            validation=str(exc),
        )
    return redirect_with_message(
        f"/literature-monitoring?tab=results#result-{result_id}",
        message="Literature result linked.",
    )


@router.post(
    "/literature-monitoring/results/{result_id}/close",
    dependencies=[Depends(require_any_permission("edit", "comment"))],
)
def close_literature_result_form(
    result_id: str,
    request: Request,
    close_comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    result = crud.get_literature_result(db, result_id)
    if not result:
        return redirect_with_message("/literature-monitoring?tab=results", error="Result not found.")
    crud.close_literature_result(
        db,
        result,
        changed_by_user_id=current_user_id(request),
        comment=close_comment,
    )
    return redirect_with_message(
        f"/literature-monitoring?tab=results#result-{result_id}",
        message="Literature result closed.",
    )


@router.get("/api/literature-monitoring/plans", response_model=list[schemas.LiteratureMonitoringPlanRead])
def api_list_literature_plans(db: Session = Depends(get_db)):
    return crud.list_literature_plans(db)


@router.get("/api/literature-monitoring/logs", response_model=list[schemas.LiteratureSearchLogRead])
def api_list_literature_logs(db: Session = Depends(get_db)):
    return crud.list_literature_search_logs(db)


@router.get("/api/literature-monitoring/results", response_model=list[schemas.LiteratureResultRead])
def api_list_literature_results(db: Session = Depends(get_db)):
    return crud.list_literature_results(db)


@router.get(
    "/api/literature-monitoring/results/export.csv",
    dependencies=[Depends(require_permission("export"))],
)
def api_export_literature_results(
    search: str | None = None,
    partner_id: str | None = None,
    product_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    result_type: str | None = None,
    processing_status: str | None = None,
    pv_decision: str | None = None,
    db: Session = Depends(get_db),
):
    results = filter_literature_results(
        crud.list_literature_results(db),
        search=search,
        partner_id=partner_id,
        product_id=product_id,
        date_from=date_from,
        date_to=date_to,
        result_type=result_type,
        processing_status=processing_status,
        pv_decision=pv_decision,
    )
    return Response(
        crud.export_literature_results_csv(results),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=literature_monitoring_results.csv"},
    )


@router.get("/api/literature-monitoring/results/{result_id}", response_model=schemas.LiteratureResultRead)
def api_get_literature_result(result_id: str, db: Session = Depends(get_db)):
    result = crud.get_literature_result(db, result_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
    return result
