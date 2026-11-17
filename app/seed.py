from datetime import date, timedelta

from app import crud, schemas
from app.database import SessionLocal, init_db
from app.models import (
    Case,
    Contract,
    ContractContact,
    FollowUp,
    Partner,
    PartnerReconciliation,
    Patient,
    Product,
    Reaction,
    SafetyReport,
    Submission,
    Substance,
)
from app.reconciliation import build_reconciliation_preview, contact_full_name, products_for_partner
from app.reconciliation_documents import generate_reconciliation_document_file


DEMO_PREPARED_BY = "PV Demo User"


def ensure_demo_partner(db, *, code: str, name: str, frequency: str) -> Partner:
    partner = db.query(Partner).filter(Partner.partner_code == code).first()
    if partner:
        partner.is_deleted = False
        partner.is_active = True
        partner.partner_name = name
        partner.reconciliation_frequency = frequency
        db.commit()
        db.refresh(partner)
        return partner
    return crud.create_partner(
        db,
        schemas.PartnerCreate(
            partner_code=code,
            partner_name=name,
            partner_type="fn",
            reconciliation_frequency=frequency,
        ),
    )


def ensure_demo_substance(db, *, name: str, inn: str, atc_code: str) -> Substance:
    substance = db.query(Substance).filter(Substance.substance_name == name).first()
    if substance:
        substance.is_deleted = False
        substance.is_active = True
        substance.substance_name_normalized = name.lower()
        substance.inn_name = inn
        substance.atc_code = atc_code
        substance.substance_type = "active"
        db.commit()
        db.refresh(substance)
        return substance
    return crud.create_substance(
        db,
        schemas.SubstanceCreate(
            substance_name=name,
            substance_name_normalized=name.lower(),
            inn_name=inn,
            atc_code=atc_code,
            substance_type="active",
        ),
    )


def ensure_demo_product(
    db,
    *,
    code: str,
    name: str,
    partner: Partner,
    substance: Substance,
    dosage_form_id: int,
    strength: str,
    auth_number: str,
    auth_country_code: str,
) -> Product:
    product = db.query(Product).filter(Product.product_code == code).first()
    if product:
        product.is_deleted = False
        product.is_active = True
        product.product_name = name
        product.dosage_form_id = dosage_form_id
        product.strength = strength
        product.route = "oral"
        product.mah_partner_id = partner.id
        product.authorization_number = auth_number
        product.authorization_country_code = auth_country_code
        product.authorization_status = "registration_active"
        db.commit()
        db.refresh(product)
        product = crud.set_product_active_substance(db, product, substance.id)
        return product
    return crud.create_product(
        db,
        schemas.ProductCreate(
            product_code=code,
            product_name=name,
            dosage_form_id=dosage_form_id,
            strength=strength,
            route="oral",
            mah_partner_id=partner.id,
            authorization_number=auth_number,
            authorization_country_code=auth_country_code,
            authorization_status="registration_active",
            is_company_product=True,
            active_substance_id=substance.id,
            comment="Seed demo product for partner reconciliation.",
        ),
    )


def ensure_demo_contact(
    db,
    *,
    partner: Partner,
    last_name: str,
    first_name: str,
    email: str,
    position: str,
    is_primary: bool = False,
    cc_reconciliation: bool = False,
) -> ContractContact:
    contact = (
        db.query(ContractContact)
        .filter(
            ContractContact.partner_id == partner.id,
            ContractContact.email == email,
            ContractContact.is_deleted.is_(False),
        )
        .first()
    )
    if contact:
        return contact
    return crud.create_contract_contact(
        db,
        schemas.ContractContactCreate(
            partner_id=partner.id,
            last_name=last_name,
            first_name=first_name,
            email=email,
            position=position,
            is_current=True,
            is_pv_contact=True,
            is_reconciliation_recipient=not cc_reconciliation,
            cc_reconciliation=cc_reconciliation,
            is_primary=is_primary,
            contact_type="pv",
            comments="Seed demo contact for partner reconciliation.",
        ),
    )


def ensure_demo_contract(
    db,
    *,
    partner: Partner,
    product: Product,
    number: str,
    contract_date: date,
    valid_until: date,
) -> Contract:
    contract = db.query(Contract).filter(Contract.contract_number == number).first()
    if contract:
        return contract
    existing_for_product = (
        db.query(Contract)
        .filter(
            Contract.partner_id == partner.id,
            Contract.product_id == product.id,
            Contract.contract_type == "pharmacovigilance_agreement",
            Contract.is_deleted.is_(False),
        )
        .first()
    )
    if existing_for_product:
        return existing_for_product
    return crud.create_contract(
        db,
        schemas.ContractCreate(
            partner_id=partner.id,
            product_id=product.id,
            product_ids=[product.id],
            contract_type="pharmacovigilance_agreement",
            contract_number=number,
            contract_date=contract_date,
            valid_until=valid_until,
        ),
    )


def ensure_demo_additional_agreement(
    db,
    *,
    parent_contract: Contract,
    number: str,
    contract_date: date,
    valid_until: date,
) -> Contract:
    agreement = (
        db.query(Contract)
        .filter(
            Contract.parent_contract_id == parent_contract.id,
            Contract.contract_number == number,
            Contract.is_deleted.is_(False),
        )
        .first()
    )
    if agreement:
        return agreement
    return crud.create_contract(
        db,
        schemas.ContractCreate(
            partner_id=parent_contract.partner_id,
            product_id=parent_contract.product_id,
            product_ids=parent_contract.product_ids,
            parent_contract_id=parent_contract.id,
            contract_type="additional_agreement",
            contract_number=number,
            contract_date=contract_date,
            valid_until=valid_until,
        ),
    )


def ensure_demo_icsr_case(
    db,
    *,
    report_number: str,
    case_number: str,
    partner: Partner,
    product: Product,
    received_date: date,
    subject: str,
    raw_text: str,
    reaction: str,
    country: str,
    seriousness: str = "non-serious",
) -> Case:
    report = db.query(SafetyReport).filter(SafetyReport.safety_report_number == report_number).first()
    if not report:
        report = crud.create_safety_report(
            db,
            schemas.SafetyReportCreate(
                safety_report_number=report_number,
                received_date=received_date,
                source_type="email",
                message_direction="needs_review",
                partner_id=partner.id,
                reporter_name=f"{partner.partner_name} Reporter",
                reporter_email=f"icsr.{partner.partner_code.lower()}@example.test",
                reporter_country_code=country,
                raw_subject=subject,
                raw_text=raw_text,
            ),
        )
        report = crud.triage_safety_report(
            db,
            report,
            schemas.TriageUpdate(
                triage_status="valid_icsr",
                triage_comment="Seed demo ICSR: four minimum criteria present.",
                is_valid_icsr=True,
                minimum_criteria_patient=True,
                minimum_criteria_reporter=True,
                minimum_criteria_product=True,
                minimum_criteria_event=True,
            ),
        )

    case = db.query(Case).filter(Case.case_number == case_number).first()
    if not case:
        case = crud.create_case(
            db,
            schemas.CaseCreate(
                case_number=case_number,
                worldwide_case_id=f"WW-{case_number}",
                safety_report_id=report.id,
                partner_id=partner.id,
                case_type="spontaneous",
                report_type="spontaneous",
                initial_received_date=received_date,
                latest_received_date=received_date,
                country_of_occurrence=country,
                seriousness=seriousness,
                narrative=raw_text,
                workflow_status="data_entry",
                due_date=received_date + timedelta(days=15),
            ),
        )
    if not case.patients:
        crud.add_patient(
            db,
            case,
            schemas.PatientCreate(
                patient_initials=f"PT-{case_number[-2:]}",
                sex="female",
                age_value=45,
                age_unit="years",
            ),
        )
    if not case.case_products:
        crud.add_case_product(
            db,
            case,
            schemas.CaseProductCreate(
                product_id=product.id,
                reported_product_name=product.product_name,
                active_substance_text=", ".join(
                    link.substance.substance_name
                    for link in product.substance_links
                    if link.substance
                ),
                drug_role="suspect",
                dose_value=product.strength,
                route=product.route or "oral",
            ),
        )
    if not db.query(Reaction).filter(Reaction.case_id == case.id).first():
        crud.add_reaction(
            db,
            case,
            schemas.ReactionCreate(
                reported_term=reaction,
                verbatim_term=reaction,
                outcome="recovering",
                is_serious=seriousness == "serious",
            ),
        )
    if not db.query(Submission).filter(Submission.case_id == case.id).first():
        crud.create_submission_for_case(
            db,
            case,
            schemas.SubmissionCreate(
                recipient_partner_id=partner.id,
                recipient_type="partner",
                submission_type="icsr",
                submission_format="email",
                submission_status="submitted",
                due_date=received_date + timedelta(days=7),
            ),
        )
    return case


def ensure_demo_partner_report(
    db,
    *,
    report_number: str,
    partner: Partner,
    received_date: date,
    subject: str,
    raw_text: str,
    country: str,
    valid: bool = True,
) -> SafetyReport:
    report = db.query(SafetyReport).filter(SafetyReport.safety_report_number == report_number).first()
    if report:
        return report
    report = crud.create_safety_report(
        db,
        schemas.SafetyReportCreate(
            safety_report_number=report_number,
            received_date=received_date,
            source_type="partner",
            message_direction="partner_to_our_company",
            partner_id=partner.id,
            reporter_name="Partner PV Sender",
            reporter_email=f"pv.{partner.partner_code.lower()}@example.test",
            reporter_country_code=country,
            raw_subject=subject,
            raw_text=raw_text,
        ),
    )
    crud.triage_safety_report(
        db,
        report,
        schemas.TriageUpdate(
            triage_status="valid_icsr" if valid else "invalid",
            triage_comment=(
                "Seed demo partner ICSR."
                if valid
                else "Seed demo invalid partner message."
            ),
            is_valid_icsr=valid,
            minimum_criteria_patient=valid,
            minimum_criteria_reporter=valid,
            minimum_criteria_product=valid,
            minimum_criteria_event=valid,
        ),
    )
    return report


def ensure_demo_reconciliation(
    db,
    *,
    partner: Partner,
    contact: ContractContact,
    period_start: date,
    period_end: date,
    document_format: str = "xlsx",
) -> PartnerReconciliation:
    existing = (
        db.query(PartnerReconciliation)
        .filter(
            PartnerReconciliation.partner_id == partner.id,
            PartnerReconciliation.period_start == period_start,
            PartnerReconciliation.period_end == period_end,
            PartnerReconciliation.is_deleted.is_(False),
        )
        .first()
    )
    if existing:
        return existing
    preview = build_reconciliation_preview(
        db,
        partner_id=partner.id,
        period_start=period_start,
        period_end=period_end,
        contact_id=contact.id,
        language="ru",
    )
    reconciliation = crud.create_partner_reconciliation(
        db,
        schemas.PartnerReconciliationCreate(
            partner_id=partner.id,
            contact_id=contact.id,
            period_start=period_start,
            period_end=period_end,
            reconciliation_type="periodic",
            language="ru",
            prepared_by=DEMO_PREPARED_BY,
            products=", ".join(product.product_name for product in products_for_partner(db, partner.id)),
            document_format=document_format,
            comments=(
                "Seed demo reconciliation form. "
                f"Partner contact: {contact_full_name(contact) or contact.email or ''}."
            ),
        ),
        preview["items"],
    )
    document_info = generate_reconciliation_document_file(
        reconciliation,
        document_format=document_format,
    )
    crud.save_partner_reconciliation_document(db, reconciliation, document_info)
    return reconciliation


def ensure_extended_demo_data(db) -> None:
    partners = [
        ("PARTNER-001", "Global Pharma Partner Ltd.", "monthly", "DE"),
        ("PARTNER-002", "Northbridge Safety Partner", "quarterly", "GB"),
        ("PARTNER-003", "Eastline PV Affiliate", "monthly", "KZ"),
    ]
    partner_map = {
        code: ensure_demo_partner(db, code=code, name=name, frequency=frequency)
        for code, name, frequency, country in partners
    }
    country_by_partner = {code: country for code, _name, _frequency, country in partners}

    substance_specs = [
        ("Ibuprofen", "Ibuprofen", "M01AE01"),
        ("Paracetamol", "Paracetamol", "N02BE01"),
        ("Loratadine", "Loratadine", "R06AX13"),
        ("Metformin", "Metformin", "A10BA02"),
        ("Amlodipine", "Amlodipine", "C08CA01"),
        ("Oseltamivir", "Oseltamivir", "J05AH02"),
    ]
    substance_map = {
        name: ensure_demo_substance(db, name=name, inn=inn, atc_code=atc)
        for name, inn, atc in substance_specs
    }

    product_specs = [
        ("PROD-IBU-200", "IbuRelief 200 mg Tablets", "PARTNER-001", "Ibuprofen", 7, "200 mg", "DE-IBU-200-2026"),
        ("PROD-METF-500", "GlucoBalance 500 mg Tablets", "PARTNER-001", "Metformin", 7, "500 mg", "DE-METF-500-2026"),
        ("PROD-PARA-500", "ParaRelief 500 mg Tablets", "PARTNER-002", "Paracetamol", 7, "500 mg", "GB-PARA-500-2026"),
        ("PROD-AMLO-5", "CardioCalm 5 mg Tablets", "PARTNER-002", "Amlodipine", 7, "5 mg", "GB-AMLO-005-2026"),
        ("PROD-LORA-10", "LoraCalm 10 mg Tablets", "PARTNER-003", "Loratadine", 7, "10 mg", "KZ-LORA-010-2026"),
        ("PROD-OSEL-75", "FluStop 75 mg Capsules", "PARTNER-003", "Oseltamivir", 3, "75 mg", "KZ-OSEL-075-2026"),
    ]
    product_map = {
        code: ensure_demo_product(
            db,
            code=code,
            name=name,
            partner=partner_map[partner_code],
            substance=substance_map[substance_name],
            dosage_form_id=dosage_form_id,
            strength=strength,
            auth_number=auth_number,
            auth_country_code=country_by_partner[partner_code],
        )
        for code, name, partner_code, substance_name, dosage_form_id, strength, auth_number in product_specs
    }

    contacts = {
        "PARTNER-001": [
            ("Mueller", "Anna", "anna.mueller.pv@example.test", "PV Manager", True, False),
            ("Weber", "Karl", "karl.weber.qa@example.test", "QA / Reconciliation CC", False, True),
        ],
        "PARTNER-002": [
            ("Smith", "Mary", "mary.smith.safety@example.test", "Safety Lead", True, False),
            ("Brown", "Olivia", "olivia.brown.pv@example.test", "PV Associate", False, True),
        ],
        "PARTNER-003": [
            ("Petrova", "Elena", "elena.petrova.pv@example.test", "PV Officer", True, False),
            ("Nurlan", "Aida", "aida.nurlan.reg@example.test", "Regulatory CC", False, True),
        ],
    }
    primary_contacts = {}
    for partner_code, contact_specs in contacts.items():
        for last_name, first_name, email, position, is_primary, cc_reconciliation in contact_specs:
            contact = ensure_demo_contact(
                db,
                partner=partner_map[partner_code],
                last_name=last_name,
                first_name=first_name,
                email=email,
                position=position,
                is_primary=is_primary,
                cc_reconciliation=cc_reconciliation,
            )
            if is_primary:
                primary_contacts[partner_code] = contact

    contract_date = date(2026, 1, 1)
    valid_until = date(2027, 12, 31)
    for partner_code, product_codes in {
        "PARTNER-001": ["PROD-IBU-200", "PROD-METF-500"],
        "PARTNER-002": ["PROD-PARA-500", "PROD-AMLO-5"],
        "PARTNER-003": ["PROD-LORA-10", "PROD-OSEL-75"],
    }.items():
        for index, product_code in enumerate(product_codes, start=1):
            contract = ensure_demo_contract(
                db,
                partner=partner_map[partner_code],
                product=product_map[product_code],
                number=f"PV-{partner_code}-{index:02d}",
                contract_date=contract_date,
                valid_until=valid_until,
            )
            ensure_demo_additional_agreement(
                db,
                parent_contract=contract,
                number=f"ADD-{partner_code}-{index:02d}-2026",
                contract_date=date(2026, 3, 1),
                valid_until=valid_until,
            )

    case_specs = [
        ("DEMO-SR-2026-0101", "DEMO-CASE-2026-0101", "PARTNER-001", "PROD-IBU-200", date(2026, 5, 5), "Rash after IbuRelief", "Patient reported rash after IbuRelief.", "Rash", "DE"),
        ("DEMO-SR-2026-0102", "DEMO-CASE-2026-0102", "PARTNER-001", "PROD-METF-500", date(2026, 5, 18), "Nausea after GlucoBalance", "Patient reported nausea after GlucoBalance.", "Nausea", "DE"),
        ("DEMO-SR-2026-0201", "DEMO-CASE-2026-0201", "PARTNER-002", "PROD-PARA-500", date(2026, 5, 7), "Headache after ParaRelief", "Patient reported headache after ParaRelief.", "Headache", "GB"),
        ("DEMO-SR-2026-0202", "DEMO-CASE-2026-0202", "PARTNER-002", "PROD-AMLO-5", date(2026, 5, 21), "Dizziness after CardioCalm", "Patient reported dizziness after CardioCalm.", "Dizziness", "GB"),
        ("DEMO-SR-2026-0301", "DEMO-CASE-2026-0301", "PARTNER-003", "PROD-LORA-10", date(2026, 5, 9), "Somnolence after LoraCalm", "Patient reported somnolence after LoraCalm.", "Somnolence", "KZ"),
        ("DEMO-SR-2026-0302", "DEMO-CASE-2026-0302", "PARTNER-003", "PROD-OSEL-75", date(2026, 5, 24), "Vomiting after FluStop", "Patient reported vomiting after FluStop.", "Vomiting", "KZ"),
    ]
    for report_number, case_number, partner_code, product_code, received, subject, raw_text, reaction, country in case_specs:
        ensure_demo_icsr_case(
            db,
            report_number=report_number,
            case_number=case_number,
            partner=partner_map[partner_code],
            product=product_map[product_code],
            received_date=received,
            subject=subject,
            raw_text=raw_text,
            reaction=reaction,
            country=country,
        )

    partner_only_specs = [
        ("DEMO-PARTNER-SR-2026-0103", "PARTNER-001", date(2026, 5, 25), "Partner-only diarrhea report", "Partner-only report: diarrhea after IbuRelief.", "DE", True),
        ("DEMO-PARTNER-SR-2026-0203", "PARTNER-002", date(2026, 5, 26), "Partner-only abdominal pain report", "Partner-only report: abdominal pain after ParaRelief.", "GB", True),
        ("DEMO-PARTNER-SR-2026-0303", "PARTNER-003", date(2026, 5, 27), "Invalid partner message", "Partner sent non-valid message without adverse event.", "KZ", False),
    ]
    for report_number, partner_code, received, subject, raw_text, country, valid in partner_only_specs:
        ensure_demo_partner_report(
            db,
            report_number=report_number,
            partner=partner_map[partner_code],
            received_date=received,
            subject=subject,
            raw_text=raw_text,
            country=country,
            valid=valid,
        )

    for partner_code, document_format in {
        "PARTNER-001": "xlsx",
        "PARTNER-002": "xlsx",
        "PARTNER-003": "docx",
    }.items():
        ensure_demo_reconciliation(
            db,
            partner=partner_map[partner_code],
            contact=primary_contacts[partner_code],
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
            document_format=document_format,
        )


def run_seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        crud.create_user(db, "pv.admin@company.ru", "Системный администратор", "admin")

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
                    message_direction="needs_review",
                    partner_id=partner.id,
                    reporter_name="Смирнова Анна Сергеевна",
                    reporter_email="pv.contact@company.ru",
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

        starter_partners = [
            ("PARTNER-002", "Northbridge Safety Partner", "quarterly"),
            ("PARTNER-003", "Eastline PV Affiliate", "monthly"),
        ]
        partner_map = {partner.partner_code: partner}
        for code, name, frequency in starter_partners:
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
            (partner.partner_code, "Смирнова", "Анна", "pv.contact@company.ru", "PV contact"),
            ("PARTNER-002", "Соколова", "Мария", "pv.safety@company.ru", "Safety manager"),
            ("PARTNER-003", "Петрова", "Елена", "pv.affiliates@company.ru", "PV officer"),
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

        starter_cases = [
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
        for report_number, case_number, case_partner, case_product, subject, raw_text, reaction in starter_cases:
            starter_report = db.query(SafetyReport).filter(
                SafetyReport.safety_report_number == report_number
            ).first()
            if not starter_report:
                starter_report = crud.create_safety_report(
                    db,
                    schemas.SafetyReportCreate(
                        safety_report_number=report_number,
                        source_type="email",
                        message_direction="needs_review",
                        partner_id=case_partner.id,
                        reporter_name="Смирнова Анна Сергеевна",
                        reporter_email="pv.contact@company.ru",
                        reporter_country_code="DE",
                        raw_subject=subject,
                        raw_text=raw_text,
                    ),
                )
                starter_report = crud.triage_safety_report(
                    db,
                    starter_report,
                    schemas.TriageUpdate(
                        triage_status="valid_icsr",
                        is_valid_icsr=True,
                        minimum_criteria_patient=True,
                        minimum_criteria_reporter=True,
                        minimum_criteria_product=True,
                        minimum_criteria_event=True,
                    ),
                )
            starter_case = db.query(Case).filter(Case.case_number == case_number).first()
            if not starter_case:
                starter_case = crud.create_case(
                    db,
                    schemas.CaseCreate(
                        case_number=case_number,
                        safety_report_id=starter_report.id,
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
            if not starter_case.patients:
                crud.add_patient(db, starter_case, schemas.PatientCreate(age_value=35, age_unit="years"))
            if not starter_case.case_products:
                crud.add_case_product(
                    db,
                    starter_case,
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
            if not db.query(Reaction).filter(Reaction.case_id == starter_case.id).first():
                crud.add_reaction(
                    db,
                    starter_case,
                    schemas.ReactionCreate(reported_term=reaction, is_serious=False),
                )
            if not db.query(Submission).filter(Submission.case_id == starter_case.id).first():
                crud.create_submission_for_case(
                    db,
                    starter_case,
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
                    message_direction="partner_to_our_company",
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
                        message_direction="partner_to_our_company",
                        partner_id=report_partner.id,
                        reporter_name="Partner Sender",
                        reporter_email="pv.partner@company.ru",
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
                            triage_comment="Недостаточно данных для валидации сообщения.",
                            is_valid_icsr=False,
                        ),
                    )

        ensure_extended_demo_data(db)
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()
    print("Seed data created.")
