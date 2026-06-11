from datetime import date, timedelta

from app import crud, schemas
from app.database import SessionLocal, init_db
from app.models import Case, Patient, Partner, Product, Reaction, SafetyReport, Submission, Substance


def run_seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        crud.create_user(db, "admin@example.com", "PV Admin", "admin")

        partner = db.query(Partner).filter(Partner.partner_code == "PARTNER-001").first()
        if not partner:
            partner = crud.create_partner(
                db,
                schemas.PartnerCreate(
                    partner_code="PARTNER-001",
                    partner_name="Global Pharma Partner Ltd.",
                    partner_type="mah",
                    country_code="DE",
                    region="EU",
                    email="pv@example-partner.test",
                    pv_responsible_person="PV Responsible Person",
                    sdea_required=True,
                ),
            )

        substance = db.query(Substance).filter(Substance.substance_name == "Ibuprofen").first()
        if not substance:
            substance = crud.create_substance(
                db,
                schemas.SubstanceCreate(
                    substance_name="Ibuprofen",
                    substance_name_normalized="ibuprofen",
                    inn_name="Ibuprofen",
                    atc_code="M01AE01",
                    substance_type="active",
                ),
            )

        product = db.query(Product).filter(Product.product_code == "PROD-IBU-200").first()
        if not product:
            product = crud.create_product(
                db,
                schemas.ProductCreate(
                    product_code="PROD-IBU-200",
                    product_name="IbuRelief 200 mg Tablets",
                    dosage_form="tablet",
                    strength="200 mg",
                    route="oral",
                    mah_partner_id=partner.id,
                    authorization_country_code="DE",
                    authorization_status="authorized",
                    is_company_product=True,
                    active_substance="Ibuprofen",
                ),
            )

        report = (
            db.query(SafetyReport)
            .filter(SafetyReport.safety_report_number == "SR-2026-0001")
            .first()
        )
        if not report:
            report = crud.create_safety_report(
                db,
                schemas.SafetyReportCreate(
                    safety_report_number="SR-2026-0001",
                    source_type="email",
                    partner_id=partner.id,
                    reporter_name="Initial Reporter",
                    reporter_email="reporter@example.test",
                    reporter_country_code="DE",
                    raw_subject="Rash and swelling after IbuRelief",
                    raw_text="Patient reported rash and swelling after taking IbuRelief.",
                ),
            )

        if report.triage_status != "converted_to_case":
            report = crud.triage_safety_report(
                db,
                report,
                schemas.TriageUpdate(
                    triage_status="valid_icsr",
                    triage_comment="Four minimum criteria present.",
                    is_valid_icsr=True,
                    minimum_criteria_patient=True,
                    minimum_criteria_reporter=True,
                    minimum_criteria_product=True,
                    minimum_criteria_event=True,
                ),
            )

        case = db.query(Case).filter(Case.case_number == "CASE-2026-0001").first()
        if not case:
            case = crud.create_case(
                db,
                schemas.CaseCreate(
                    case_number="CASE-2026-0001",
                    safety_report_id=report.id,
                    partner_id=partner.id,
                    case_type="spontaneous",
                    report_type="spontaneous",
                    initial_received_date=report.received_date or date.today(),
                    latest_received_date=report.received_date or date.today(),
                    country_of_occurrence="DE",
                    seriousness="non-serious",
                    narrative=report.raw_text,
                    workflow_status="data_entry",
                    due_date=date.today() + timedelta(days=15),
                ),
            )

        has_patient = db.query(Patient).filter(Patient.case_id == case.id).first()
        if not has_patient:
            crud.add_patient(
                db,
                case,
                schemas.PatientCreate(
                    sex="female",
                    age_value=42,
                    age_unit="years",
                ),
            )

        if not case.case_products:
            crud.add_case_product(
                db,
                case,
                schemas.CaseProductCreate(
                    product_id=product.id,
                    reported_product_name="IbuRelief",
                    active_substance_text="Ibuprofen",
                    drug_role="suspect",
                    dose_value="200",
                    dose_unit="mg",
                    route="oral",
                ),
            )

        has_reaction = db.query(Reaction).filter(Reaction.case_id == case.id).first()
        if not has_reaction:
            crud.add_reaction(
                db,
                case,
                schemas.ReactionCreate(
                    reported_term="Rash",
                    verbatim_term="rash and swelling",
                    outcome="recovering",
                    is_serious=False,
                ),
            )

        has_submission = db.query(Submission).filter(Submission.case_id == case.id).first()
        if not has_submission:
            crud.create_submission_for_case(
                db,
                case,
                schemas.SubmissionCreate(
                    recipient_partner_id=partner.id,
                    recipient_type="partner",
                    submission_type="icsr",
                    submission_format="email",
                    submission_status="planned",
                    due_date=date.today() + timedelta(days=7),
                ),
            )
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()
    print("Seed data created.")
