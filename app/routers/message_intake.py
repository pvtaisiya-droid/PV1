from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import ae_card, crud, message_intake as intake
from app.auth import require_any_permission, require_permission
from app.config import get_settings
from app.database import get_db
from app.duplicates import DuplicateContactError, DuplicateMessageError, blocked_contact_candidates, contact_candidates, message_candidates, message_fingerprint
from app.models import ContractContact
from app.security import require_csrf
from app.templating import templates
from app.ui_helpers import redirect_with_message
from app.unit_of_work import atomic_write


router = APIRouter()


async def form_data(request: Request):
    return await request.form()


def get_report(db, report_id):
    report = crud.get_safety_report(db, report_id)
    if not report:
        raise HTTPException(404, "Сообщение не найдено.")
    return report


def page(request, db, *, report=None, values=None, validation=None,
         contact_duplicates=None, message_duplicates=None, status_code=200, e2b_draft=None):
    today = datetime.now(ZoneInfo(get_settings().business_timezone)).date()
    if values is None:
        values = intake.report_values(report) if report else {
            "received_date": today.isoformat(), "message_direction": "our_company_to_partner",
            "creation_token": str(uuid4()), "product_ids": [], "routes": [],
        }
    contacts = crud.list_contract_contacts(db)
    if report:
        present = {contact.id for contact in contacts}
        route_contacts = [(route.details or {}).get("contact_id") for route in report.processing_routes
                          if route.route_kind == "medical" and not route.is_deleted]
        for identifier in [report.sender_contact_id, report.reporter_contact_id, *route_contacts]:
            if identifier and identifier not in present:
                historical = db.get(ContractContact, identifier)
                if historical:
                    contacts.append(historical)
                    present.add(identifier)
    contact_data = [{"id": contact.id, "name": contact.known_name or "", "label": contact.full_name, "email": contact.email or "", "country": contact.country or "", "saved": True,
                     "phone": contact.mobile_phone or contact.work_phone or "", "partner_id": contact.partner_id or ""}
                    for contact in contacts]
    documents = [] if report is None else [document for document in crud.list_attachments(db) if document.safety_report_id == report.id]
    pv_route = ae_card.route_for(report) if report else None
    state = ae_card.state_for(report, pv_route.details if pv_route else {})
    if values.get("pv_card_json"):
        try:
            state = ae_card.decode(values["pv_card_json"])
        except ValueError:
            pass
    return templates.TemplateResponse(request, "message_intake.html", {
        "request": request, "report": report, "values": values, "contacts": contacts,
        "contact_data": contact_data, "partners": crud.list_partners(db), "products": crud.list_products(db),
        "routes": intake.ROUTES, "complaint_groups": intake.COMPLAINT_GROUPS,
        "legacy_complaints": [label for label in intake.LEGACY_COMPLAINTS
                              if label in (values.get("quality_complaints") or [])],
        "special_situations": intake.SPECIAL_SITUATIONS,
        "documents": documents, "validation": validation, "contact_duplicates": contact_duplicates or [],
        "message_duplicates": message_duplicates or [], "active_page": "safety_reports",
        "message": request.query_params.get("message"), "today": today,
        "checked": lambda name: values.get(name) is True or str(values.get(name, "")).lower() in {"true", "1", "yes", "on"},
        "route_numbers": {row.route_kind: row.route_number for row in report.processing_routes} if report else {},
        "ae_state": state, "ae_products": [{"id": p.id, "name": p.product_name} for p in crud.list_products(db)],
        "ae_contacts": contact_data, "ae_case_mode": False, "ae_linked_case": ae_card.linked_case(report),
        "e2b_draft": e2b_draft,
        "embedded": request.query_params.get("embed") == "package" or e2b_draft is not None,
    }, status_code=status_code)


@router.get("/safety-reports/new", response_class=HTMLResponse)
def new_message(request: Request, db: Session = Depends(get_db)):
    return page(request, db, validation=request.query_params.get("validation"))


@router.get("/safety-reports/{report_id}/intake", response_class=HTMLResponse)
def message_intake_page(report_id: str, request: Request, db: Session = Depends(get_db)):
    return page(request, db, report=get_report(db, report_id))


def package_group(db, batch_id, token):
    from app.e2b_import import E2BImportService
    from app.models import E2BImportBatch
    batch = db.query(E2BImportBatch).filter_by(id=batch_id, is_deleted=False).first()
    if not batch:
        raise HTTPException(404, "Пакет не найден.")
    group = next((row for row in E2BImportService().preview_groups(db, batch) if row["token"] == token), None)
    if not group:
        raise HTTPException(404, "Случай пакета не найден.")
    return batch, group


def package_intake_page(request, db, batch, group, *, values=None, validation=None, status_code=200):
    from app.concurrency import CASE_INPUTS, snapshot_token
    from app.e2b_review import document_review, draft_values
    from app.e2b_import import batch_case_groups
    groups = batch_case_groups(batch)
    selected_ids = values.get("document_ids", []) if values and values.get("documents_explicit") else None
    return page(request, db, values=values if values is not None else draft_values(db, batch, group),
        validation=validation, status_code=status_code,
        e2b_draft={"batch_id": batch.id, "token": group["token"], "version": batch.version,
                   "case_token": group["expected_case_token"], "inputs": snapshot_token(db, CASE_INPUTS),
                   "can_import": group["can_import"],
                   "case_count": len(groups),
                   "documents": document_review(batch, group, groups, selected_ids)})


@router.get("/safety-reports/e2b-imports/{batch_id}/intake", response_class=HTMLResponse)
def package_intake_preview(batch_id: str, request: Request, case_group: str, db: Session = Depends(get_db)):
    batch, group = package_group(db, batch_id, case_group)
    if group["import_result"]:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(f'/safety-reports/{group["import_result"]["safety_report_id"]}/intake?step=pv&embed=package', status_code=303)
    return package_intake_page(request, db, batch, group)


@router.post("/safety-reports/e2b-imports/{batch_id}/intake",
             dependencies=[Depends(require_any_permission("create", "icsr_workflow")), Depends(require_csrf)])
@atomic_write
def register_package_case(batch_id: str, request: Request, case_group: str, form=Depends(form_data), db: Session = Depends(get_db)):
    import json
    from app.concurrency import CASE_INPUTS, protect_snapshot, require_version
    from app.e2b_import import E2BImportService
    batch, group = package_group(db, batch_id, case_group)
    try:
        version = int(form.get("expected_import_version") or "0")
    except (ValueError, TypeError):
        raise HTTPException(422, "Обновите страницу перед импортом.")
    if version < 1:
        raise HTTPException(422, "Обновите страницу перед импортом.")
    require_version(batch, version)
    protect_snapshot(db, CASE_INPUTS, form.get("expected_inputs") or "")
    if group["import_result"]:
        raise HTTPException(409, "Случай уже импортирован. Обновите страницу.")
    try:
        if intake.flag(form, "save_sender_contact") and not {"create", "manage_reference_data"} & request.state.permission_codes:
            raise HTTPException(403, "Недостаточно прав для сохранения контакта.")
        summary = E2BImportService().confirm_batch(db, batch, confirmed_by_user_id=request.state.current_user.id,
            expected_case_token=form.get("expected_case_token") or "", case_group=case_group,
            document_ids=form.getlist("document_ids") if intake.flag(form, "documents_explicit") else None)
        report = get_report(db, summary["safety_report_id"])
        if summary.get("duplicate_import"):
            return redirect_with_message(f"/safety-reports/{report.id}/intake?step=pv&embed=package",
                message="Источник уже импортирован. Открыто существующее сообщение.")
        mutable = form.multi_items()
        from starlette.datastructures import FormData
        overrides = {"expected_version": str(report.version), "expected_case_version": str(ae_card.linked_case(report).version)}
        if intake.flag(form, "pv_card_dirty"):
            state = ae_card.decode(form.get("pv_card_json"))
            # Bind projected medications back to the imported rows so editing the
            # short card retains source doses, roles, dates and other details.
            available = list(ae_card.linked_case(report).case_products)
            for medication in state["medications"]:
                row = next((row for row in available if (row.reported_product_name or "").casefold().strip() == medication["reported"].casefold().strip()), None)
                if row:
                    medication["caseProductId"] = row.id
                    available.remove(row)
            overrides["pv_card_json"] = json.dumps(state, ensure_ascii=False)
        submitted = FormData([(key, value) for key, value in mutable if key not in overrides] + list(overrides.items()))
        report = intake.save_intake(db, submitted, report=report, actor_id=request.state.current_user.id,
            can_save_contacts=bool({"create", "manage_reference_data"} & request.state.permission_codes))
    except (ValueError, ValidationError) as exc:
        db.rollback()
        batch, group = package_group(db, batch_id, case_group)
        return package_intake_page(request, db, batch, group, values=intake.posted_values(form), validation=str(exc), status_code=422)
    return redirect_with_message(f"/safety-reports/{report.id}/intake?step=pv&embed=package", message="Сообщение сохранено.")


def persist(request, db, form, report=None):
    permissions = getattr(request.state, "permission_codes", set())
    if intake.flag(form, "save_sender_contact") and not {"create", "manage_reference_data"} & permissions:
        raise HTTPException(403, "Для сохранения в контактных лицах требуется право создания контактов.")
    handoff = form.get("action") == "handoff" or (form.get("action") == "next" and form.get("current_step") == "pv" and "other" not in form.getlist("routes"))
    if handoff and not ae_card.linked_case(report) and not {"create", "icsr_workflow"} & permissions:
        raise HTTPException(403, "Недостаточно прав для создания ИСНР.")
    try:
        report = intake.save_intake(db, form, report=report, can_save_contacts=bool({"create", "manage_reference_data"} & permissions),
                                   actor_id=getattr(getattr(request.state, "current_user", None), "id", None))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except (ValueError, ValidationError) as exc:
        db.rollback()
        if isinstance(exc, ValidationError):
            message = "; ".join(" / ".join(map(str, error["loc"])) + ": " + error["msg"] for error in exc.errors())
        else:
            message = str(exc)
        return page(request, db, report=report, values=intake.posted_values(form), validation=message,
                    contact_duplicates=exc.candidates if isinstance(exc, DuplicateContactError) else None,
                    message_duplicates=exc.candidates if isinstance(exc, DuplicateMessageError) else None,
                    status_code=409 if isinstance(exc, (DuplicateContactError, DuplicateMessageError)) else 422)
    active = {row.route_kind for row in report.processing_routes if row.is_active and not row.is_deleted}
    case = ae_card.linked_case(report)
    if case and (form.get("action") == "handoff" or
            (form.get("action") == "next" and form.get("current_step") == "pv" and "other" not in active)):
        return redirect_with_message(f"/cases/{case.id}", message="Сведения сохранены. Продолжите заполнение ИСНР.")
    requested_step = str(form.get("next_step") or "directions")
    if requested_step not in {"registration", "directions", *(key for key, _, _ in intake.ROUTES)}:
        requested_step = "directions"
    embedded = "&embed=package" if request.query_params.get("embed") == "package" else ""
    return redirect_with_message(f"/safety-reports/{report.id}/intake?step={requested_step}{embedded}", message="Сообщение сохранено.")


@router.post("/safety-reports/intake", dependencies=[Depends(require_permission("create")), Depends(require_csrf)])
@atomic_write
def create_incoming_message(request: Request, form=Depends(form_data), db: Session = Depends(get_db)):
    return persist(request, db, form)


@router.post("/safety-reports/{report_id}/intake", dependencies=[Depends(require_any_permission("edit", "icsr_workflow")), Depends(require_csrf)])
@atomic_write
def update_incoming_message(report_id: str, request: Request, form=Depends(form_data), db: Session = Depends(get_db)):
    return persist(request, db, form, get_report(db, report_id))


@router.post("/cases/{case_id}/ae-card", dependencies=[Depends(require_any_permission("edit", "icsr_workflow")), Depends(require_csrf)])
@atomic_write
def save_case_ae_card(case_id: str, request: Request, form=Depends(form_data), db: Session = Depends(get_db)):
    from app.concurrency import ONBOARDING_INPUTS, lock_snapshot_inputs, protect_record, require_version
    from app.models import Case
    lock_snapshot_inputs(db, ONBOARDING_INPUTS + ("Case", "SafetyReport", "SafetyReportRoute"))
    try:
        case_version = int(form.get("expected_case_version") or 0)
        report_version = int(form.get("expected_version") or 0)
    except (ValueError, TypeError):
        raise HTTPException(422, "Обновите страницу перед сохранением.")
    if case_version < 1 or report_version < 1:
        raise HTTPException(422, "Обновите страницу перед сохранением.")
    case = protect_record(db, Case, case_id, case_version)
    # protect_record returns the protected object, including current lock state.
    if case is None:
        case = crud.get_case(db, case_id)
    if not case or not case.safety_report or not ae_card.route_for(case.safety_report):
        raise HTTPException(404, "Связанное сообщение не найдено.")
    report = case.safety_report
    require_version(report, report_version)
    try:
        crud.assert_case_editable(case)
        state = ae_card.validate_state(db, ae_card.decode(form.get("pv_card_json")), report, save_contacts=True,
            can_save_contacts=bool({"create", "manage_reference_data"} & request.state.permission_codes))
        ae_card.sync_report_people(db, report, state)
        ae_card.apply_to_case(db, report, state)
        route = ae_card.route_for(report)
        old = route.details
        route.details = ae_card.details_from_state(state)
        crud.log_field_changes(db, entity_type="SafetyReportRoute", entity_id=route.id,
                              old_values={"details": old}, new_values={"details": route.details})
        identifiers = list(dict.fromkeys(report.product_ids + route.details["product_ids"]))
        crud.sync_message_product_links(db, report, intake.SafetyReportProduct, identifiers)
        db.commit()
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        db.rollback()
        values = {**intake.report_values(report), **intake.posted_values(form)}
        return page(request, db, report=report, values=values, validation=str(exc), status_code=422)
    return redirect_with_message(f"/cases/{case.id}?tab=medical", message="Сведения сохранены.")


@router.post("/safety-reports/check-duplicates", dependencies=[Depends(require_any_permission("create", "edit", "icsr_workflow")), Depends(require_csrf)])
def check_message_duplicates(form=Depends(form_data), db: Session = Depends(get_db)):
    received = form.get("received_date")
    try:
        received = datetime.strptime(received, "%Y-%m-%d").date() if received else None
    except (ValueError, TypeError):
        received = None
    source_text = form.get("original_text") or form.get("raw_text")
    existing = crud.get_safety_report(db, form.get("report_id")) if form.get("report_id") else None
    if existing and message_fingerprint(source_text) == message_fingerprint(existing.original_text or existing.raw_text):
        source_text = None
    matches = [] if form.get("skip_messages") == "true" else message_candidates(db, text=source_text, received_date=received,
                                 sender_email=form.get("sender_email"), sender_phone=form.get("sender_phone"),
                                 checksums=form.getlist("checksums"), exclude_id=form.get("report_id"))
    contacts = contact_candidates(db, name=form.get("sender_name"), email=form.get("sender_email"),
                                  phones=[form.get("sender_phone")], partner_id=form.get("partner_id") or None)
    return {
        "contact_separate_allowed": not blocked_contact_candidates(contacts, allowed=True,
            partner_id=form.get("partner_id") or None, email=form.get("sender_email")),
        "messages": [{"id": item["report"].id, "number": item["report"].safety_report_number,
                      "url": f'/safety-reports/{item["report"].id}/intake', "reasons": item["reasons"]} for item in matches],
        "contacts": [{"id": item["contact"].id, "name": item["contact"].known_name or "", "label": item["contact"].full_name,
                      "email": item["contact"].email or "", "phone": item["contact"].mobile_phone or item["contact"].work_phone or "",
                      "partner_id": item["contact"].partner_id or "",
                      "url": f'/contract-contacts/{item["contact"].id}', "reasons": item["reasons"]} for item in contacts],
    }
