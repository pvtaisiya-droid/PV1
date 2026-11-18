from app.concurrency import CASE_INPUTS
from app.concurrency import snapshot_token, protect_snapshot, ONBOARDING_INPUTS
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app import crud, schemas
from app.auth import require_any_permission, require_permission
from app.audit import log_audit
from app.database import get_db
from app.concurrency import require_version
from app.e2b_import import E2BImportService, E2BPackageService, batch_preview_context
from app.message_directions import ResolvedMessageDirection
from app.models import Case, CaseProduct, E2BImportBatch, E2BImportIssue, SafetyReport
from app.pagination import paginate_query
from app.config import get_settings
from app.security import require_csrf
from app.statuses import status_codes
from app.templating import templates
from app.ui_helpers import active_filters, redirect_with_message
from app.e2b_i18n import E2BDisplay
from app.i18n import translate
from app.unit_of_work import atomic_write
from app.duplicates import DuplicateMessageError, DuplicateContactError
from app import identity_review, models


router = APIRouter()
TRIAGE_STATUS_OPTIONS = status_codes("triage")


def current_user_id(request: Request) -> str | None:
    current_user = getattr(request.state, "current_user", None)
    return getattr(current_user, "id", None)


def result_product_names(report: SafetyReport) -> list[str]:
    names = list(report.product_names)
    if report.case and not report.case.is_deleted:
        for case_product in report.case.case_products:
            if case_product.is_deleted:
                continue
            name = case_product.reported_product_name or (
                case_product.product.product_name if case_product.product else None
            )
            if name and name not in names:
                names.append(name)
    return names


@router.get("/safety-reports", response_class=HTMLResponse)
def safety_reports_page(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    partner_id: str | None = None,
    product_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    per_page: int | None = None,
    db: Session = Depends(get_db),
):
    total_count = crud.safety_reports_query(db).order_by(None).count()
    page_reports, pagination = paginate_query(crud.safety_reports_query(
        db, search=search, status_filter=status_filter, partner_id=partner_id,
        product_id=product_id, date_from=date_from, date_to=date_to,
    ).options(
        selectinload(SafetyReport.case).selectinload(Case.case_products).joinedload(CaseProduct.product),
        selectinload(SafetyReport.processing_routes),
    ), page, per_page)
    filters = {
        "search": search or "",
        "status_filter": status_filter or "",
        "partner_id": partner_id or "",
        "product_id": product_id or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "active": active_filters(
            search=search,
            status_filter=status_filter,
            partner_id=partner_id,
            product_id=product_id,
            date_from=date_from,
            date_to=date_to,
        ),
    }
    return templates.TemplateResponse(
        request,
        "safety_reports.html",
        {
            "request": request,
            "reports": page_reports,
            "report_product_names": {report.id: result_product_names(report) for report in page_reports},
            "partners": crud.list_partners(db),
            "products": crud.list_products(db),
            "status_options": TRIAGE_STATUS_OPTIONS,
            "filters": filters,
            "total_count": total_count,
            "filtered_count": pagination.total,
            "pagination": pagination,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "input_snapshot": snapshot_token(db, ONBOARDING_INPUTS),
            "active_page": "safety_reports",
        },
    )


@router.post("/safety-reports", dependencies=[Depends(require_permission("create"))])
def create_safety_report_form(
    source_type: str | None = Form("email"),
    message_direction: ResolvedMessageDirection = Form(...),
    partner_id: str | None = Form(None),
    product_ids: list[str] | None = Form(None),
    reporter_name: str | None = Form(None),
    reporter_email: str | None = Form(None),
    reporter_country_code: str | None = Form(None),
    raw_subject: str | None = Form(None),
    raw_text: str | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        crud.create_safety_report(
            db,
            schemas.SafetyReportCreate(
                source_type=source_type,
                message_direction=message_direction,
                partner_id=partner_id,
                product_ids=product_ids or [],
                reporter_name=reporter_name,
                reporter_email=reporter_email,
                reporter_country_code=reporter_country_code,
                raw_subject=raw_subject,
                raw_text=raw_text,
            ),
        )
    except ValueError as exc:
        return redirect_with_message("/safety-reports", validation=str(exc))
    return redirect_with_message("/safety-reports", message="Safety report saved.")


@router.post(
    "/safety-reports/{report_id}/direction",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def update_safety_report_direction_form(
    report_id: str,
    expected_version: int = Form(..., gt=0),
    message_direction: ResolvedMessageDirection = Form(...),
    db: Session = Depends(get_db),
):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    require_version(report, expected_version)
    crud.update_safety_report_direction(
        db,
        report,
        schemas.MessageDirectionUpdate(message_direction=message_direction),
    )
    return redirect_with_message(
        f"/safety-reports/{report_id}",
        message="Message recipient saved.",
    )


@router.post(
    "/safety-reports/e2b-imports",
    dependencies=[Depends(require_any_permission("create", "icsr_workflow"))],
)
def upload_e2b_source_form(
    request: Request,
    file: UploadFile = File(...),
    origin: str = Form(""),
    db: Session = Depends(get_db),
):
    # A synchronous route runs parsing and database work in the worker pool.
    content = file.file.read(get_settings().max_upload_bytes + 1)
    if len(content) > get_settings().max_upload_bytes:
        raise HTTPException(413, translate("E2B source file exceeds configured upload size limit.", request.state.lang))
    try:
        batch = E2BPackageService().prepare_batch(
            db,
            filename=file.filename or "e2b-message.xml",
            content=content,
            uploaded_by_user_id=current_user_id(request),
        )
    except ValueError as exc:
        target = "/safety-reports/new?method=e2b" if origin == "registration" else "/safety-reports"
        return redirect_with_message(target, validation=str(exc))
    return redirect_with_message(
        f"/safety-reports/e2b-imports/{batch.id}",
        message="E2B source uploaded. Review before import.",
    )


@router.get("/safety-reports/e2b-imports/{batch_id}", response_class=HTMLResponse)
def e2b_import_preview_page(
    batch_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    batch = (
        db.query(E2BImportBatch)
        .options(
            selectinload(E2BImportBatch.items),
            selectinload(E2BImportBatch.issues).joinedload(E2BImportIssue.item),
            joinedload(E2BImportBatch.duplicate_batch),
            joinedload(E2BImportBatch.created_case),
            joinedload(E2BImportBatch.created_safety_report),
            joinedload(E2BImportBatch.created_incoming_request),
        )
        .filter(E2BImportBatch.id == batch_id, E2BImportBatch.is_deleted.is_(False))
        .first()
    )
    if not batch:
        raise HTTPException(status_code=404, detail=translate("E2B import batch not found", request.state.lang))
    preview = batch_preview_context(batch)
    preview["case_groups"] = E2BImportService().preview_groups(db, batch)
    from app.e2b_review import enrich_groups
    enrich_groups(db, batch, preview)
    matching_error = None
    try:
        related_case = E2BImportService().preview_case(db, batch)
    except ValueError as exc:
        related_case = None
        matching_error = str(exc)
    return templates.TemplateResponse(
        request,
        "e2b_import_preview.html",
        {
            "request": request,
            "batch": batch,
            "preview": preview,
            "group_tokens": {group["token"]: group["expected_case_token"] for group in preview["case_groups"]},
            "r3_messages": db.query(models.IcsrMessage).filter_by(batch_id=batch.id, is_deleted=False)
                .order_by(models.IcsrMessage.item_id, models.IcsrMessage.ordinal).all(),
            "e2b": E2BDisplay(request.state.lang),
            "case_input_snapshot": snapshot_token(db, CASE_INPUTS),
            "expected_case_token": E2BImportService.case_token(related_case),
            "matching_error": matching_error,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "input_snapshot": snapshot_token(db, ONBOARDING_INPUTS),
            "active_page": "safety_reports",
        },
    )


@router.get("/safety-reports/e2b-imports/{batch_id}/identity-review", response_class=HTMLResponse,
            dependencies=[Depends(require_permission("icsr_workflow"))])
def identity_review_page(batch_id: str, request: Request, db: Session = Depends(get_db)):
    batch = db.get(E2BImportBatch, batch_id)
    if not batch or batch.is_deleted:
        raise HTTPException(404, "Import batch not found")
    return templates.TemplateResponse(request, "identity_review.html", dict(
        request=request, batch=batch, review=identity_review.review_context(db, batch),
        input_snapshot=snapshot_token(db, identity_review.INPUTS),
        validation=request.query_params.get("validation"), active_page="safety_reports"))


@router.get("/safety-reports/e2b-imports/{batch_id}/identity-review/sources/{transmission_id}",
            dependencies=[Depends(require_permission("icsr_workflow"))])
def identity_review_source(batch_id: str, transmission_id: str, db: Session = Depends(get_db)):
    batch = db.get(E2BImportBatch, batch_id)
    if not batch or batch.is_deleted:
        raise HTTPException(404, "Import batch not found")
    context = identity_review.review_context(db, batch)
    tx = db.get(models.E2BTransmission, transmission_id)
    if not tx or tx.case_id not in {c["case"].id for c in context["candidates"]}:
        raise HTTPException(404, "Related source not found")
    try:
        path, _, _, _ = identity_review.evidence(tx, tx.case_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return FileResponse(path, media_type="application/xml", filename="source.xml",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/safety-reports/e2b-imports/{batch_id}/identity-review",
             dependencies=[Depends(require_permission("icsr_workflow")), Depends(require_csrf)])
@atomic_write
def identity_review_save(batch_id: str, request: Request, case_id: str = Form(...),
        transmission_id: str = Form(...), reason: str = Form(..., max_length=4000),
        expected_case_version: int = Form(..., gt=0), expected_batch_version: int = Form(..., gt=0),
        expected_snapshot: str = Form(...), supersedes_id: str = Form(""),
        db: Session = Depends(get_db)):
    try:
        _, message = identity_review.save_decision(db, batch_id=batch_id, case_id=case_id,
            transmission_id=transmission_id, reason=reason, author_id=current_user_id(request),
            expected_case_version=expected_case_version, expected_batch_version=expected_batch_version,
            expected_snapshot=expected_snapshot, supersedes_id=supersedes_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}/identity-review", validation=str(exc))
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Решение конфликтует с сохранёнными данными. Откройте форму заново.") from None
    return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", message=message)


@router.post(
    "/safety-reports/e2b-imports/{batch_id}/recheck",
    dependencies=[Depends(require_any_permission("create", "icsr_workflow")), Depends(require_csrf)],
)
def recheck_e2b_import(
    batch_id: str, request: Request,
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    batch = db.query(E2BImportBatch).filter(
        E2BImportBatch.id == batch_id, E2BImportBatch.is_deleted.is_(False)
    ).first()
    if not batch:
        raise HTTPException(404, translate("E2B import batch not found", request.state.lang))
    require_version(batch, expected_version)
    try:
        E2BPackageService().recheck_batch(db, batch, checked_by_user_id=current_user_id(request))
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", validation=str(exc))
    return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", message="E2B package re-checked.")


@router.post(
    "/safety-reports/e2b-imports/{batch_id}/confirm",
    dependencies=[Depends(require_any_permission("create", "icsr_workflow")), Depends(require_csrf)],
)
def confirm_e2b_import_form(
    batch_id: str,
    request: Request,
    expected_version: int = Form(..., gt=0),
    expected_case_token: str = Form(...),
    expected_inputs: str = Form(..., min_length=64, max_length=64),
    case_group: str | None = Query(None, pattern="^[0-9a-f]{64}$"),
    document_ids: list[str] | None = Form(None),
    documents_explicit: bool = Form(False),
    db: Session = Depends(get_db),
):
    batch = (
        db.query(E2BImportBatch)
        .options(
            selectinload(E2BImportBatch.items),
            selectinload(E2BImportBatch.issues),
            joinedload(E2BImportBatch.created_case),
        )
        .filter(E2BImportBatch.id == batch_id, E2BImportBatch.is_deleted.is_(False))
        .first()
    )
    if not batch:
        raise HTTPException(status_code=404, detail=translate("E2B import batch not found", request.state.lang))
    require_version(batch, expected_version)
    if batch.status != "imported":
        protect_snapshot(db, CASE_INPUTS, expected_inputs)
    try:
        summary = E2BImportService().confirm_batch(
            db,
            batch,
            confirmed_by_user_id=current_user_id(request),
            expected_case_token=expected_case_token,
            case_group=case_group,
            document_ids=(document_ids or []) if documents_explicit else None,
        )
    except StaleDataError:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", validation=str(exc))
    except Exception as exc:
        db.rollback()
        return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", error=f"Import failed: {exc}")
    if batch.detected_case_count > 1:
        return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", message="Selected case imported. Other cases remain separate.")
    return redirect_with_message(
        f"/safety-reports/{summary['safety_report_id']}/intake?step=pv",
        message="E2B import completed. Add other processing directions if needed.",
    )


@router.post("/safety-reports/e2b-imports/{batch_id}/confirm-selection",
             dependencies=[Depends(require_any_permission("create", "icsr_workflow")), Depends(require_csrf)])
@atomic_write
def confirm_selected_package_cases(batch_id: str, request: Request,
        expected_version: int = Form(..., gt=0), expected_inputs: str = Form(..., min_length=64, max_length=64),
        selected_groups: list[str] = Form([]), case_tokens: str = Form(...), case_documents: str = Form("{}"),
        db: Session = Depends(get_db)):
    import json
    batch = db.query(E2BImportBatch).filter_by(id=batch_id, is_deleted=False).first()
    if not batch:
        raise HTTPException(404, translate("E2B import batch not found", request.state.lang))
    require_version(batch, expected_version)
    protect_snapshot(db, CASE_INPUTS, expected_inputs)
    try:
        tokens = json.loads(case_tokens)
        documents = json.loads(case_documents)
        selected = list(dict.fromkeys(selected_groups))
        groups = {group["token"]: group for group in E2BImportService().preview_groups(db, batch)}
        if not isinstance(tokens, dict) or not selected or any(token not in groups or not groups[token]["can_import"] for token in selected):
            raise ValueError("This case requires review before import.")
        if not isinstance(documents, dict) or any(token not in groups or not isinstance(ids, list)
                or any(not isinstance(identifier, str) for identifier in ids) for token, ids in documents.items()):
            raise ValueError("Selected documents do not belong to this case group.")
        for token in selected:
            E2BImportService().confirm_batch(db, batch, confirmed_by_user_id=current_user_id(request),
                expected_case_token=tokens.get(token, ""), case_group=token, document_ids=documents.get(token))
        db.commit()
    except (ValueError, TypeError) as exc:
        db.rollback()
        return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", validation=str(exc))
    return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", message="Selected case imported. Other cases remain separate.")


@router.post(
    "/safety-reports/e2b-imports/{batch_id}/resolve",
    dependencies=[Depends(require_any_permission("create", "icsr_workflow")), Depends(require_csrf)],
)
def resolve_e2b_case_sources(
    batch_id: str, request: Request,
    case_group: str = Query(..., pattern="^[0-9a-f]{64}$"),
    expected_version: int = Form(..., gt=0),
    choice_paths: list[str] = Form([]),
    choice_sources: list[str] = Form([]),
    reason: str = Form(..., min_length=1, max_length=2000),
    db: Session = Depends(get_db),
):
    batch = db.query(E2BImportBatch).filter(E2BImportBatch.id == batch_id, E2BImportBatch.is_deleted.is_(False)).first()
    if not batch:
        raise HTTPException(404, translate("E2B import batch not found", request.state.lang))
    require_version(batch, expected_version)
    try:
        if len(choice_paths) != len(choice_sources) or len(set(choice_paths)) != len(choice_paths):
            raise ValueError("Invalid source choices")
        choices = {path: value for path, value in zip(choice_paths, choice_sources) if value}
        E2BImportService().resolve_group(db, batch, case_group, choices, reason, current_user_id(request))
    except (ValueError, TypeError):
        db.rollback()
        return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", validation="Select valid source values and provide a reason.")
    return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", message="Case source decisions saved. Review the assembled case before import.")


@router.post("/safety-reports/e2b-imports/{batch_id}/resolve-products",
             dependencies=[Depends(require_any_permission("create", "icsr_workflow")), Depends(require_csrf)])
def resolve_e2b_products(batch_id: str, request: Request,
        case_group: str = Query(..., pattern="^[0-9a-f]{64}$"),
        expected_version: int = Form(..., gt=0), expected_inputs: str = Form(..., min_length=64, max_length=64),
        product_sources: list[str] = Form([]), product_choices: list[str] = Form([]),
        reason: str = Form(..., min_length=1, max_length=2000), db: Session = Depends(get_db)):
    batch = db.query(E2BImportBatch).filter_by(id=batch_id, is_deleted=False).first()
    if not batch:
        raise HTTPException(404, translate("E2B import batch not found", request.state.lang))
    require_version(batch, expected_version)
    protect_snapshot(db, CASE_INPUTS, expected_inputs)
    try:
        if len(product_sources) != len(product_choices) or len(set(product_sources)) != len(product_sources):
            raise ValueError("Select a product candidate and provide a reason.")
        E2BImportService().resolve_products(db, batch, case_group, dict(zip(product_sources, product_choices)),
                                          reason, current_user_id(request))
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}?case={case_group}&page=overview", validation=str(exc))
    return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}?case={case_group}&page=overview", message="Product matching saved. Review the case before import.")


@router.post(
    "/safety-reports/e2b-imports/{batch_id}/mark-not-pv-ars",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def mark_e2b_import_not_pv_ars_form(
    batch_id: str,
    request: Request,
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    batch = (
        db.query(E2BImportBatch)
        .options(
            selectinload(E2BImportBatch.items),
            selectinload(E2BImportBatch.issues),
            joinedload(E2BImportBatch.created_incoming_request),
        )
        .filter(E2BImportBatch.id == batch_id, E2BImportBatch.is_deleted.is_(False))
        .first()
    )
    if not batch:
        raise HTTPException(status_code=404, detail=translate("E2B import batch not found", request.state.lang))
    require_version(batch, expected_version)
    try:
        E2BImportService().mark_not_pv_ars(
            db,
            batch,
            marked_by_user_id=current_user_id(request),
        )
    except StaleDataError:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", validation=str(exc))
    except Exception as exc:
        db.rollback()
        return redirect_with_message(f"/safety-reports/e2b-imports/{batch_id}", error=f"Marking failed: {exc}")
    return redirect_with_message(
        f"/safety-reports/e2b-imports/{batch_id}",
        message="Package marked as not PV system ARS LLC / не ФН АРС.",
    )


@router.post(
    "/safety-reports/e2b-imports/{batch_id}/cancel",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def cancel_e2b_import_form(
    batch_id: str, request: Request,
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    batch = db.query(E2BImportBatch).filter(
        E2BImportBatch.id == batch_id, E2BImportBatch.is_deleted.is_(False)
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail=translate("E2B import batch not found", request.state.lang))
    require_version(batch, expected_version)
    if batch.status in {"imported", "not_pv_ars", "partially_imported"}:
        return redirect_with_message(
            f"/safety-reports/e2b-imports/{batch_id}",
            validation="Finalized batch cannot be cancelled from preview. Use reversal workflow.",
        )
    if batch.status != "cancelled":
        old_status = batch.status
        batch.status = "cancelled"
        log_audit(db, entity_type="E2BImportBatch", entity_id=batch.id,
                  action="cancel", field_name="status", old_value=old_status,
                  new_value="cancelled", user_id=current_user_id(request),
                  source_module="E2B Import")
        db.commit()
    return redirect_with_message("/safety-reports", message="E2B import cancelled.")


@router.get("/safety-reports/{report_id}", response_class=HTMLResponse)
def safety_report_detail(report_id: str, request: Request, db: Session = Depends(get_db)):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    immutable_documents = [
        document
        for document in crud.list_attachments(db)
        if document.safety_report_id == report.id
        or (
            document.related_object_type == "SafetySource"
            and document.related_object_id == report.id
        )
    ]
    return templates.TemplateResponse(
        request,
        "safety_report_detail.html",
        {
            "request": request,
            "report": report,
            "products": crud.list_products(db),
            "immutable_documents": immutable_documents,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "input_snapshot": snapshot_token(db, ONBOARDING_INPUTS),
            "active_page": "safety_reports",
        },
    )


@router.post(
    "/safety-reports/{report_id}/products",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def update_safety_report_products_form(
    report_id: str,
    expected_version: int = Form(..., gt=0),
    product_ids: list[str] | None = Form(None),
    db: Session = Depends(get_db),
):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    require_version(report, expected_version)
    try:
        crud.update_safety_report_products(db, report, product_ids or [])
    except ValueError as exc:
        return redirect_with_message(
            f"/safety-reports/{report_id}",
            validation=str(exc),
        )
    return redirect_with_message(
        f"/safety-reports/{report_id}",
        message="Safety report saved.",
    )


@router.post(
    "/safety-reports/{report_id}/triage",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def triage_safety_report_form(
    report_id: str,
    expected_version: int = Form(..., gt=0),
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
    require_version(report, expected_version)
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
def create_case_from_report_form(
    report_id: str,
    expected_version: int = Form(..., gt=0),
    expected_inputs: str = Form(..., min_length=64, max_length=64),
    db: Session = Depends(get_db),
):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    require_version(report, expected_version)
    protect_snapshot(db, ONBOARDING_INPUTS, expected_inputs)
    case = crud.create_case_from_report(db, report)
    return redirect_with_message(f"/cases/{case.id}", message="Case saved.")


@router.get("/api/safety-reports", response_model=list[schemas.SafetyReportRead])
def api_list_safety_reports(page: int = 1, per_page: int | None = None, db: Session = Depends(get_db)):
    rows, _ = paginate_query(crud.safety_reports_query(db), page, per_page)
    return rows


@router.post(
    "/api/safety-reports",
    response_model=schemas.SafetyReportRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("create"))],
)
@atomic_write
def api_create_safety_report(payload: schemas.SafetyReportCreate, db: Session = Depends(get_db)):
    try:
        return crud.create_safety_report(db, payload)
    except (DuplicateContactError, DuplicateMessageError) as exc:
        db.rollback()
        key = "contact" if isinstance(exc, DuplicateContactError) else "report"
        raise HTTPException(409, detail={"message": str(exc), "candidates": [item[key].id for item in exc.candidates]}) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, detail=str(exc)) from exc


@router.get("/api/safety-reports/{report_id}", response_model=schemas.SafetyReportRead)
def api_get_safety_report(report_id: str, db: Session = Depends(get_db)):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    report.input_snapshot = snapshot_token(db, ONBOARDING_INPUTS)
    return report


@router.put(
    "/api/safety-reports/{report_id}/products",
    response_model=schemas.SafetyReportRead,
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def api_update_safety_report_products(
    report_id: str,
    payload: schemas.SafetyReportProductSelectionUpdate,
    db: Session = Depends(get_db),
):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    require_version(report, payload.expected_version)
    try:
        return crud.update_safety_report_products(db, report, payload.product_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch(
    "/api/safety-reports/{report_id}/triage",
    response_model=schemas.SafetyReportRead,
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def api_triage_safety_report(
    report_id: str,
    payload: schemas.SafetyReportTriageUpdate,
    db: Session = Depends(get_db),
):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    require_version(report, payload.expected_version)
    return crud.triage_safety_report(db, report, payload)


@router.post(
    "/api/safety-reports/{report_id}/create-case",
    response_model=schemas.CaseRead,
    dependencies=[Depends(require_any_permission("create", "icsr_workflow"))],
)
def api_create_case_from_report(
    report_id: str,
    expected_version: int = Query(..., gt=0),
    expected_inputs: str = Query(..., min_length=64, max_length=64),
    db: Session = Depends(get_db),
):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Safety report not found")
    require_version(report, expected_version)
    protect_snapshot(db, ONBOARDING_INPUTS, expected_inputs)
    return crud.create_case_from_report(db, report)
