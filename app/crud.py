import csv
import io
from datetime import date
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app import schemas
from app.audit import log_audit
from app.models import (
    AuditTrail,
    Case,
    CaseProduct,
    FollowUp,
    Partner,
    Patient,
    Product,
    ProductSubstance,
    Reaction,
    SafetyReport,
    Submission,
    Substance,
    User,
    utcnow,
)


def normalize_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value.lower() if value else None


def model_data(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return dict(payload)


def empty_to_none(value: Any) -> Any:
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def clean_form_data(data: dict[str, Any]) -> dict[str, Any]:
    return {key: empty_to_none(value) for key, value in data.items()}


def get_current_user(db: Session) -> User | None:
    return (
        db.query(User)
        .filter(User.is_deleted.is_(False))
        .order_by(User.created_at.asc())
        .first()
    )


def generate_number(db: Session, model: type, field_name: str, prefix: str) -> str:
    year = date.today().year
    stem = f"{prefix}-{year}-"
    existing_count = db.query(model).filter(getattr(model, field_name).like(f"{stem}%")).count()
    candidate_number = existing_count + 1
    while True:
        candidate = f"{stem}{candidate_number:04d}"
        exists = (
            db.query(model)
            .filter(getattr(model, field_name) == candidate)
            .first()
            is not None
        )
        if not exists:
            return candidate
        candidate_number += 1


def create_user(db: Session, email: str, full_name: str, role: str = "viewer") -> User:
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return existing
    user = User(email=email, full_name=full_name, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def list_partners(db: Session) -> list[Partner]:
    return db.query(Partner).filter(Partner.is_deleted.is_(False)).order_by(Partner.partner_name).all()


def get_partner(db: Session, partner_id: str) -> Partner | None:
    return db.query(Partner).filter(Partner.id == partner_id, Partner.is_deleted.is_(False)).first()


def create_partner(db: Session, payload: schemas.PartnerCreate) -> Partner:
    data = clean_form_data(model_data(payload))
    partner = Partner(**data)
    db.add(partner)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Partner",
        entity_id=partner.id,
        action="create",
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(partner)
    return partner


def list_substances(db: Session) -> list[Substance]:
    return (
        db.query(Substance)
        .filter(Substance.is_deleted.is_(False))
        .order_by(Substance.substance_name)
        .all()
    )


def get_substance(db: Session, substance_id: str) -> Substance | None:
    return db.query(Substance).filter(Substance.id == substance_id).first()


def get_or_create_substance(
    db: Session,
    name: str,
    *,
    atc_code: str | None = None,
    inn_name: str | None = None,
) -> Substance:
    normalized = normalize_text(name)
    substance = (
        db.query(Substance)
        .filter(Substance.substance_name_normalized == normalized)
        .first()
    )
    if substance:
        return substance
    substance = Substance(
        substance_name=name.strip(),
        substance_name_normalized=normalized,
        inn_name=inn_name,
        atc_code=atc_code,
        substance_type="active",
    )
    db.add(substance)
    db.flush()
    log_audit(
        db,
        entity_type="Substance",
        entity_id=substance.id,
        action="create",
        user_id=(get_current_user(db).id if get_current_user(db) else None),
    )
    return substance


def create_substance(db: Session, payload: schemas.SubstanceCreate) -> Substance:
    data = clean_form_data(model_data(payload))
    data["substance_name_normalized"] = data.get("substance_name_normalized") or normalize_text(
        data.get("substance_name")
    )
    substance = Substance(**data)
    db.add(substance)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Substance",
        entity_id=substance.id,
        action="create",
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(substance)
    return substance


def list_products(db: Session) -> list[Product]:
    return (
        db.query(Product)
        .options(joinedload(Product.substance_links).joinedload(ProductSubstance.substance))
        .filter(Product.is_deleted.is_(False))
        .order_by(Product.product_name)
        .all()
    )


def get_product(db: Session, product_id: str) -> Product | None:
    return db.query(Product).filter(Product.id == product_id).first()


def create_product(db: Session, payload: schemas.ProductCreate) -> Product:
    data = clean_form_data(model_data(payload))
    active_substance = data.pop("active_substance", None)
    data["product_name_normalized"] = data.get("product_name_normalized") or normalize_text(
        data.get("product_name")
    )
    product = Product(**data)
    db.add(product)
    db.flush()

    if active_substance:
        substance = get_or_create_substance(db, active_substance)
        link = ProductSubstance(
            product_id=product.id,
            substance_id=substance.id,
            substance_role="active",
            is_primary=True,
        )
        db.add(link)

    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Product",
        entity_id=product.id,
        action="create",
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(product)
    return product


def create_product_substance(
    db: Session,
    payload: schemas.ProductSubstanceCreate,
) -> ProductSubstance:
    link = ProductSubstance(**clean_form_data(model_data(payload)))
    db.add(link)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="ProductSubstance",
        entity_id=link.id,
        action="create",
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(link)
    return link


def list_safety_reports(db: Session) -> list[SafetyReport]:
    return (
        db.query(SafetyReport)
        .options(joinedload(SafetyReport.partner))
        .filter(SafetyReport.is_deleted.is_(False))
        .order_by(SafetyReport.received_at.desc())
        .all()
    )


def get_safety_report(db: Session, report_id: str) -> SafetyReport | None:
    return (
        db.query(SafetyReport)
        .options(
            joinedload(SafetyReport.partner),
            joinedload(SafetyReport.case),
        )
        .filter(SafetyReport.id == report_id)
        .first()
    )


def create_safety_report(db: Session, payload: schemas.SafetyReportCreate) -> SafetyReport:
    data = clean_form_data(model_data(payload))
    data["safety_report_number"] = data.get("safety_report_number") or generate_number(
        db,
        SafetyReport,
        "safety_report_number",
        "SR",
    )
    data["received_at"] = data.get("received_at") or utcnow()
    data["received_date"] = data.get("received_date") or date.today()
    report = SafetyReport(**data)
    db.add(report)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="SafetyReport",
        entity_id=report.id,
        action="create",
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(report)
    return report


def triage_safety_report(db: Session, report: SafetyReport, payload: schemas.TriageUpdate) -> SafetyReport:
    data = clean_form_data(model_data(payload))
    old_status = report.triage_status
    report.triage_status = data["triage_status"]
    report.triage_comment = data.get("triage_comment")
    report.is_valid_icsr = bool(data.get("is_valid_icsr"))
    report.minimum_criteria_patient = bool(data.get("minimum_criteria_patient"))
    report.minimum_criteria_reporter = bool(data.get("minimum_criteria_reporter"))
    report.minimum_criteria_product = bool(data.get("minimum_criteria_product"))
    report.minimum_criteria_event = bool(data.get("minimum_criteria_event"))
    user = get_current_user(db)
    report.triaged_by_user_id = user.id if user else None
    report.triaged_at = utcnow()
    report.version += 1
    log_audit(
        db,
        entity_type="SafetyReport",
        entity_id=report.id,
        action="triage",
        field_name="triage_status",
        old_value=old_status,
        new_value=report.triage_status,
        change_reason=data.get("change_reason"),
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(report)
    return report


def list_cases(db: Session) -> list[Case]:
    return (
        db.query(Case)
        .options(joinedload(Case.partner))
        .filter(Case.is_deleted.is_(False))
        .order_by(Case.created_at.desc())
        .all()
    )


def get_case(db: Session, case_id: str) -> Case | None:
    return (
        db.query(Case)
        .options(
            joinedload(Case.partner),
            joinedload(Case.safety_report),
            joinedload(Case.patients),
            joinedload(Case.case_products).joinedload(CaseProduct.product),
            joinedload(Case.reactions),
            joinedload(Case.followups),
            joinedload(Case.submissions).joinedload(Submission.recipient_partner),
            joinedload(Case.audit_entries).joinedload(AuditTrail.user),
        )
        .filter(Case.id == case_id)
        .first()
    )


def create_case(db: Session, payload: schemas.CaseCreate) -> Case:
    data = clean_form_data(model_data(payload))
    data["case_number"] = data.get("case_number") or generate_number(db, Case, "case_number", "CASE")
    case = Case(**data)
    db.add(case)
    db.flush()

    if case.safety_report_id:
        report = db.query(SafetyReport).filter(SafetyReport.id == case.safety_report_id).first()
        if report:
            report.case_id = case.id
            report.triage_status = "converted_to_case"

    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Case",
        entity_id=case.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(case)
    return case


def create_case_from_report(db: Session, report: SafetyReport) -> Case:
    if report.case:
        return report.case

    case = Case(
        case_number=generate_number(db, Case, "case_number", "CASE"),
        safety_report_id=report.id,
        partner_id=report.partner_id,
        case_type="spontaneous",
        report_type=report.source_type or "spontaneous",
        initial_received_date=report.received_date,
        latest_received_date=report.received_date,
        country_of_occurrence=report.reporter_country_code,
        seriousness="non-serious",
        narrative=report.raw_text,
        workflow_status="data_entry",
    )
    db.add(case)
    db.flush()
    report.case_id = case.id
    report.triage_status = "converted_to_case"
    report.is_valid_icsr = True
    report.version += 1
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Case",
        entity_id=case.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
        change_reason="Created from safety report",
    )
    log_audit(
        db,
        entity_type="SafetyReport",
        entity_id=report.id,
        action="status_change",
        field_name="triage_status",
        old_value="valid_icsr",
        new_value="converted_to_case",
        case_id=case.id,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(case)
    return case


def update_case_status(db: Session, case: Case, payload: schemas.CaseStatusUpdate) -> Case:
    old_status = case.workflow_status
    case.workflow_status = payload.workflow_status
    case.version += 1
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Case",
        entity_id=case.id,
        action="status_change",
        case_id=case.id,
        field_name="workflow_status",
        old_value=old_status,
        new_value=case.workflow_status,
        change_reason=payload.change_reason,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(case)
    return case


def add_patient(db: Session, case: Case, payload: schemas.PatientCreate) -> Patient:
    patient = Patient(case_id=case.id, **clean_form_data(model_data(payload)))
    db.add(patient)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Patient",
        entity_id=patient.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(patient)
    return patient


def add_case_product(db: Session, case: Case, payload: schemas.CaseProductCreate) -> CaseProduct:
    data = clean_form_data(model_data(payload))
    product = None
    if data.get("product_id"):
        product = get_product(db, data["product_id"])
    if product:
        data["reported_product_name"] = data.get("reported_product_name") or product.product_name
        data["route"] = data.get("route") or product.route
        if not data.get("active_substance_text") and product.substance_links:
            data["active_substance_text"] = ", ".join(
                link.substance.substance_name
                for link in product.substance_links
                if link.substance
            )
    case_product = CaseProduct(case_id=case.id, **data)
    db.add(case_product)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="CaseProduct",
        entity_id=case_product.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(case_product)
    return case_product


def add_reaction(db: Session, case: Case, payload: schemas.ReactionCreate) -> Reaction:
    reaction = Reaction(case_id=case.id, **clean_form_data(model_data(payload)))
    db.add(reaction)
    if reaction.is_serious:
        case.seriousness = "serious"
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Reaction",
        entity_id=reaction.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(reaction)
    return reaction


def add_followup(db: Session, case: Case, payload: schemas.FollowUpCreate) -> FollowUp:
    data = clean_form_data(model_data(payload))
    if not data.get("follow_up_number"):
        current_count = db.query(FollowUp).filter(FollowUp.case_id == case.id).count()
        data["follow_up_number"] = current_count + 1
    if data.get("received_date"):
        case.latest_received_date = data["received_date"]
    case.case_version += 1
    data["case_version_after_follow_up"] = data.get("case_version_after_follow_up") or case.case_version
    followup = FollowUp(case_id=case.id, **data)
    db.add(followup)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="FollowUp",
        entity_id=followup.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(followup)
    return followup


def validate_submission_references(data: dict[str, Any]) -> None:
    refs = [data.get("case_id"), data.get("pbrer_id"), data.get("rmp_id")]
    if sum(1 for ref in refs if ref) != 1:
        raise ValueError("Exactly one of case_id, pbrer_id, or rmp_id must be provided.")


def list_submissions(db: Session) -> list[Submission]:
    return (
        db.query(Submission)
        .options(joinedload(Submission.case), joinedload(Submission.recipient_partner))
        .filter(Submission.is_deleted.is_(False))
        .order_by(Submission.created_at.desc())
        .all()
    )


def get_submission(db: Session, submission_id: str) -> Submission | None:
    return db.query(Submission).filter(Submission.id == submission_id).first()


def create_submission(db: Session, payload: schemas.SubmissionCreate) -> Submission:
    data = clean_form_data(model_data(payload))
    validate_submission_references(data)
    data["submission_number"] = data.get("submission_number") or generate_number(
        db,
        Submission,
        "submission_number",
        "SUB",
    )
    if data.get("recipient_partner_id") and not data.get("recipient_country_code"):
        partner = get_partner(db, data["recipient_partner_id"])
        data["recipient_country_code"] = partner.country_code if partner else None
    submission = Submission(**data)
    db.add(submission)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Submission",
        entity_id=submission.id,
        action="create",
        case_id=submission.case_id,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(submission)
    return submission


def create_submission_for_case(
    db: Session,
    case: Case,
    payload: schemas.SubmissionCreate,
) -> Submission:
    data = model_data(payload)
    data["case_id"] = case.id
    data["submission_object_type"] = "case"
    return create_submission(db, schemas.SubmissionCreate(**data))


def update_submission_status(
    db: Session,
    submission: Submission,
    payload: schemas.SubmissionStatusUpdate,
) -> Submission:
    old_status = submission.submission_status
    submission.submission_status = payload.submission_status
    submission.error_message = payload.error_message
    if submission.submission_status == "submitted" and not submission.submitted_at:
        submission.submitted_at = utcnow()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Submission",
        entity_id=submission.id,
        action="status_change",
        case_id=submission.case_id,
        field_name="submission_status",
        old_value=old_status,
        new_value=submission.submission_status,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(submission)
    return submission


def dashboard_stats(db: Session) -> schemas.DashboardStats:
    today = date.today()
    total_safety_reports = db.query(SafetyReport).filter(SafetyReport.is_deleted.is_(False)).count()
    reports_awaiting_triage = (
        db.query(SafetyReport)
        .filter(
            SafetyReport.is_deleted.is_(False),
            SafetyReport.triage_status.in_(["new", "in_triage"]),
        )
        .count()
    )
    total_cases = db.query(Case).filter(Case.is_deleted.is_(False)).count()
    open_cases = (
        db.query(Case)
        .filter(
            Case.is_deleted.is_(False),
            Case.workflow_status.notin_(["submitted", "closed"]),
        )
        .count()
    )
    serious_cases = (
        db.query(Case)
        .filter(Case.is_deleted.is_(False), Case.seriousness == "serious")
        .count()
    )
    submissions_due = (
        db.query(Submission)
        .filter(
            Submission.is_deleted.is_(False),
            Submission.submission_status.in_(["planned", "ready"]),
            Submission.due_date.is_not(None),
        )
        .count()
    )
    overdue_submissions = (
        db.query(Submission)
        .filter(
            Submission.is_deleted.is_(False),
            Submission.submission_status.in_(["planned", "ready"]),
            Submission.due_date < today,
        )
        .count()
    )
    return schemas.DashboardStats(
        total_safety_reports=total_safety_reports,
        reports_awaiting_triage=reports_awaiting_triage,
        total_cases=total_cases,
        open_cases=open_cases,
        serious_cases=serious_cases,
        submissions_due=submissions_due,
        overdue_submissions=overdue_submissions,
    )


def case_overview(case: Case) -> schemas.CaseOverview:
    return schemas.CaseOverview(
        case=case,
        patients=case.patients,
        products=case.case_products,
        reactions=case.reactions,
        followups=case.followups,
        submissions=case.submissions,
    )


def export_cases_csv(db: Session) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "case number",
            "received date",
            "country",
            "seriousness",
            "product",
            "active substance",
            "reaction",
            "MedDRA PT",
            "outcome",
            "workflow status",
        ]
    )

    cases = (
        db.query(Case)
        .options(
            joinedload(Case.case_products).joinedload(CaseProduct.product),
            joinedload(Case.reactions),
        )
        .filter(Case.is_deleted.is_(False))
        .order_by(Case.created_at.desc())
        .all()
    )
    for case in cases:
        products = case.case_products or [None]
        reactions = case.reactions or [None]
        for product_row in products:
            for reaction in reactions:
                product_name = ""
                active_substance = ""
                if product_row:
                    product_name = product_row.reported_product_name or (
                        product_row.product.product_name if product_row.product else ""
                    )
                    active_substance = product_row.active_substance_text or ""
                writer.writerow(
                    [
                        case.case_number,
                        case.initial_received_date,
                        case.country_of_occurrence,
                        case.seriousness,
                        product_name,
                        active_substance,
                        reaction.reported_term if reaction else "",
                        reaction.meddra_pt_name or reaction.meddra_pt_code if reaction else "",
                        reaction.outcome if reaction else "",
                        case.workflow_status,
                    ]
                )
    return output.getvalue()
