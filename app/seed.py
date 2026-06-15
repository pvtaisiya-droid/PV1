from datetime import date, timedelta

from app import crud, schemas
from app.database import SessionLocal, init_db
from app.models import (
    Case,
    ContractContact,
    FollowUp,
    Partner,
    Patient,
    Product,
    Reaction,
    SafetyReport,
    Submission,
    Substance,
)


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
                    partner_type="fn",
                    reconciliation_frequency="monthly",
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

        demo_partners = [
            ("PARTNER-002", "Northbridge Safety Partner", "quarterly"),
            ("PARTNER-003", "Eastline PV Affiliate", "monthly"),
        ]
        partner_map = {partner.partner_code: partner}
        for code, name, frequency in demo_partners:
            existing_partner = db.query(Partner).filter(Partner.partner_code == code).first()
            if not existing_partner:
                existing_partner = crud.create_partner(
                    db,
                    schemas.PartnerCreate(
                        partner_code=code,
                        partner_name=name,
                        partner_type="fn",
                        reconciliation_frequency=frequency,
                    ),
                )
            partner_map[code] = existing_partner

        contact_specs = [
            (partner.partner_code, "Muller", "Anna", "pv.muller@example.test", "PV contact"),
            ("PARTNER-002", "Smith", "John", "john.smith@example.test", "Safety manager"),
            ("PARTNER-003", "Petrova", "Maria", "m.petrova@example.test", "PV officer"),
        ]
        for partner_code, last_name, first_name, email, position in contact_specs:
            contact_partner = partner_map.get(partner_code)
            if not contact_partner:
                continue
            exists = db.query(ContractContact).filter(ContractContact.email == email).first()
            if not exists:
                crud.create_contract_contact(
                    db,
                    schemas.ContractContactCreate(
                        partner_id=contact_partner.id,
                        last_name=last_name,
                        first_name=first_name,
                        email=email,
                        position=position,
                        is_current=True,
                    ),
                )

        product_specs = [
            ("PROD-PARA-500", "ParaRelief 500 mg Tablets", "Paracetamol", partner_map["PARTNER-002"]),
            ("PROD-LORA-10", "LoraCalm 10 mg Tablets", "Loratadine", partner_map["PARTNER-003"]),
        ]
        product_map = {product.product_code: product}
        for product_code, product_name, substance_name, product_partner in product_specs:
            existing_product = db.query(Product).filter(Product.product_code == product_code).first()
            if not existing_product:
                existing_product = crud.create_product(
                    db,
                    schemas.ProductCreate(
                        product_code=product_code,
                        product_name=product_name,
                        dosage_form="tablet",
                        strength=product_name.split()[1] if len(product_name.split()) > 1 else None,
                        route="oral",
                        mah_partner_id=product_partner.id,
                        authorization_status="authorized",
                        is_company_product=True,
                        active_substance=substance_name,
                    ),
                )
            product_map[product_code] = existing_product

        demo_cases = [
            (
                "SR-2026-0002",
                "CASE-2026-0002",
                partner_map["PARTNER-002"],
                product_map["PROD-PARA-500"],
                "Nausea after ParaRelief",
                "Patient reported nausea after ParaRelief.",
                "Nausea",
            ),
            (
                "SR-2026-0003",
                "CASE-2026-0003",
                partner_map["PARTNER-002"],
                product_map["PROD-PARA-500"],
                "Nausea after ParaRelief duplicate",
                "Patient reported nausea after ParaRelief.",
                "Nausea",
            ),
            (
                "SR-2026-0004",
                "CASE-2026-0004",
                partner_map["PARTNER-003"],
                product_map["PROD-LORA-10"],
                "Somnolence after LoraCalm",
                "Patient reported somnolence after LoraCalm.",
                "Somnolence",
            ),
            (
                "SR-2026-0005",
                "CASE-2026-0005",
                partner,
                product,
                "Swelling after IbuRelief",
                "Patient reported swelling after IbuRelief.",
                "Swelling",
            ),
        ]
        for report_number, case_number, case_partner, case_product, subject, raw_text, reaction in demo_cases:
            demo_report = db.query(SafetyReport).filter(
                SafetyReport.safety_report_number == report_number
            ).first()
            if not demo_report:
                demo_report = crud.create_safety_report(
                    db,
                    schemas.SafetyReportCreate(
                        safety_report_number=report_number,
                        source_type="email",
                        partner_id=case_partner.id,
                        reporter_name="Demo Reporter",
                        reporter_email="demo.reporter@example.test",
                        reporter_country_code="DE",
                        raw_subject=subject,
                        raw_text=raw_text,
                    ),
                )
                demo_report = crud.triage_safety_report(
                    db,
                    demo_report,
                    schemas.TriageUpdate(
                        triage_status="valid_icsr",
                        is_valid_icsr=True,
                        minimum_criteria_patient=True,
                        minimum_criteria_reporter=True,
                        minimum_criteria_product=True,
                        minimum_criteria_event=True,
                    ),
                )
            demo_case = db.query(Case).filter(Case.case_number == case_number).first()
            if not demo_case:
                demo_case = crud.create_case(
                    db,
                    schemas.CaseCreate(
                        case_number=case_number,
                        safety_report_id=demo_report.id,
                        partner_id=case_partner.id,
                        case_type="spontaneous",
                        report_type="spontaneous",
                        initial_received_date=date.today() - timedelta(days=20),
                        latest_received_date=date.today() - timedelta(days=20),
                        country_of_occurrence="DE",
                        seriousness="non-serious",
                        narrative=raw_text,
                        workflow_status="data_entry",
                    ),
                )
            if not demo_case.patients:
                crud.add_patient(db, demo_case, schemas.PatientCreate(age_value=35, age_unit="years"))
            if not demo_case.case_products:
                crud.add_case_product(
                    db,
                    demo_case,
                    schemas.CaseProductCreate(
                        product_id=case_product.id,
                        reported_product_name=case_product.product_name,
                        active_substance_text=", ".join(
                            link.substance.substance_name
                            for link in case_product.substance_links
                            if link.substance
                        ),
                        drug_role="suspect",
                        route="oral",
                    ),
                )
            if not db.query(Reaction).filter(Reaction.case_id == demo_case.id).first():
                crud.add_reaction(
                    db,
                    demo_case,
                    schemas.ReactionCreate(reported_term=reaction, is_serious=False),
                )
            if not db.query(Submission).filter(Submission.case_id == demo_case.id).first():
                crud.create_submission_for_case(
                    db,
                    demo_case,
                    schemas.SubmissionCreate(
                        recipient_partner_id=case_partner.id,
                        recipient_type="partner",
                        submission_status="planned",
                        due_date=date.today() - timedelta(days=10),
                    ),
                )

        followup_case = db.query(Case).filter(Case.case_number == "CASE-2026-0002").first()
        if followup_case and not db.query(FollowUp).filter(FollowUp.case_id == followup_case.id).first():
            crud.add_followup(
                db,
                followup_case,
                schemas.FollowUpCreate(
                    received_date=date.today() - timedelta(days=5),
                    source_type="partner",
                    description="Follow-up: nausea resolved without sequelae.",
                    significant_new_information=True,
                ),
            )

        partner_reports = [
            (
                "PARTNER-SR-2026-0002",
                partner_map["PARTNER-002"],
                "Partner reported nausea after ParaRelief.",
                "partner",
            ),
            (
                "PARTNER-SR-2026-9999",
                partner_map["PARTNER-002"],
                "Partner-only report: headache after ParaRelief.",
                "partner",
            ),
            (
                "PARTNER-SR-2026-INVALID",
                partner_map["PARTNER-003"],
                "Non-valid partner report without adverse event.",
                "partner",
            ),
        ]
        for report_number, report_partner, raw_text, source_type in partner_reports:
            partner_report = db.query(SafetyReport).filter(
                SafetyReport.safety_report_number == report_number
            ).first()
            if not partner_report:
                partner_report = crud.create_safety_report(
                    db,
                    schemas.SafetyReportCreate(
                        safety_report_number=report_number,
                        source_type=source_type,
                        partner_id=report_partner.id,
                        reporter_name="Partner Sender",
                        reporter_email="partner.sender@example.test",
                        reporter_country_code="DE",
                        raw_subject=raw_text[:80],
                        raw_text=raw_text,
                    ),
                )
                if "INVALID" in report_number:
                    crud.triage_safety_report(
                        db,
                        partner_report,
                        schemas.TriageUpdate(
                            triage_status="invalid",
                            triage_comment="Demo invalid partner report.",
                            is_valid_icsr=False,
                        ),
                    )
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()
    print("Seed data created.")
