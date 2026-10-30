from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.exc import OperationalError

from app import crud, outlook_service, schemas
from app.auth import SESSION_COOKIE, require_any_permission, require_permission
from app.database import get_db
from app.concurrency import require_version
from app.security import require_csrf
from app.reconciliation import (
    AmbiguousCurrentRegistrationError,
    RECONCILIATION_STATUSES,
    build_reconciliation_preview,
    reconciliation_preview_version,
    contact_email_list,
    contact_full_name,
    email_list_text,
    products_for_partner,
    reconciliation_recipients,
    render_reconciliation_email,
)
from app.reconciliation_documents import generate_reconciliation_document_file
from app.reconciliation_excel import build_reconciliation_workbook
from app.routers.placeholders import store_document_file
from app.templating import templates
from app.ui_helpers import redirect_with_message


router = APIRouter()
RECONCILIATION_RECORD_STATUSES = [
    "draft",
    "generated",
    "outlook_draft_created",
    "sent",
    "confirmed",
    "discrepancy",
    "closed",
    "error",
]
OUTLOOK_NOT_CREATED = "not_created"
RECONCILIATION_FREQUENCIES = {"monthly", "quarterly"}


def month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    next_month = add_months(start, 1)
    return start, next_month - timedelta(days=1)


def previous_month_bounds(day: date) -> tuple[date, date]:
    current_month_start = day.replace(day=1)
    previous_month_end = current_month_start - timedelta(days=1)
    return month_bounds(previous_month_end)


def previous_quarter_bounds(day: date) -> tuple[date, date]:
    current_quarter_month = ((day.month - 1) // 3) * 3 + 1
    current_quarter_start = date(day.year, current_quarter_month, 1)
    previous_quarter_end = current_quarter_start - timedelta(days=1)
    previous_quarter_month = ((previous_quarter_end.month - 1) // 3) * 3 + 1
    previous_quarter_start = date(previous_quarter_end.year, previous_quarter_month, 1)
    return previous_quarter_start, previous_quarter_end


def add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, day.day)


def reconciliation_period_presets(today: date | None = None) -> list[dict[str, str | date]]:
    today = today or date.today()
    presets = [
        ("current_month", "Current month", month_bounds(today)),
        ("previous_month", "Previous month", previous_month_bounds(today)),
        ("previous_quarter", "Previous quarter", previous_quarter_bounds(today)),
    ]
    return [
        {
            "key": key,
            "label": label,
            "period_start": period_start,
            "period_end": period_end,
        }
        for key, label, (period_start, period_end) in presets
    ]


@router.get("/outlook/auth", dependencies=[Depends(require_any_permission("edit", "export"))])
def outlook_auth(
    request: Request,
    reconciliation_id: str | None = None,
):
    try:
        auth_url = outlook_service.get_auth_url(
            user_key=outlook_user_key(request), session_token=request.cookies.get(SESSION_COOKIE),
            reconciliation_id=reconciliation_id,
        )
    except (outlook_service.OutlookConfigurationError, outlook_service.OutlookAuthorizationError) as exc:
        target = (
            f"/partner-reconciliation?reconciliation_id={reconciliation_id}"
            if reconciliation_id
            else "/partner-reconciliation"
        )
        return redirect_with_message(target, validation=str(exc))
    return RedirectResponse(auth_url)


@router.get("/outlook/callback", dependencies=[Depends(require_any_permission("edit", "export"))])
def outlook_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    target = "/partner-reconciliation"
    try:
        result = outlook_service.handle_callback(
            code=code,
            state=state,
            user_key=outlook_user_key(request),
            session_token=request.cookies.get(SESSION_COOKIE),
            error=error_description or error,
        )
    except (outlook_service.OutlookConfigurationError, outlook_service.OutlookAuthorizationError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if result.get("reconciliation_id"):
        from urllib.parse import urlencode
        target += "?" + urlencode({"reconciliation_id": result["reconciliation_id"]})
    return redirect_with_message(target, message="Microsoft Outlook authorization saved.")


@router.get("/partner-reconciliation", response_class=HTMLResponse)
def partner_reconciliation_page(
    request: Request,
    reconciliation_id: str | None = None,
    partner_id: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    contact_id: str | None = None,
    language: str = "ru",
    status_filter: str | None = None,
    product_id: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    default_period_start, default_period_end = previous_month_bounds(date.today())
    context = base_context(request, db)
    prepared_by = current_user_display_name(request)
    registration_error = None
    try:
        partner_products = products_for_partner(db, partner_id) if partner_id else []
    except AmbiguousCurrentRegistrationError as exc:
        partner_products = []
        registration_error = str(exc)
    context.update(
        {
            "status_options": RECONCILIATION_STATUSES,
            "record_status_options": RECONCILIATION_RECORD_STATUSES,
            "status_filter": status_filter or "",
            "product_filter": product_id or "",
            "search": search or "",
            "language": language,
            "selected_partner_id": partner_id or "",
            "selected_contact_id": "",
            "period_start": period_start or default_period_start,
            "period_end": period_end or default_period_end,
            "prepared_by": prepared_by,
            "products": partner_products,
            "period_presets": reconciliation_period_presets(),
            "reconciliation": None,
            "preview": None,
            "our_items": [],
            "partner_items": [],
            "direction_review_items": [],
            "discrepancy_items": [],
            "summary": {},
            "outlook_preview": None,
            "registration_error": registration_error,
        }
    )

    if reconciliation_id:
        reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail="Reconciliation not found")
        all_items = filter_items(
            list(reconciliation.items),
            status_filter=status_filter,
            product_id=product_id,
            search=search,
        )
        try:
            reconciliation_products = products_for_partner(db, reconciliation.partner_id)
        except AmbiguousCurrentRegistrationError as exc:
            reconciliation_products = []
            context["registration_error"] = str(exc)
        context.update(
            {
                "reconciliation": reconciliation,
                "selected_partner_id": reconciliation.partner_id,
                "selected_contact_id": reconciliation.contact_id or "",
                "period_start": reconciliation.period_start,
                "period_end": reconciliation.period_end,
                "prepared_by": reconciliation.prepared_by or prepared_by,
                "language": reconciliation.language,
                "products": reconciliation_products,
                "our_items": [item for item in all_items if item.source_side == "our_company"],
                "partner_items": [item for item in all_items if item.source_side == "partner"],
                "direction_review_items": [
                    item for item in all_items if item.source_side == "needs_review"
                ],
                "discrepancy_items": [
                    item
                    for item in all_items
                    if item.reconciliation_status not in {"matched", "confirmed"}
                ],
                "summary": {
                    "our_case_count": reconciliation.our_case_count,
                    "partner_case_count": reconciliation.partner_case_count,
                    "matched_count": reconciliation.matched_count,
                    "discrepancy_count": reconciliation.discrepancy_count,
                },
                "outlook_preview": build_outlook_preview(db, reconciliation, request),
            }
        )
    elif partner_id and period_start and period_end and not registration_error:
        try:
            preview = build_reconciliation_preview(
                db,
                partner_id=partner_id,
                period_start=period_start,
                period_end=period_end,
                contact_id=None,
                language=language,
            )
        except AmbiguousCurrentRegistrationError as exc:
            context["registration_error"] = str(exc)
            preview = None
        if preview is None:
            context.update(
                reconciliation_control_context(
                    partners=context["partners"],
                    contacts=context["contacts"],
                    reconciliations=context["reconciliations"],
                    selected_partner_id=context["selected_partner_id"],
                    period_start=context["period_start"],
                    period_end=context["period_end"],
                    current_reconciliation_id=None,
                )
            )
            return templates.TemplateResponse(request, "partner_reconciliation.html", context)
        context["preview_version"] = reconciliation_preview_version(preview, period_start, period_end)
        preview["items"] = filter_items(
            preview["items"],
            status_filter=status_filter,
            product_id=product_id,
            search=search,
        )
        context.update(
            {
                "preview": preview,
                "selected_contact_id": preview["contact"].id if preview["contact"] else "",
                "products": preview["products"],
                "our_items": [item for item in preview["items"] if item["source_side"] == "our_company"],
                "partner_items": [item for item in preview["items"] if item["source_side"] == "partner"],
                "direction_review_items": [
                    item for item in preview["items"] if item["source_side"] == "needs_review"
                ],
                "discrepancy_items": [
                    item
                    for item in preview["items"]
                    if item["reconciliation_status"] not in {"matched", "confirmed"}
                ],
                "summary": preview["summary"],
            }
        )

    context.update(
        reconciliation_control_context(
            partners=context["partners"],
            contacts=context["contacts"],
            reconciliations=context["reconciliations"],
            selected_partner_id=context["selected_partner_id"],
            period_start=context["period_start"],
            period_end=context["period_end"],
            current_reconciliation_id=(
                context["reconciliation"].id if context["reconciliation"] else None
            ),
        )
    )

    return templates.TemplateResponse(request, "partner_reconciliation.html", context)


@router.post("/partner-reconciliation/save", dependencies=[Depends(require_permission("create"))])
def save_partner_reconciliation(
    request: Request,
    partner_id: str = Form(...),
    period_start: date = Form(...),
    period_end: date = Form(...),
    expected_preview_version: str = Form(..., pattern="^[0-9a-f]{64}$"),
    contact_id: str | None = Form(None),
    language: str = Form("ru"),
    reconciliation_type: str = Form("periodic"),
    document_format: str = Form("pdf"),
    prepared_by: str | None = Form(None),
    products: str | None = Form(None),
    comments: str | None = Form(None),
    db: Session = Depends(get_db),
):
    # Reserve SQLite's writer before reading inputs; another create or source
    # edit cannot commit between the freshness check and the final commit.
    if db.get_bind().dialect.name == "sqlite":
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        except OperationalError as exc:
            db.rollback()
            if "locked" in str(exc.orig).lower():
                raise StaleDataError("Reload before saving.") from exc
            raise
    current_user = getattr(request.state, "current_user", None)
    partner = crud.get_partner(db, partner_id)
    if not partner or partner.reconciliation_frequency not in RECONCILIATION_FREQUENCIES:
        return redirect_with_message(
            "/partner-reconciliation",
            validation="Для партнёра не настроена ежемесячная или ежеквартальная сверка.",
        )
    try:
        preview = build_reconciliation_preview(
            db,
            partner_id=partner_id,
            period_start=period_start,
            period_end=period_end,
            contact_id=None,
            language=language,
        )
    except AmbiguousCurrentRegistrationError as exc:
        target = (
            f"/partner-reconciliation?partner_id={partner_id}"
            f"&period_start={period_start}&period_end={period_end}&language={language}"
        )
        return redirect_with_message(target, error=str(exc))
    if expected_preview_version != reconciliation_preview_version(preview, period_start, period_end):
        raise StaleDataError("Preview has changed. Reload before saving.")
    if preview["direction_review_items"]:
        target = (
            f"/partner-reconciliation?partner_id={partner_id}"
            f"&period_start={period_start}&period_end={period_end}&language={language}"
        )
        return redirect_with_message(
            target,
            validation=(
                "Нельзя сформировать сверку: у части сообщений не уточнено направление. "
                "Укажите «Партнёр → наша компания» или «Наша компания → партнёр»."
            ),
        )
    from app.models import PartnerReconciliation
    existing = db.query(PartnerReconciliation).filter(
        PartnerReconciliation.partner_id == partner_id,
        PartnerReconciliation.period_start == period_start,
        PartnerReconciliation.period_end == period_end,
        PartnerReconciliation.is_deleted.is_(False),
    ).first()
    if existing:
        raise StaleDataError("A reconciliation already exists for this period.")
    created_paths = []
    try:
        resolved_contact_id = preview["contact"].id if preview["contact"] else None
        prepared_by_name = current_user_display_name(request) or prepared_by
        reconciliation = crud.create_partner_reconciliation(
            db,
            schemas.PartnerReconciliationCreate(
                partner_id=partner_id,
                contact_id=resolved_contact_id,
                period_start=period_start,
                period_end=period_end,
                reconciliation_type=reconciliation_type,
                language=language,
                prepared_by=prepared_by_name,
                products=product_names(preview["products"]),
                document_format=document_format,
                comments=comments,
            ),
            preview["items"],
            commit=False,
        )
        document_info = generate_reconciliation_document_file(
            reconciliation,
            document_format=document_format,
            products=preview["products"],
            created_paths=created_paths,
        )
        crud.save_partner_reconciliation_document(
            db,
            reconciliation,
            document_info,
            changed_by_user_id=current_user.id if current_user else None,
            commit=False,
        )
        reconciliation_id = reconciliation.id
        db.commit()
    except Exception as exc:
        db.rollback()
        for path in created_paths:
            path.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            return redirect_with_message(
                f"/partner-reconciliation?partner_id={partner_id}"
                f"&period_start={period_start}&period_end={period_end}&language={language}",
                validation=str(exc),
            )
        raise
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Письмо партнёру и форма сверки подготовлены.",
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/confirm",
    dependencies=[Depends(require_permission("approve"))],
)
def confirm_partner_reconciliation(
    reconciliation_id: str,
    expected_version: int = Form(..., ge=1),
    confirmed_by_user: str | None = Form(None),
    db: Session = Depends(get_db),
):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    require_version(reconciliation, expected_version)
    crud.confirm_partner_reconciliation(db, reconciliation, confirmed_by_user)
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Reconciliation confirmed.",
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/status",
    dependencies=[Depends(require_any_permission("edit", "comment"))],
)
def update_partner_reconciliation_status_form(
    reconciliation_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    reconciliation_status: str = Form(...),
    sent_date: str | None = Form(None),
    response_date: str | None = Form(None),
    products: str | None = Form(None),
    discrepancy_description: str | None = Form(None),
    comments: str | None = Form(None),
    db: Session = Depends(get_db),
):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    require_version(reconciliation, expected_version)
    if reconciliation_status not in RECONCILIATION_RECORD_STATUSES:
        return redirect_with_message(
            f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
            validation="Invalid reconciliation status.",
        )
    current_user = getattr(request.state, "current_user", None)
    crud.update_partner_reconciliation_status(
        db,
        reconciliation,
        reconciliation_status=reconciliation_status,
        sent_date=optional_date(sent_date),
        response_date=optional_date(response_date),
        products=products,
        discrepancy_description=discrepancy_description,
        comments=comments,
        changed_by_user_id=current_user.id if current_user else None,
    )
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Reconciliation status saved.",
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/generate-document",
    dependencies=[Depends(require_any_permission("edit", "export"))],
)
def generate_partner_reconciliation_document(
    reconciliation_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    document_format: str = Form("pdf"),
    db: Session = Depends(get_db),
):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    require_version(reconciliation, expected_version)
    created_paths = []
    current_user = getattr(request.state, "current_user", None)
    try:
        document_info = generate_reconciliation_document_file(
            reconciliation,
            document_format=document_format,
            products=products_for_partner(db, reconciliation.partner_id),
            created_paths=created_paths,
            unique_name=True,
        )
        crud.save_partner_reconciliation_document(
            db, reconciliation, document_info,
            changed_by_user_id=current_user.id if current_user else None,
            commit=False,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        for path in created_paths:
            path.unlink(missing_ok=True)
        if isinstance(exc, (ValueError, AmbiguousCurrentRegistrationError)):
            return redirect_with_message(
                f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
                validation=str(exc),
            )
        raise
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Reconciliation document generated.",
    )


@router.get(
    "/partner-reconciliation/{reconciliation_id}/document",
    dependencies=[Depends(require_permission("export"))],
)
def download_generated_reconciliation_document(
    reconciliation_id: str,
    db: Session = Depends(get_db),
):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    if not reconciliation.document_path or not reconciliation.document_filename:
        raise HTTPException(status_code=404, detail="Reconciliation document not generated")
    path = Path(reconciliation.document_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Reconciliation document file not found")
    return FileResponse(
        path,
        filename=reconciliation.document_filename,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get(
    "/partner-reconciliation/{reconciliation_id}/document/{document_format}",
    dependencies=[Depends(require_permission("export"))],
)
def download_generated_reconciliation_document_by_format(
    reconciliation_id: str,
    document_format: str,
    db: Session = Depends(get_db),
):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    requested_format = document_format.strip().lower()
    if requested_format not in {"docx", "pdf"}:
        raise HTTPException(status_code=404, detail="Reconciliation document format not found")
    if not reconciliation.document_path:
        raise HTTPException(status_code=404, detail="Reconciliation document not generated")
    path = Path(reconciliation.document_path).with_suffix(f".{requested_format}")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Reconciliation document file not found")
    return FileResponse(
        path,
        filename=path.name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/outlook-draft",
    dependencies=[Depends(require_any_permission("edit", "export"))],
)
def create_partner_reconciliation_outlook_draft(
    reconciliation_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    expected_mail_version: str = Form(..., pattern=r"^[0-9a-f]{64}$"),
    email_subject: str = Form(...),
    email_body: str = Form(...),
    db: Session = Depends(get_db),
):
    begin_mail_inputs(db)
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    require_version(reconciliation, expected_version)
    require_mail_ready(db, reconciliation, request, expected_mail_version)
    current_user = getattr(request.state, "current_user", None)
    if reconciliation.reconciliation_status in {"sent", "closed"}:
        raise HTTPException(status_code=409, detail="Reconciliation has already been sent or closed.")
    if reconciliation.outlook_message_id:
        raise HTTPException(status_code=409, detail="Outlook draft already exists. Check Outlook before creating another.")
    recipients = reconciliation_recipients(db, reconciliation.partner_id)
    to_emails = contact_email_list(recipients["to"])
    cc_emails = contact_email_list(recipients["cc"])
    if not to_emails:
        return redirect_with_message(
            f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
            validation="У выбранного партнёра не указаны активные контактные лица для сверки сообщений.",
        )
    if not reconciliation.document_path or not Path(reconciliation.document_path).exists():
        return redirect_with_message(
            f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
            validation="Reconciliation document is not generated.",
        )
    if not outlook_service.is_configured():
        return redirect_with_message(
            f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
            validation="Microsoft Graph is not configured.",
        )
    if not outlook_service.is_authorized(outlook_user_key(request)):
        return redirect_with_message(
            f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
            validation="Microsoft Outlook authorization is required.",
        )
    mail_inputs = build_outlook_preview(db, reconciliation, request)
    mail_inputs.update(subject=email_subject, body=email_body)
    reserve_mail_operation(db, reconciliation, request, "draft", mail_version(reconciliation, mail_inputs))
    try:
        draft = outlook_service.create_outlook_draft(
            subject=email_subject,
            body=email_body,
            to_recipients=to_emails,
            cc_recipients=cc_emails,
            user_key=outlook_user_key(request),
        )
        outlook_service.add_attachment_to_draft(
            message_id=draft["id"],
            file_path=reconciliation.document_path,
            attachment_name=reconciliation.document_filename,
            user_key=outlook_user_key(request),
        )
        web_link = outlook_service.get_message_web_link_if_available(draft)
        crud.mark_partner_reconciliation_outlook_draft_created(
            db,
            reconciliation,
            outlook_message_id=draft["id"],
            outlook_draft_web_link=web_link,
            email_to=email_list_text(to_emails),
            email_cc=email_list_text(cc_emails),
            email_subject=email_subject,
            email_body=email_body,
            changed_by_user_id=current_user.id if current_user else None,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=MAIL_RECOVERY_MESSAGE) from exc
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Outlook draft created.",
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/outlook-send",
    dependencies=[Depends(require_any_permission("approve", "edit"))],
)
def send_partner_reconciliation_outlook_message(
    reconciliation_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    expected_mail_version: str = Form(..., pattern=r"^[0-9a-f]{64}$"),
    db: Session = Depends(get_db),
):
    begin_mail_inputs(db)
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    require_version(reconciliation, expected_version)
    require_mail_ready(db, reconciliation, request, expected_mail_version)
    current_user = getattr(request.state, "current_user", None)
    validation = validate_outlook_send_ready(reconciliation, request)
    if validation:
        return redirect_with_message(
            f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
            validation=validation,
        )
    recipients = reconciliation_recipients(db, reconciliation.partner_id)
    if (reconciliation.email_to != email_list_text(contact_email_list(recipients["to"]))
            or (reconciliation.email_cc or "") != (email_list_text(contact_email_list(recipients["cc"])) or "")):
        raise HTTPException(status_code=409, detail="Получатели изменились после создания черновика. Проверьте письмо в Outlook.")
    draft_claim = db.query(crud.AuditTrail).filter_by(
        entity_type="PartnerReconciliation", entity_id=reconciliation.id,
        action="outlook_draft_reserved", field_name="outlook_status",
    ).order_by(crud.AuditTrail.changed_at.desc()).first()
    if (not draft_claim or draft_claim.user_id != outlook_user_key(request)
            or not draft_claim.comment.endswith("mail_version=" + expected_mail_version)):
        raise HTTPException(status_code=409, detail="Черновик создан другим пользователем или его входные данные изменились. Проверьте Outlook.")
    reserve_mail_operation(db, reconciliation, request, "send")
    try:
        outlook_service.send_outlook_message(
            message_id=reconciliation.outlook_message_id,
            user_key=outlook_user_key(request),
        )
        crud.mark_partner_reconciliation_outlook_sent(
            db,
            reconciliation,
            changed_by_user_id=current_user.id if current_user else None,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=MAIL_RECOVERY_MESSAGE) from exc
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Outlook message sent.",
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/outlook-recover",
    dependencies=[Depends(require_permission("manage_system_settings")), Depends(require_csrf)],
)
def recover_partner_reconciliation_outlook(
    reconciliation_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    outcome: str = Form(..., pattern=r"^(sent|cancelled)$"),
    evidence: str = Form(..., min_length=20, max_length=4000),
    mailbox: str = Form(..., min_length=3, max_length=320),
    operations_stopped: str = Form(..., pattern=r"^yes$"),
    result_verified: str = Form(..., pattern=r"^yes$"),
    verified_message_id: str = Form("", max_length=255),
    verified_sent_at: datetime | None = Form(None),
    db: Session = Depends(get_db),
):
    # Manual reconciliation only: never call Graph or infer absence from a timeout.
    begin_mail_inputs(db)
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(404, "Reconciliation not found")
    require_version(reconciliation, expected_version)
    if (reconciliation.outlook_status not in MAIL_PENDING | {"draft_created"}
            or reconciliation.reconciliation_status in {"sent", "closed", "confirmed", "discrepancy"}):
        raise HTTPException(409, "This Outlook result cannot be reconciled. Reload the page.")
    evidence, mailbox, verified_message_id = evidence.strip(), mailbox.strip(), verified_message_id.strip()
    if len(evidence) < 20 or len(mailbox) < 3:
        raise HTTPException(422, "Describe the mailbox and the external verification.")
    if outcome == "sent":
        if (not verified_message_id or not verified_sent_at or verified_sent_at.utcoffset() is None
                or verified_sent_at > datetime.now(timezone.utc)):
            raise HTTPException(422, "Provide the sent message ID and its timestamp with timezone.")
    elif verified_message_id or verified_sent_at:
        raise HTTPException(422, "Cancelled recovery requires confirmed absence of sending and removal of drafts.")
    fields = ("outlook_status", "outlook_error", "outlook_message_id", "outlook_draft_web_link",
              "reconciliation_status", "draft_created_at", "sent_at", "sent_date")
    old_values = {field: getattr(reconciliation, field) for field in fields}
    reconciliation.outlook_error = None
    reconciliation.outlook_draft_web_link = None
    if outcome == "sent":
        reconciliation.outlook_status = "sent"
        reconciliation.reconciliation_status = "sent"
        reconciliation.outlook_message_id = verified_message_id
        reconciliation.sent_at = verified_sent_at.astimezone(timezone.utc).replace(tzinfo=None)
        reconciliation.sent_date = reconciliation.sent_at.date()
    else:
        reconciliation.outlook_status = OUTLOOK_NOT_CREATED
        reconciliation.reconciliation_status = "generated" if reconciliation.document_path else "draft"
        reconciliation.outlook_message_id = None
        reconciliation.draft_created_at = None
        reconciliation.sent_at = None
        reconciliation.sent_date = None
    reconciliation.updated_by = outlook_user_key(request)
    reconciliation.version += 1
    try:
        crud.log_field_changes(
            db, entity_type="PartnerReconciliation", entity_id=reconciliation.id,
            old_values=old_values, new_values={field: getattr(reconciliation, field) for field in fields},
            action="outlook_recovered", user_id=reconciliation.updated_by,
            comment=json.dumps({"outcome": outcome, "mailbox": mailbox, "evidence": evidence,
                                "operations_stopped": True, "result_verified": True}, ensure_ascii=False),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Результат Outlook согласован и записан в аудит.",
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/response",
    dependencies=[Depends(require_any_permission("upload", "edit"))],
)
def upload_partner_reconciliation_response(
    reconciliation_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    response_file: UploadFile = File(...),
    discrepancy_description: str | None = Form(None),
    db: Session = Depends(get_db),
):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    require_version(reconciliation, expected_version)
    created_paths = []
    current_user = getattr(request.state, "current_user", None)
    try:
        stored_file = store_document_file(response_file, created_paths=created_paths)
        document = crud.create_attachment(
            db,
            file_name=response_file.filename or f"partner_response_{reconciliation_id}",
            attachment_type="partner response",
            document_title=response_file.filename or "Partner response",
            document_type="partner response",
            related_object_type="Reconciliation",
            related_object_id=reconciliation.id,
            partner_id=reconciliation.partner_id,
            mime_type=response_file.content_type or "application/octet-stream",
            file_size_bytes=stored_file["file_size_bytes"],
            storage_path=stored_file["storage_path"],
            status="active",
            comment="Partner response file linked to reconciliation.",
            checksum_sha256=stored_file["checksum_sha256"],
            uploaded_by_user_id=current_user.id if current_user else None,
            commit=False,
        )
        crud.update_partner_reconciliation_status(
            db,
            reconciliation,
            reconciliation_status=(
                "discrepancy"
                if (discrepancy_description or "").strip()
                else "confirmed"
            ),
            response_date=date.today(),
            discrepancy_description=discrepancy_description,
            document_id=document.id,
            changed_by_user_id=current_user.id if current_user else None,
            commit=False,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        for path in created_paths:
            path.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            return redirect_with_message(
                f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
                validation=str(exc),
            )
        raise
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Partner response saved.",
    )


@router.post(
    "/partner-reconciliation/{reconciliation_id}/task",
    dependencies=[Depends(require_any_permission("create", "create_related_requests", "comment"))],
)
def create_partner_reconciliation_task(
    reconciliation_id: str,
    request: Request,
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    require_version(reconciliation, expected_version)
    current_user = getattr(request.state, "current_user", None)
    partner_name = reconciliation.partner.partner_name if reconciliation.partner else "partner"
    try:
        # Claim the parent version in the same transaction as the task and audit.
        reconciliation.version += 1
        reconciliation.updated_by = current_user.id if current_user else None
        db.flush()
        crud.create_task(
            db,
            schemas.TaskCreate(
                title=f"Resolve reconciliation discrepancies: {partner_name}",
                description=reconciliation.discrepancy_description,
                status="new",
                priority="high",
                due_date=date.today() + timedelta(days=7),
                responsible_person=reconciliation.prepared_by,
                related_entity_type="PartnerReconciliation",
                related_entity_id=reconciliation.id,
                comment="Created from reconciliation discrepancy.",
            ),
            created_by_user_id=current_user.id if current_user else None,
            commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={reconciliation_id}",
        message="Task saved.",
    )


@router.post(
    "/partner-reconciliation/items/{item_id}",
    dependencies=[Depends(require_any_permission("edit", "comment"))],
)
def update_partner_reconciliation_item(
    item_id: str,
    reconciliation_id: str = Form(...),
    expected_version: int = Form(..., ge=1),
    expected_item_version: int = Form(..., ge=1),
    internal_case_id: str | None = Form(None),
    reconciliation_status: str = Form(...),
    reviewer_comment: str | None = Form(None),
    confirmed_by_user: str | None = Form(None),
    db: Session = Depends(get_db),
):
    item = crud.get_partner_reconciliation_item(db, item_id)
    if not item or item.reconciliation_id != reconciliation_id or item.reconciliation.is_deleted:
        raise HTTPException(status_code=404, detail="Reconciliation item not found")
    require_version(item.reconciliation, expected_version)
    require_version(item, expected_item_version)
    if reconciliation_status not in RECONCILIATION_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid reconciliation status")
    crud.update_partner_reconciliation_item(
        db,
        item,
        schemas.PartnerReconciliationItemUpdate(
            internal_case_id=internal_case_id or None,
            reconciliation_status=reconciliation_status,
            reviewer_comment=reviewer_comment,
            confirmed_by_user=confirmed_by_user,
        ),
    )
    return redirect_with_message(
        f"/partner-reconciliation?reconciliation_id={item.reconciliation_id}",
        message="Reconciliation item saved.",
    )


@router.get("/partner-reconciliation/export", dependencies=[Depends(require_permission("export"))])
def export_partner_reconciliation(
    request: Request,
    reconciliation_id: str | None = None,
    partner_id: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    contact_id: str | None = None,
    language: str = "ru",
    db: Session = Depends(get_db),
):
    if reconciliation_id:
        reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail="Reconciliation not found")
        our_items = [item for item in reconciliation.items if item.source_side == "our_company"]
        partner_items = [item for item in reconciliation.items if item.source_side == "partner"]
        discrepancy_items = [
            item
            for item in reconciliation.items
            if item.reconciliation_status not in {"matched", "confirmed"}
        ]
        content = build_reconciliation_workbook(
            partner_name=reconciliation.partner.partner_name,
            period_start=reconciliation.period_start,
            period_end=reconciliation.period_end,
            generated_at=date.today(),
            contact_name=reconciliation.contact_name,
            contact_email=reconciliation.contact_email,
            prepared_by=reconciliation.prepared_by,
            status=reconciliation.reconciliation_status,
            our_items=our_items,
            partner_items=partner_items,
            discrepancy_items=discrepancy_items,
            language=language or reconciliation.language,
        )
        filename = f"partner_reconciliation_{reconciliation.id}.xlsx"
    else:
        if not partner_id or not period_start or not period_end:
            raise HTTPException(status_code=400, detail="Partner and period are required")
        preview = build_reconciliation_preview(
            db,
            partner_id=partner_id,
            period_start=period_start,
            period_end=period_end,
            contact_id=contact_id,
            language=language,
        )
        content = build_reconciliation_workbook(
            partner_name=preview["partner"].partner_name,
            period_start=period_start,
            period_end=period_end,
            generated_at=date.today(),
            contact_name=contact_full_name(preview["contact"]),
            contact_email=preview["contact"].email if preview["contact"] else None,
            prepared_by=current_user_display_name(request),
            status="draft",
            our_items=preview["our_items"],
            partner_items=preview["partner_items"],
            discrepancy_items=preview["discrepancy_items"],
            language=language,
        )
        filename = "partner_reconciliation_preview.xlsx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/partner-reconciliations", response_model=list[schemas.PartnerReconciliationRead])
def api_list_partner_reconciliations(db: Session = Depends(get_db)):
    return crud.list_partner_reconciliations(db)


@router.get(
    "/api/partner-reconciliations/{reconciliation_id}",
    response_model=schemas.PartnerReconciliationRead,
)
def api_get_partner_reconciliation(reconciliation_id: str, db: Session = Depends(get_db)):
    reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    return reconciliation


def base_context(request: Request, db: Session) -> dict:
    latest = crud.get_latest_partner_reconciliation(db)
    partners = [
        partner
        for partner in crud.list_partners(db)
        if partner.reconciliation_frequency in RECONCILIATION_FREQUENCIES
    ]
    return {
        "request": request,
        "active_page": "partner_reconciliation",
        "partners": partners,
        "contacts": crud.list_contract_contacts(db),
        "products": crud.list_products(db),
        "cases": crud.list_cases(db),
        "reconciliations": crud.list_partner_reconciliations(db),
        "latest_reconciliation": latest,
        "outlook_configured": outlook_service.is_configured(),
        "outlook_authorized": outlook_service.is_authorized(outlook_user_key(request)),
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
        "validation": request.query_params.get("validation"),
    }


def reconciliation_control_context(
    *,
    partners: list,
    contacts: list,
    reconciliations: list,
    selected_partner_id: str | None,
    period_start: date,
    period_end: date,
    current_reconciliation_id: str | None,
) -> dict:
    by_partner: dict[str, list] = {}
    for reconciliation in reconciliations:
        by_partner.setdefault(reconciliation.partner_id, []).append(reconciliation)

    contacts_by_partner: dict[str, list] = {}
    for contact in contacts:
        if not getattr(contact, "is_active", True) or not getattr(contact, "is_current", True):
            continue
        contacts_by_partner.setdefault(contact.partner_id, []).append(contact)

    existing_period_reconciliation = None
    if selected_partner_id and period_start and period_end:
        for reconciliation in by_partner.get(selected_partner_id, []):
            if reconciliation.id == current_reconciliation_id:
                continue
            if (
                reconciliation.period_start == period_start
                and reconciliation.period_end == period_end
            ):
                existing_period_reconciliation = reconciliation
                break

    queue = []
    today = date.today()
    for partner in partners:
        frequency = partner.reconciliation_frequency or "not_conducted"
        if frequency == "not_conducted":
            continue

        latest = by_partner.get(partner.id, [None])[0]
        contact = preferred_reconciliation_contact(contacts_by_partner.get(partner.id, []))
        target_start = period_start
        target_end = period_end
        planned_date = period_end
        status = "not_started"
        href = (
            f"/partner-reconciliation?partner_id={partner.id}"
            f"&period_start={target_start}&period_end={target_end}"
        )
        if contact:
            href += f"&contact_id={contact.id}"

        if latest and latest.reconciliation_status not in {"sent", "confirmed", "closed"}:
            target_start = latest.period_start
            target_end = latest.period_end
            planned_date = latest.period_end
            status = "in_progress"
            href = f"/partner-reconciliation?reconciliation_id={latest.id}"
        elif latest:
            target_start, target_end = next_reconciliation_period(latest, frequency)
            planned_date = target_end
            href = (
                f"/partner-reconciliation?partner_id={partner.id}"
                f"&period_start={target_start}&period_end={target_end}"
            )
            if contact:
                href += f"&contact_id={contact.id}"
            if planned_date < today:
                status = "overdue"
            elif planned_date <= today + timedelta(days=14):
                status = "due"
            else:
                status = "planned"

        queue.append(
            {
                "partner": partner,
                "contact": contact,
                "period_start": target_start,
                "period_end": target_end,
                "planned_date": planned_date,
                "status": status,
                "href": href,
                "latest": latest,
            }
        )

    priority = {"overdue": 0, "due": 1, "in_progress": 2, "not_started": 3, "planned": 4}
    queue.sort(
        key=lambda row: (
            priority.get(row["status"], 9),
            row["planned_date"] or date.max,
            row["partner"].partner_name,
        )
    )
    schedule = reconciliation_schedule_rows(
        partners=partners,
        reconciliations=reconciliations,
        today=today,
    )
    completed = [
        reconciliation
        for reconciliation in reconciliations
        if reconciliation.reconciliation_status in {"sent", "confirmed", "closed"}
    ]
    in_progress = [
        reconciliation
        for reconciliation in reconciliations
        if reconciliation.reconciliation_status not in {"sent", "confirmed", "closed"}
    ]
    return {
        "reconciliation_queue": queue,
        "reconciliation_schedule": schedule,
        "completed_reconciliations": completed,
        "in_progress_reconciliations": in_progress,
        "existing_period_reconciliation": existing_period_reconciliation,
    }


def reconciliation_schedule_rows(
    *,
    partners: list,
    reconciliations: list,
    today: date | None = None,
) -> list[dict]:
    """Build the compact monthly/quarterly reconciliation checklist."""
    today = today or date.today()
    by_period = {}
    for reconciliation in reconciliations:
        by_period.setdefault(
            (
                reconciliation.partner_id,
                reconciliation.period_start,
                reconciliation.period_end,
            ),
            reconciliation,
        )
    rows = []
    for partner in partners:
        frequency = partner.reconciliation_frequency
        if frequency == "monthly":
            period_start, period_end = previous_month_bounds(today)
            frequency_label = "Месяц"
        elif frequency == "quarterly":
            period_start, period_end = previous_quarter_bounds(today)
            frequency_label = "Квартал"
        else:
            continue

        reconciliation = by_period.get((partner.id, period_start, period_end))
        if reconciliation:
            href = f"/partner-reconciliation?reconciliation_id={reconciliation.id}"
        else:
            href = (
                f"/partner-reconciliation?partner_id={partner.id}"
                f"&period_start={period_start}&period_end={period_end}"
            )
        rows.append(
            {
                "partner": partner,
                "frequency": frequency,
                "frequency_label": frequency_label,
                "period_start": period_start,
                "period_end": period_end,
                "period_label": (
                    f"{period_start.strftime('%d.%m.%Y')} — "
                    f"{period_end.strftime('%d.%m.%Y')}"
                ),
                "reconciliation": reconciliation,
                "completed": reconciliation is not None,
                "href": href,
            }
        )

    return sorted(
        rows,
        key=lambda row: (
            row["completed"],
            row["frequency"] == "quarterly",
            row["partner"].partner_name.casefold(),
        ),
    )


def preferred_reconciliation_contact(contacts: list):
    if not contacts:
        return None
    return sorted(
        contacts,
        key=lambda contact: (
            not getattr(contact, "is_reconciliation_recipient", False),
            not getattr(contact, "is_primary", False),
            contact.last_name or "",
            contact.first_name or "",
        ),
    )[0]


def current_user_display_name(request: Request) -> str:
    current_user = getattr(request.state, "current_user", None)
    if not current_user:
        return ""
    return current_user.full_name or current_user.email or ""


def next_reconciliation_period(latest, frequency: str) -> tuple[date, date]:
    start = latest.period_end + timedelta(days=1)
    days_by_frequency = {
        "weekly": 7,
        "monthly": 31,
        "quarterly": 92,
        "annual": 365,
        "yearly": 365,
    }
    days = days_by_frequency.get(frequency, 31)
    return start, start + timedelta(days=days - 1)


MAIL_PENDING = {"draft_pending", "send_pending"}
MAIL_RECOVERY_MESSAGE = (
    "Результат операции Outlook требует проверки. Повторный вызов заблокирован. "
    "Проверьте черновики и отправленные письма в Outlook и обратитесь к администратору "
    "для согласования состояния сверки."
)


def begin_mail_inputs(db):
    # Serialize the local input snapshot and reservation, release before network.
    if db.get_bind().dialect.name == "sqlite":
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        except OperationalError as exc:
            db.rollback()
            if "locked" in str(exc.orig).lower():
                raise StaleDataError("Reload before sending.") from exc
            raise


def mail_version(reconciliation, preview) -> str:
    path = Path(reconciliation.document_path) if reconciliation.document_path else None
    payload = {key: preview[key] for key in ("to", "cc", "subject", "body", "attachment")}
    payload["document"] = hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None
    payload["path"] = reconciliation.document_path
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def require_mail_ready(db, reconciliation, request, expected_mail_version):
    if reconciliation.outlook_status in MAIL_PENDING:
        raise HTTPException(status_code=409, detail=MAIL_RECOVERY_MESSAGE)
    preview = build_outlook_preview(db, reconciliation, request)
    if preview["mail_version"] != expected_mail_version:
        raise StaleDataError("Mail inputs changed. Reload before sending.")


def reserve_mail_operation(db, reconciliation, request, operation, input_version=None):
    reconciliation.outlook_status = operation + "_pending"
    reconciliation.outlook_error = MAIL_RECOVERY_MESSAGE
    reconciliation.updated_by = getattr(request.state.current_user, "id", None)
    reconciliation.version += 1
    crud.log_field_changes(
        db, entity_type="PartnerReconciliation", entity_id=reconciliation.id,
        old_values={}, new_values={"outlook_status": reconciliation.outlook_status},
        action="outlook_" + operation + "_reserved", user_id=reconciliation.updated_by,
        comment="operation_id=" + str(uuid4()) + ("; mail_version=" + input_version if input_version else ""),
    )
    expire_on_commit = db.expire_on_commit
    db.expire_on_commit = False
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.expire_on_commit = expire_on_commit
    # Do not refresh: retain the claimed version for the completion UPDATE.


def build_outlook_preview(db: Session, reconciliation, request: Request) -> dict:
    recipients = reconciliation_recipients(db, reconciliation.partner_id)
    to_emails = contact_email_list(recipients["to"])
    cc_emails = contact_email_list(recipients["cc"])
    email = render_reconciliation_email(
        reconciliation,
        current_user=getattr(request.state, "current_user", None),
    )
    preview = {
        "to": to_emails,
        "cc": cc_emails,
        "email_to": email_list_text(to_emails),
        "email_cc": email_list_text(cc_emails),
        "subject": reconciliation.email_subject or email["subject"],
        "body": reconciliation.email_body or email["body"],
        "attachment": reconciliation.document_filename or "",
        "has_to": bool(to_emails),
        "has_document": bool(
            reconciliation.document_path and Path(reconciliation.document_path).exists()
        ),
        "configured": outlook_service.is_configured(),
        "authorized": outlook_service.is_authorized(outlook_user_key(request)),
    }

    preview["mail_version"] = mail_version(reconciliation, preview)
    return preview


def validate_outlook_send_ready(reconciliation, request: Request) -> str | None:
    if reconciliation.reconciliation_status in {"sent", "closed"}:
        return "Reconciliation has already been sent or closed."
    if not reconciliation.document_path or not Path(reconciliation.document_path).exists():
        return "Reconciliation document is not generated."
    if not reconciliation.email_to:
        return "У выбранного партнёра не указаны активные контактные лица для сверки сообщений."
    if not outlook_service.is_configured():
        return "Microsoft Graph is not configured."
    if not outlook_service.is_authorized(outlook_user_key(request)):
        return "Microsoft Outlook authorization is required."
    if not reconciliation.outlook_message_id:
        return "Outlook draft is not created."
    return None


def outlook_user_key(request: Request) -> str:
    current_user = getattr(request.state, "current_user", None)
    return current_user.id if current_user else "default"


def filter_items(
    items: list,
    *,
    status_filter: str | None,
    product_id: str | None,
    search: str | None,
) -> list:
    result = items
    if status_filter:
        result = [
            item
            for item in result
            if get_value(item, "reconciliation_status") == status_filter
        ]
    if product_id:
        result = [
            item
            for item in result
            if get_value(item, "product_id") == product_id
        ]
    if search:
        needle = search.lower()
        result = [
            item
            for item in result
            if needle
            in " ".join(
                str(get_value(item, field) or "")
                for field in [
                    "internal_case_number",
                    "partner_case_number",
                    "product_name",
                    "active_substance",
                    "patient",
                    "adverse_event",
                    "short_description",
                    "reviewer_comment",
                ]
            ).lower()
        ]
    return result


def get_value(item, field: str):
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def product_names(products: list) -> str:
    return ", ".join(product.product_name for product in products)


def optional_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)
