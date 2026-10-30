from app import models
from app.concurrency import protect_record
from app.unit_of_work import atomic_write
import json
from datetime import date

from fastapi import Query, APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission, require_permission
from app.causality import (
    ASSESSOR_ROLES,
    CAUSALITY_RESULT_LABELS,
    NARANJO_QUESTIONS,
    WHO_UMC_CATEGORIES,
    WHO_UMC_DESCRIPTIONS,
)
from app.case_numbering import correct_numbering_partner, finalize_provisional_number
from app.database import get_db
from app.message_directions import ResolvedMessageDirection
from app.pagination import paginate_query
from app.statuses import status_codes
from app.templating import templates
from app.ui_helpers import active_filters, redirect_with_message


router = APIRouter()
CASE_STATUS_OPTIONS = status_codes("case")
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def parse_optional_query_date(value: str | None, label: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label}: укажите дату в формате ГГГГ-ММ-ДД.",
        ) from exc


@router.get("/cases", response_class=HTMLResponse)
def cases_page(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    partner_id: str | None = None,
    product_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    per_page: int | None = None,
    db: Session = Depends(get_db),
):
    parsed_date_from = parse_optional_query_date(date_from, "Дата с")
    parsed_date_to = parse_optional_query_date(date_to, "Дата по")
    total_count = crud.cases_query(db).order_by(None).count()
    page_cases, pagination = paginate_query(crud.filtered_cases_query(
        db, search=search, status_filter=status_filter, partner_id=partner_id,
        product_id=product_id, date_from=parsed_date_from, date_to=parsed_date_to,
    ), page, per_page)
    filters = {
        "search": search or "",
        "status_filter": status_filter or "",
        "partner_id": partner_id or "",
        "product_id": product_id or "",
        "date_from": parsed_date_from or "",
        "date_to": parsed_date_to or "",
        "active": active_filters(
            search=search,
            status_filter=status_filter,
            partner_id=partner_id,
            product_id=product_id,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
        ),
    }
    return templates.TemplateResponse(
        request,
        "cases.html",
        {
            "request": request,
            "cases": page_cases,
            "partners": crud.list_partners(db),
            "products": crud.list_products(db),
            "status_options": CASE_STATUS_OPTIONS,
            "filters": filters,
            "total_count": total_count,
            "filtered_count": pagination.total,
            "pagination": pagination,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "active_page": "cases",
        },
    )


@router.get(
    "/cases/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("create"))],
)
def new_case_page(request: Request, db: Session = Depends(get_db)):
    available_reports = [
        report for report in crud.list_safety_reports(db) if not report.case_id
    ]
    return templates.TemplateResponse(
        request,
        "case_new.html",
        {
            "request": request,
            "partners": crud.list_partners(db),
            "products": crud.list_products(db),
            "reports": available_reports,
            "today": date.today(),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "active_page": "cases",
        },
    )


@router.post("/cases", dependencies=[Depends(require_permission("create"))])
@atomic_write
def create_case_form(
    case_number: str | None = Form(None),
    worldwide_case_id: str | None = Form(None),
    safety_report_id: str | None = Form(None),
    partner_id: str | None = Form(None),
    case_type: str | None = Form("spontaneous"),
    report_type: str | None = Form(None),
    initial_received_date: str | None = Form(None),
    latest_received_date: str | None = Form(None),
    country_of_occurrence: str | None = Form(None),
    seriousness: str | None = Form("non-serious"),
    narrative: str | None = Form(None),
    workflow_status: str = Form("new"),
    due_date: str | None = Form(None),
    expected_report_version: int | None = Form(None, gt=0),
    db: Session = Depends(get_db),
):
    if safety_report_id:
        if expected_report_version is None:
            raise HTTPException(status_code=422, detail="Expected report version is required")
        protect_record(db, models.SafetyReport, safety_report_id, expected_report_version)
    try:
        case = crud.create_case(
            db,
            schemas.CaseCreate(
                case_number=case_number,
                worldwide_case_id=worldwide_case_id,
                safety_report_id=safety_report_id,
                partner_id=partner_id,
                case_type=case_type,
                report_type=report_type,
                initial_received_date=initial_received_date or None,
                latest_received_date=latest_received_date or None,
                country_of_occurrence=country_of_occurrence,
                seriousness=seriousness,
                narrative=narrative,
                workflow_status=workflow_status,
                due_date=due_date or None,
            ),
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/cases/new", error="Case could not be saved.")
    return redirect_with_message(f"/cases/{case.id}", message="Case saved.")


@router.post("/cases/manual", dependencies=[Depends(require_permission("create"))])
@atomic_write
def create_manual_case_form(
    request: Request,
    case_number: str | None = Form(None),
    worldwide_case_id: str | None = Form(None),
    safety_report_id: str | None = Form(None),
    partner_id: str | None = Form(None),
    source_type: str | None = Form("other"),
    reporter_name: str | None = Form(None),
    reporter_email: str | None = Form(None),
    reporter_country_code: str | None = Form(None),
    case_type: str | None = Form("spontaneous"),
    report_type: str | None = Form(None),
    initial_received_date: str | None = Form(None),
    latest_received_date: str | None = Form(None),
    country_of_occurrence: str | None = Form(None),
    listedness: str | None = Form(None),
    expectedness: str | None = Form(None),
    case_outcome: str | None = Form(None),
    narrative: str | None = Form(None),
    workflow_status: str = Form("data_entry"),
    due_date: str | None = Form(None),
    patient_initials: str | None = Form(None),
    patient_identifier: str | None = Form(None),
    sex: str | None = Form(None),
    date_of_birth: str | None = Form(None),
    age_value: float | None = Form(None),
    age_unit: str | None = Form("years"),
    weight_kg: float | None = Form(None),
    height_cm: float | None = Form(None),
    pregnancy_status: str | None = Form(None),
    medical_history_text: str | None = Form(None),
    product_id: str | None = Form(None),
    reported_product_name: str | None = Form(None),
    active_substance_text: str | None = Form(None),
    drug_role: str = Form("suspect"),
    indication_text: str | None = Form(None),
    dose_value: str | None = Form(None),
    dose_unit: str | None = Form(None),
    route: str | None = Form(None),
    frequency: str | None = Form(None),
    therapy_start_date: str | None = Form(None),
    therapy_end_date: str | None = Form(None),
    action_taken: str | None = Form(None),
    batch_lot_number: str | None = Form(None),
    reported_term: str | None = Form(None),
    verbatim_term: str | None = Form(None),
    meddra_pt_code: str | None = Form(None),
    meddra_pt_name: str | None = Form(None),
    onset_date: str | None = Form(None),
    end_date: str | None = Form(None),
    reaction_outcome: str | None = Form(None),
    is_serious: bool = Form(False),
    seriousness_death: bool = Form(False),
    seriousness_life_threatening: bool = Form(False),
    seriousness_hospitalization: bool = Form(False),
    seriousness_disability: bool = Form(False),
    seriousness_congenital_anomaly: bool = Form(False),
    seriousness_other_medically_important: bool = Form(False),
    document_file: UploadFile | None = File(None),
    document_title: str | None = Form(None),
    document_type: str | None = Form("other"),
    document_comment: str | None = Form(None),
    next_step: str = Form("entry"),
    expected_report_version: int | None = Form(None, gt=0),
    db: Session = Depends(get_db),
):
    if safety_report_id:
        if expected_report_version is None:
            raise HTTPException(status_code=422, detail="Expected report version is required")
        protect_record(db, models.SafetyReport, safety_report_id, expected_report_version)
    from app.routers.placeholders import safe_display_name, store_document_file
    original_document_name = safe_display_name(document_file.filename if document_file else None)
    stored_file = None
    if document_file and original_document_name:
        try:
            stored_file = store_document_file(document_file)
        except ValueError as exc:
            return redirect_with_message('/cases/new', validation=f'Документ не загружен: {exc}')
    country = (country_of_occurrence or "").strip().upper() or None
    reporter_country = (reporter_country_code or "").strip().upper() or None
    try:
        case = crud.create_manual_case(
            db,
            schemas.CaseCreate(
                case_number=case_number,
                worldwide_case_id=worldwide_case_id,
                safety_report_id=safety_report_id,
                partner_id=partner_id,
                case_type=case_type,
                report_type=report_type,
                initial_received_date=initial_received_date or None,
                latest_received_date=latest_received_date or None,
                country_of_occurrence=country,
                listedness=listedness,
                expectedness=expectedness,
                case_outcome=case_outcome,
                narrative=narrative,
                workflow_status=workflow_status,
                due_date=due_date or None,
            ),
            reporter_name=reporter_name,
            reporter_email=reporter_email,
            reporter_country_code=reporter_country,
            source_type=source_type,
            patient_payload=schemas.PatientCreate(
                patient_initials=patient_initials,
                patient_identifier=patient_identifier,
                sex=sex,
                date_of_birth=date_of_birth or None,
                age_value=age_value,
                age_unit=age_unit,
                weight_kg=weight_kg,
                height_cm=height_cm,
                pregnancy_status=pregnancy_status,
                medical_history_text=medical_history_text,
            ),
            product_payload=schemas.CaseProductCreate(
                product_id=product_id,
                reported_product_name=reported_product_name,
                active_substance_text=active_substance_text,
                drug_role=drug_role,
                indication_text=indication_text,
                dose_value=dose_value,
                dose_unit=dose_unit,
                route=route,
                frequency=frequency,
                therapy_start_date=therapy_start_date or None,
                therapy_end_date=therapy_end_date or None,
                action_taken=action_taken,
                batch_lot_number=batch_lot_number,
            ),
            reaction_payload=schemas.ReactionCreate(
                reported_term=reported_term or "",
                verbatim_term=verbatim_term,
                meddra_pt_code=meddra_pt_code,
                meddra_pt_name=meddra_pt_name,
                onset_date=onset_date or None,
                end_date=end_date or None,
                outcome=reaction_outcome,
                is_serious=is_serious,
                seriousness_death=seriousness_death,
                seriousness_life_threatening=seriousness_life_threatening,
                seriousness_hospitalization=seriousness_hospitalization,
                seriousness_disability=seriousness_disability,
                seriousness_congenital_anomaly=seriousness_congenital_anomaly,
                seriousness_other_medically_important=seriousness_other_medically_important,
            ),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/cases/new", validation=str(exc))
    except IntegrityError:
        db.rollback()
        return redirect_with_message("/cases/new", error="Case could not be saved.")

    if stored_file is not None:
        current_user = getattr(request.state, "current_user", None)
        crud.create_attachment(
            db,
            file_name=original_document_name,
            attachment_type=document_type or "other",
            document_title=(document_title or "").strip() or original_document_name,
            document_type=document_type or "other",
            case_id=case.id,
            mime_type=document_file.content_type or "application/octet-stream",
            file_size_bytes=stored_file["file_size_bytes"],
            storage_path=stored_file["storage_path"],
            status="draft",
            comment=(document_comment or "").strip() or None,
            checksum_sha256=stored_file["checksum_sha256"],
            uploaded_by_user_id=current_user.id if current_user else None,
        )

    target_tab = "assessment" if next_step == "assessment" else "overview"
    return redirect_with_message(
        f"/cases/{case.id}?tab={target_tab}",
        message="Manual case saved.",
    )


@router.get("/cases/{case_id}", response_class=HTMLResponse)
def case_detail(case_id: str, request: Request, audit_page: int = 1, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    audit_entries, audit_pagination = ([], None)
    if "audit_view" in request.state.permission_codes:
        audit_entries, audit_pagination = paginate_query(crud.case_audit_query(db, case.id), audit_page, 25)
    causality_assessments = crud.list_case_causality_assessments(db, case)
    current_user = getattr(request.state, "current_user", None)
    return templates.TemplateResponse(
        request,
        "case_detail.html",
        {
            "request": request,
            "case": case,
            "products": crud.list_products(db),
            "partners": crud.list_partners(db),
            "audit_entries": audit_entries,
            "audit_pagination": audit_pagination,
            "causality_assessments": causality_assessments,
            "who_umc_categories": WHO_UMC_CATEGORIES,
            "who_umc_descriptions": WHO_UMC_DESCRIPTIONS,
            "naranjo_questions": NARANJO_QUESTIONS,
            "assessor_roles": ASSESSOR_ROLES,
            "assessor_role_labels": dict(ASSESSOR_ROLES),
            "default_assessor_name": (
                current_user.full_name or current_user.email if current_user else ""
            ),
            "default_assessor_role": "ars" if current_user else None,
            "causality_result_labels": CAUSALITY_RESULT_LABELS,
            "naranjo_answers_by_assessment": {
                assessment.id: json.loads(assessment.naranjo_answers_json)
                for assessment in causality_assessments
                if assessment.naranjo_answers_json
            },
            "today": date.today(),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
            "active_page": "cases",
        },
    )


@router.post(
    "/cases/{case_id}/numbering-partner",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
@atomic_write
def assign_numbering_partner_form(
    case_id: str,
    request: Request,
    partner_id: str = Form(...),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    partner = crud.get_partner(db, partner_id)
    if not case or not partner:
        return redirect_with_message(
            f"/cases/{case_id}?tab=overview",
            validation="Случай или партнёр не найден.",
        )
    current_user = getattr(request.state, "current_user", None)
    try:
        finalize_provisional_number(
            db,
            case,
            partner,
            actor_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(
            f"/cases/{case_id}?tab=overview",
            validation=str(exc),
        )
    return redirect_with_message(
        f"/cases/{case_id}?tab=overview",
        message="Партнёр нумерации назначен. Предварительный номер сохранён в истории.",
    )


@router.post(
    "/cases/{case_id}/numbering-partner/correct",
    dependencies=[Depends(require_permission("manage_reference_data"))],
)
@atomic_write
def correct_numbering_partner_form(
    case_id: str,
    request: Request,
    partner_id: str = Form(...),
    reason: str = Form(...),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    partner = crud.get_partner(db, partner_id)
    if not case or not partner:
        return redirect_with_message(
            f"/cases/{case_id}?tab=overview",
            validation="Случай или партнёр не найден.",
        )
    current_user = getattr(request.state, "current_user", None)
    try:
        correct_numbering_partner(
            db,
            case,
            partner,
            reason=reason,
            actor_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(
            f"/cases/{case_id}?tab=overview",
            validation=str(exc),
        )
    return redirect_with_message(
        f"/cases/{case_id}?tab=overview",
        message="Партнёр нумерации исправлен с записью причины в журнал аудита.",
    )


@router.post(
    "/cases/{case_id}/partners",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
@atomic_write
def add_case_partner_form(
    case_id: str,
    partner_id: str = Form(...),
    relationship_type: str = Form("related"),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    partner = crud.get_partner(db, partner_id)
    if not case or not partner:
        return redirect_with_message(
            f"/cases/{case_id}?tab=overview",
            validation="Случай или партнёр не найден.",
        )
    if any(link.partner_id == partner.id for link in case.partner_links):
        return redirect_with_message(
            f"/cases/{case_id}?tab=overview",
            validation="Этот партнёр уже связан со случаем.",
        )
    crud.add_case_partner(
        db,
        case,
        partner,
        relationship_type=relationship_type,
    )
    return redirect_with_message(
        f"/cases/{case_id}?tab=overview",
        message="Связанный партнёр добавлен. Номер случая не изменён.",
    )


@router.post(
    "/cases/{case_id}/identifiers",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
@atomic_write(commit_validation=True)
def add_case_identifier_form(
    case_id: str,
    identifier_value: str = Form(...),
    partner_id: str | None = Form(None),
    organization_name: str | None = Form(None),
    identifier_type: str = Form("partner_case_number"),
    received_date: date | None = Form(None),
    is_primary: bool = Form(False),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        return redirect_with_message("/cases", validation="Случай не найден.")
    try:
        _, possible_duplicate = crud.create_case_identifier(
            db,
            case,
            schemas.CaseIdentifierCreate(
                identifier_value=identifier_value,
                partner_id=partner_id or None,
                organization_name=organization_name,
                identifier_type=identifier_type,
                received_date=received_date,
                is_primary=is_primary,
            ),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(
            f"/cases/{case_id}?tab=overview",
            validation=str(exc),
        )
    if possible_duplicate:
        return redirect_with_message(
            f"/cases/{case_id}?tab=overview",
            validation=(
                "Дополнительный номер сохранён. Возможный дубликат: такой номер уже "
                "используется у выбранного партнёра. Случаи не объединялись."
            ),
        )
    return redirect_with_message(
        f"/cases/{case_id}?tab=overview",
        message="Дополнительный номер сохранён.",
    )


@router.post(
    "/cases/{case_id}/status",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def change_case_status_form(
    case_id: str,
    workflow_status: str = Form(...),
    expected_version: int = Form(..., gt=0),
    change_reason: str | None = Form(None),
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.update_case_status(
            db,
            case,
            schemas.CaseStatusUpdate(workflow_status=workflow_status, change_reason=change_reason, expected_version=expected_version),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}?tab=overview", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}?tab=overview", message="Case saved.")


@router.post(
    "/cases/{case_id}/assessments",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
@atomic_write
def add_causality_assessment_form(
    case_id: str,
    case_product_id: str = Form(...),
    reaction_id: str = Form(...),
    causality_method: str = Form(...),
    relatedness: str | None = Form(None),
    assessor_role: str = Form(...),
    assessor_name: str = Form(...),
    assessor_organization: str | None = Form(None),
    assessment_date: str | None = Form(None),
    assessment_comment: str | None = Form(None),
    q1: str | None = Form(None),
    q2: str | None = Form(None),
    q3: str | None = Form(None),
    q4: str | None = Form(None),
    q5: str | None = Form(None),
    q6: str | None = Form(None),
    q7: str | None = Form(None),
    q8: str | None = Form(None),
    q9: str | None = Form(None),
    q10: str | None = Form(None),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    method = (causality_method or "").strip().lower()
    naranjo_answers = {}
    if method == "naranjo":
        naranjo_answers = {
            "q1": q1 or "",
            "q2": q2 or "",
            "q3": q3 or "",
            "q4": q4 or "",
            "q5": q5 or "",
            "q6": q6 or "",
            "q7": q7 or "",
            "q8": q8 or "",
            "q9": q9 or "",
            "q10": q10 or "",
        }
    try:
        assessment = crud.add_causality_assessment(
            db,
            case,
            schemas.CausalityAssessmentCreate(
                case_product_id=case_product_id,
                reaction_id=reaction_id,
                causality_method=method,
                relatedness=relatedness or "",
                naranjo_answers=naranjo_answers,
                assessor_role=assessor_role,
                assessor_name=assessor_name,
                assessor_organization=assessor_organization,
                assessment_date=assessment_date or date.today(),
                assessment_comment=assessment_comment,
            ),
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(
            f"/cases/{case_id}?tab=assessment",
            validation=str(exc),
        )
    return redirect_with_message(
        f"/cases/{case_id}?tab=assessment",
        message=(
            f"Causality assessment saved: {assessment.relatedness}"
            + (
                f" (Naranjo score {assessment.naranjo_score})"
                if assessment.naranjo_score is not None
                else ""
            )
            + "."
        ),
    )


@router.post("/cases/{case_id}/delete", dependencies=[Depends(require_permission("soft_delete"))])
@atomic_write
def delete_case_form(
    case_id: str,
    request: Request,
    delete_reason: str | None = Form(None),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        return redirect_with_message("/cases", error="Case not found.")
    current_user = getattr(request.state, "current_user", None)
    try:
        crud.delete_case(
            db,
            case,
            deleted_by_user_id=current_user.id if current_user else None,
            delete_reason=delete_reason,
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}", validation=str(exc))
    return redirect_with_message("/cases", message="Case deleted.")


@router.post("/cases/{case_id}/patients", dependencies=[Depends(require_permission("create"))])
@atomic_write
def add_patient_form(
    case_id: str,
    patient_initials: str | None = Form(None),
    patient_identifier: str | None = Form(None),
    sex: str | None = Form(None),
    age_value: float | None = Form(None),
    age_unit: str | None = Form("years"),
    weight_kg: float | None = Form(None),
    height_cm: float | None = Form(None),
    pregnancy_status: str | None = Form(None),
    medical_history_text: str | None = Form(None),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.add_patient(
            db,
            case,
            schemas.PatientCreate(
                patient_initials=patient_initials,
                patient_identifier=patient_identifier,
                sex=sex,
                age_value=age_value,
                age_unit=age_unit,
                weight_kg=weight_kg,
                height_cm=height_cm,
                pregnancy_status=pregnancy_status,
                medical_history_text=medical_history_text,
            ),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}?tab=patient", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}?tab=patient", message="Patient saved.")


@router.post("/cases/{case_id}/products", dependencies=[Depends(require_permission("create"))])
@atomic_write
def add_case_product_form(
    case_id: str,
    product_id: str | None = Form(None),
    reported_product_name: str | None = Form(None),
    active_substance_text: str | None = Form(None),
    drug_role: str = Form("suspect"),
    dose_value: str | None = Form(None),
    dose_unit: str | None = Form(None),
    route: str | None = Form(None),
    frequency: str | None = Form(None),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.add_case_product(
            db,
            case,
            schemas.CaseProductCreate(
                product_id=product_id,
                reported_product_name=reported_product_name,
                active_substance_text=active_substance_text,
                drug_role=drug_role,
                dose_value=dose_value,
                dose_unit=dose_unit,
                route=route,
                frequency=frequency,
            ),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}?tab=product", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}?tab=product", message="Product saved.")


@router.post("/cases/{case_id}/reactions", dependencies=[Depends(require_permission("create"))])
@atomic_write
def add_reaction_form(
    case_id: str,
    reported_term: str = Form(...),
    verbatim_term: str | None = Form(None),
    meddra_pt_code: str | None = Form(None),
    meddra_pt_name: str | None = Form(None),
    outcome: str | None = Form(None),
    is_serious: bool = Form(False),
    seriousness_death: bool = Form(False),
    seriousness_life_threatening: bool = Form(False),
    seriousness_hospitalization: bool = Form(False),
    seriousness_disability: bool = Form(False),
    seriousness_congenital_anomaly: bool = Form(False),
    seriousness_other_medically_important: bool = Form(False),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.add_reaction(
            db,
            case,
            schemas.ReactionCreate(
                reported_term=reported_term,
                verbatim_term=verbatim_term,
                meddra_pt_code=meddra_pt_code,
                meddra_pt_name=meddra_pt_name,
                outcome=outcome,
                is_serious=is_serious,
                seriousness_death=seriousness_death,
                seriousness_life_threatening=seriousness_life_threatening,
                seriousness_hospitalization=seriousness_hospitalization,
                seriousness_disability=seriousness_disability,
                seriousness_congenital_anomaly=seriousness_congenital_anomaly,
                seriousness_other_medically_important=seriousness_other_medically_important,
            ),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}?tab=reactions", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}?tab=reactions", message="Reaction saved.")


@router.post("/cases/{case_id}/followups", dependencies=[Depends(require_permission("create"))])
@atomic_write
def add_followup_form(
    case_id: str,
    received_date: str | None = Form(None),
    source_type: str | None = Form(None),
    message_direction: ResolvedMessageDirection = Form(...),
    description: str | None = Form(None),
    significant_new_information: bool = Form(False),
    expected_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.add_followup(
            db,
            case,
            schemas.FollowUpCreate(
                received_date=received_date or None,
                source_type=source_type,
                message_direction=message_direction,
                description=description,
                significant_new_information=significant_new_information,
            ),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}?tab=history", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}?tab=history", message="Follow-up saved.")


@router.post(
    "/cases/{case_id}/followups/{followup_id}/direction",
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
@atomic_write
def update_followup_direction_form(
    case_id: str,
    followup_id: str,
    message_direction: ResolvedMessageDirection = Form(...),
    expected_version: int = Form(..., gt=0),
    expected_followup_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    followup = crud.get_followup(db, followup_id)
    if not followup or followup.case_id != case_id:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    protect_record(db, models.FollowUp, followup_id, expected_followup_version)
    crud.update_followup_direction(
        db,
        followup,
        schemas.MessageDirectionUpdate(message_direction=message_direction),
    )
    return redirect_with_message(
        f"/cases/{case_id}?tab=followup",
        message="Направление follow-up сохранено.",
    )


@router.post("/cases/{case_id}/submissions", dependencies=[Depends(require_permission("create"))])
def add_submission_form(
    case_id: str,
    expected_object_version: int = Form(..., gt=0),
    recipient_partner_id: str | None = Form(None),
    recipient_type: str = Form("partner"),
    submission_type: str | None = Form("icsr"),
    submission_format: str | None = Form("email"),
    submission_status: str = Form("planned"),
    due_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        crud.create_submission_for_case(
            db,
            case,
            schemas.SubmissionCreateRequest(
                expected_object_version=expected_object_version,
                recipient_partner_id=recipient_partner_id,
                recipient_type=recipient_type,
                submission_type=submission_type,
                submission_format=submission_format,
                submission_status=submission_status,
                due_date=due_date or None,
            ),
        )
    except ValueError as exc:
        return redirect_with_message(f"/cases/{case_id}?tab=documents", validation=str(exc))
    return redirect_with_message(f"/cases/{case_id}?tab=documents", message="Submission saved.")


@router.get("/api/cases/export.csv", dependencies=[Depends(require_permission("export"))])
def api_export_cases_csv(
    dosage_form_language: str = "ru",
    partner_id: str | None = None,
    db: Session = Depends(get_db),
):
    return Response(
        crud.export_cases_csv(
            db,
            dosage_form_language=dosage_form_language,
            partner_id=partner_id,
        ),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=case_line_listing.csv"},
    )


@router.get("/api/cases/export.xlsx", dependencies=[Depends(require_permission("export"))])
def api_export_cases_xlsx(
    dosage_form_language: str = "ru",
    partner_id: str | None = None,
    db: Session = Depends(get_db),
):
    return Response(
        crud.export_cases_xlsx(
            db,
            dosage_form_language=dosage_form_language,
            partner_id=partner_id,
        ),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": "attachment; filename=case_line_listing.xlsx"},
    )


@router.get("/api/cases", response_model=list[schemas.CaseRead])
def api_list_cases(page: int = 1, per_page: int | None = None, search: str | None = None,
                   status_filter: str | None = None, partner_id: str | None = None,
                   product_id: str | None = None, date_from: date | None = None,
                   date_to: date | None = None, db: Session = Depends(get_db)):
    rows, _ = paginate_query(crud.filtered_cases_query(
        db, search=search, status_filter=status_filter, partner_id=partner_id,
        product_id=product_id, date_from=date_from, date_to=date_to,
    ), page, per_page)
    return rows


@router.post(
    "/api/cases",
    response_model=schemas.CaseRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("create"))],
)
@atomic_write
def api_create_case(payload: schemas.CaseCreate, expected_report_version: int | None = Query(None, gt=0), db: Session = Depends(get_db)):
    if payload.safety_report_id:
        if expected_report_version is None:
            raise HTTPException(status_code=422, detail="Expected report version is required")
        protect_record(db, models.SafetyReport, payload.safety_report_id, expected_report_version)
    return crud.create_case(db, payload)


@router.get("/api/cases/{case_id}", response_model=schemas.CaseRead)
def api_get_case(case_id: str, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.post(
    "/api/cases/{case_id}/numbering-partner",
    response_model=schemas.CaseRead,
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
@atomic_write
def api_assign_numbering_partner(
    case_id: str,
    payload: schemas.CaseNumberingPartnerUpdate,
    request: Request,
    expected_version: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    partner = crud.get_partner(db, payload.partner_id)
    if not case or not partner:
        raise HTTPException(status_code=404, detail="Case or partner not found")
    current_user = getattr(request.state, "current_user", None)
    try:
        return finalize_provisional_number(
            db,
            case,
            partner,
            actor_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/numbering-partner/correct",
    response_model=schemas.CaseRead,
    dependencies=[Depends(require_permission("manage_reference_data"))],
)
@atomic_write
def api_correct_numbering_partner(
    case_id: str,
    payload: schemas.CaseNumberingPartnerUpdate,
    request: Request,
    expected_version: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    partner = crud.get_partner(db, payload.partner_id)
    if not case or not partner:
        raise HTTPException(status_code=404, detail="Case or partner not found")
    current_user = getattr(request.state, "current_user", None)
    try:
        return correct_numbering_partner(
            db,
            case,
            partner,
            reason=payload.reason or "",
            actor_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/partners",
    response_model=schemas.CasePartnerLinkRead,
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
@atomic_write
def api_add_case_partner(
    case_id: str,
    payload: schemas.CasePartnerLinkCreate,
    expected_version: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    partner = crud.get_partner(db, payload.partner_id)
    if not case or not partner:
        raise HTTPException(status_code=404, detail="Case or partner not found")
    if any(link.partner_id == partner.id for link in case.partner_links):
        raise HTTPException(status_code=409, detail="Partner is already linked to the case")
    return crud.add_case_partner(
        db,
        case,
        partner,
        relationship_type=payload.relationship_type,
    )


@router.post(
    "/api/cases/{case_id}/identifiers",
    response_model=schemas.CaseIdentifierRead,
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
@atomic_write
def api_add_case_identifier(
    case_id: str,
    payload: schemas.CaseIdentifierCreate,
    response: Response,
    expected_version: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        identifier, possible_duplicate = crud.create_case_identifier(db, case, payload)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if possible_duplicate:
        response.headers["Warning"] = (
            '299 - "Possible duplicate external identifier for this partner"'
        )
    return identifier


@router.get("/api/cases/{case_id}/overview", response_model=schemas.CaseOverview)
def api_get_case_overview(case_id: str, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return crud.case_overview(case)


@router.patch(
    "/api/cases/{case_id}/status",
    response_model=schemas.CaseRead,
    dependencies=[Depends(require_any_permission("edit", "icsr_workflow"))],
)
def api_update_case_status(
    case_id: str,
    payload: schemas.CaseStatusUpdate,
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.update_case_status(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/patients",
    response_model=schemas.PatientRead,
    dependencies=[Depends(require_permission("create"))],
)
@atomic_write
def api_add_patient(
    case_id: str,
    payload: schemas.PatientCreate,
    expected_version: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.add_patient(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/products",
    response_model=schemas.CaseProductRead,
    dependencies=[Depends(require_permission("create"))],
)
@atomic_write
def api_add_case_product(
    case_id: str,
    payload: schemas.CaseProductCreate,
    expected_version: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.add_case_product(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/reactions",
    response_model=schemas.ReactionRead,
    dependencies=[Depends(require_permission("create"))],
)
@atomic_write
def api_add_reaction(
    case_id: str,
    payload: schemas.ReactionCreate,
    expected_version: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.add_reaction(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/followups",
    response_model=schemas.FollowUpRead,
    dependencies=[Depends(require_permission("create"))],
)
@atomic_write
def api_add_followup(
    case_id: str,
    payload: schemas.FollowUpCreate,
    expected_version: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Case, case_id, expected_version)
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.add_followup(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/cases/{case_id}/submissions",
    response_model=schemas.SubmissionRead,
    dependencies=[Depends(require_permission("create"))],
)
def api_add_submission(
    case_id: str,
    payload: schemas.SubmissionCreateRequest,
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return crud.create_submission_for_case(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
