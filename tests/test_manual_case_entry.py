from tests.form_helpers import post_current
from pathlib import Path
from uuid import uuid4


def test_manual_case_form_is_available_without_e2b_file(client):
    safety_page = client.get("/safety-reports")
    assert safety_page.status_code == 200
    assert 'href="/cases/new"' in safety_page.text
    assert "Создать кейс вручную" in safety_page.text

    form_page = client.get("/cases/new")
    assert form_page.status_code == 200
    assert 'action="/cases/manual"' in form_page.text
    assert 'name="reporter_name"' in form_page.text
    assert 'name="patient_initials"' in form_page.text
    assert 'name="reported_product_name"' in form_page.text
    assert 'name="reported_term"' in form_page.text
    assert 'name="next_step" value="assessment"' in form_page.text
    assert "Создать и оценить" in form_page.text
    creation_tab_ids = (
        "new-overview-tab",
        "new-source-tab",
        "new-product-tab",
        "new-patient-tab",
        "new-reactions-tab",
        "new-additional-tab",
        "new-documents-tab",
    )
    tab_positions = [form_page.text.index(f'id="{tab_id}"') for tab_id in creation_tab_ids]
    assert tab_positions == sorted(tab_positions)
    assert form_page.text.count('data-next-case-tab=') == 6
    assert "Следующий шаг" in form_page.text


def test_manual_case_creates_complete_linked_icsr_atomically(client):
    from app.database import SessionLocal
    from app.models import Case, SafetyReport

    suffix = uuid4().hex[:8]
    case_number = f"MAN-{suffix}"
    response = post_current(client,
        "/cases/manual",
        data={
            "case_number": case_number,
            "worldwide_case_id": f"RU-ARS-{suffix}",
            "source_type": "phone",
            "reporter_name": "Dr Test Reporter",
            "reporter_email": "reporter@example.test",
            "reporter_country_code": "ru",
            "case_type": "spontaneous",
            "initial_received_date": "2026-08-17",
            "latest_received_date": "2026-08-18",
            "country_of_occurrence": "ru",
            "workflow_status": "data_entry",
            "patient_initials": "AB",
            "patient_identifier": f"PAT-{suffix}",
            "sex": "female",
            "age_value": "42",
            "age_unit": "years",
            "reported_product_name": "Foreign test medicine",
            "active_substance_text": "test substance",
            "drug_role": "suspect",
            "dose_value": "10",
            "dose_unit": "mg",
            "route": "oral",
            "reported_term": "Severe headache",
            "meddra_pt_code": "10019211",
            "meddra_pt_name": "Headache",
            "reaction_outcome": "recovering",
            "seriousness_hospitalization": "true",
            "narrative": "Manual post-marketing case without an XML file.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/cases/")

    with SessionLocal() as db:
        case = db.query(Case).filter(Case.case_number == case_number).one()
        assert case.e2b_import_batch_id is None
        assert case.worldwide_case_id == f"RU-ARS-{suffix}"
        assert case.country_of_occurrence == "RU"
        assert case.seriousness == "serious"
        assert case.workflow_status == "data_entry"

        assert len(case.patients) == 1
        assert case.patients[0].patient_initials == "AB"
        assert case.patients[0].age_value == 42

        assert len(case.case_products) == 1
        assert case.case_products[0].reported_product_name == "Foreign test medicine"
        assert case.case_products[0].active_substance_text == "test substance"

        assert len(case.reactions) == 1
        assert case.reactions[0].reported_term == "Severe headache"
        assert case.reactions[0].is_serious is True
        assert case.reactions[0].seriousness_hospitalization is True

        report = db.query(SafetyReport).filter(SafetyReport.id == case.safety_report_id).one()
        assert report.case_id == case.id
        assert report.source_type == "phone"
        assert report.reporter_name == "Dr Test Reporter"
        assert report.reporter_country_code == "RU"
        assert report.triage_status == "converted_to_case"
        assert report.is_valid_icsr is True
        assert report.minimum_criteria_patient is True
        assert report.minimum_criteria_reporter is True
        assert report.minimum_criteria_product is True
        assert report.minimum_criteria_event is True
        assert len(report.product_links) == 1
        assert report.product_links[0].reported_product_name == "Foreign test medicine"


def test_manual_case_missing_minimum_criteria_saves_nothing(client):
    from app.database import SessionLocal
    from app.models import Case, SafetyReport

    suffix = uuid4().hex[:8]
    case_number = f"INVALID-{suffix}"
    with SessionLocal() as db:
        reports_before = db.query(SafetyReport).count()

    response = post_current(client,
        "/cases/manual",
        data={
            "case_number": case_number,
            "initial_received_date": "2026-08-17",
            "reported_term": "Headache",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/cases/new?validation=")
    with SessionLocal() as db:
        assert db.query(Case).filter(Case.case_number == case_number).count() == 0
        assert db.query(SafetyReport).count() == reports_before


def test_manual_case_can_open_causality_assessment_after_creation(client):
    suffix = uuid4().hex[:8]
    response = post_current(client,
        "/cases/manual",
        data={
            "case_number": f"ASSESS-{suffix}",
            "source_type": "phone",
            "reporter_name": "Dr Initial Assessor",
            "initial_received_date": "2026-08-18",
            "patient_initials": "EF",
            "reported_product_name": "Initial assessment medicine",
            "drug_role": "suspect",
            "reported_term": "Initial assessment reaction",
            "next_step": "assessment",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/cases/")
    assert "tab=assessment" in response.headers["location"]


def test_case_card_uses_focused_icsr_tabs(client):
    suffix = uuid4().hex[:8]
    response = post_current(client,
        "/cases/manual",
        data={
            "case_number": f"TABS-{suffix}",
            "source_type": "phone",
            "reporter_name": "Dr Tab Reporter",
            "initial_received_date": "2026-08-18",
            "patient_initials": "GH",
            "reported_product_name": "Tabbed medicine",
            "drug_role": "suspect",
            "reported_term": "Tabbed reaction",
        },
        follow_redirects=False,
    )

    page = client.get(response.headers["location"])
    assert page.status_code == 200
    expected_tabs = (
        ("overview", "Основные сведения"),
        ("source", "Источник и репортёр"),
        ("product", "Препарат"),
        ("patient", "Пациент"),
        ("reactions", "Нежелательные реакции"),
        ("additional", "Дополнительная информация"),
        ("documents", "Документы"),
    )
    tab_positions = []
    for tab_id, label in expected_tabs:
        assert f'id="{tab_id}-tab"' in page.text
        assert f'data-bs-target="#{tab_id}"' in page.text
        assert label in page.text
        tab_positions.append(page.text.index(f'id="{tab_id}-tab"'))
    assert tab_positions == sorted(tab_positions)

    assert 'id="entry-tab"' not in page.text
    assert 'id="medical-tab"' not in page.text
    assert 'id="assessment-tab"' not in page.text
    assert 'id="history-tab"' not in page.text
    assert 'id="followup-tab"' not in page.text
    assert 'id="submission-tab"' not in page.text
    assert 'id="audit-tab"' not in page.text
    assert "caseTabButtons.slice(0, -1)" in page.text
    assert "Документы ИСНР" not in page.text
    assert "Документы исходного сообщения" not in page.text
    assert "Документы пока не добавлены." in page.text
    assert "Связанные партнёры и дополнительные номера" in page.text
    assert "Номер партнёра партнёра" in page.text


def test_documents_can_be_attached_during_creation_and_from_case_card(client):
    from app.database import SessionLocal
    from app.models import Attachment, Case

    suffix = uuid4().hex[:8]
    case_number = f"DOCS-{suffix}"
    created_files: list[Path] = []

    try:
        create_response = post_current(client,
            "/cases/manual",
            data={
                "case_number": case_number,
                "source_type": "phone",
                "reporter_name": "Dr Document Reporter",
                "initial_received_date": "2026-08-18",
                "patient_initials": "IJ",
                "reported_product_name": "Documented medicine",
                "drug_role": "suspect",
                "reported_term": "Documented reaction",
                "document_title": "Исходный документ",
                "document_type": "other",
            },
            files={"document_file": ("initial-note.txt", b"initial evidence", "text/plain")},
            follow_redirects=False,
        )

        assert create_response.status_code == 303
        assert "tab=overview" in create_response.headers["location"]

        with SessionLocal() as db:
            case = db.query(Case).filter(Case.case_number == case_number).one()
            case_id = case.id
            initial_document = db.query(Attachment).filter(Attachment.case_id == case_id).one()
            assert initial_document.document_title == "Исходный документ"
            assert initial_document.file_name == "initial-note.txt"
            assert initial_document.file_size_bytes == len(b"initial evidence")
            created_files.append(Path.cwd() / initial_document.storage_path)

        attach_response = post_current(client,
            "/documents",
            data={
                "case_id": case_id,
                "return_to_case_id": case_id,
                "document_title": "Дополнительный документ",
                "document_type": "other",
            },
            files={"document_file": ("follow-up.txt", b"follow-up evidence", "text/plain")},
            follow_redirects=False,
        )

        assert attach_response.status_code == 303
        assert attach_response.headers["location"].startswith(f"/cases/{case_id}?tab=documents")

        with SessionLocal() as db:
            documents = (
                db.query(Attachment)
                .filter(Attachment.case_id == case_id)
                .order_by(Attachment.uploaded_at)
                .all()
            )
            assert [document.file_name for document in documents] == [
                "initial-note.txt",
                "follow-up.txt",
            ]
            created_files.extend(Path.cwd() / document.storage_path for document in documents[1:])
    finally:
        for created_file in created_files:
            created_file.unlink(missing_ok=True)


def test_case_search_returns_same_ordinary_number_for_different_partners(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:6].upper()
    with SessionLocal() as db:
        partner_a = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"A{suffix}",
                partner_name=f"Search Partner A {suffix}",
            ),
        )
        partner_b = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"B{suffix}",
                partner_name=f"Search Partner B {suffix}",
            ),
        )
        case_a = crud.create_case(db, schemas.CaseCreate(partner_id=partner_a.id))
        case_b = crud.create_case(db, schemas.CaseCreate(partner_id=partner_b.id))
        assert case_a.case_number == case_b.case_number == "2026-001"

    response = client.get("/cases", params={"search": "2026-001"})
    assert response.status_code == 200
    assert f"A{suffix}-2026-001" in response.text
    assert f"B{suffix}-2026-001" in response.text
    assert f"Search Partner A {suffix}" in response.text
    assert f"Search Partner B {suffix}" in response.text


def test_case_search_accepts_empty_dates_and_finds_secondary_identifier(client):
    from datetime import date

    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:6].upper()
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"SID{suffix}",
                partner_name=f"Secondary Identifier Partner {suffix}",
            ),
        )
        case = crud.create_case(
            db,
            schemas.CaseCreate(
                partner_id=partner.id,
                initial_received_date=date(2026, 8, 19),
            ),
        )
        crud.create_case_identifier(
            db,
            case,
            schemas.CaseIdentifierCreate(
                identifier_value=f"PV{suffix}",
                partner_id=partner.id,
                is_primary=True,
            ),
        )
        secondary_number = f"SECONDARY-{suffix}"
        crud.create_case_identifier(
            db,
            case,
            schemas.CaseIdentifierCreate(
                identifier_value=secondary_number,
                organization_name="Partner of partner",
                identifier_type="partner_of_partner_case_number",
            ),
        )
        case_number = case.case_number

    response = client.get(
        "/cases",
        params={"search": secondary_number, "date_from": "", "date_to": ""},
    )
    assert response.status_code == 200
    assert case_number in response.text
    assert secondary_number in response.text

    dated_response = client.get(
        "/cases",
        params={
            "search": secondary_number,
            "date_from": "2026-08-01",
            "date_to": "2026-08-31",
        },
    )
    assert dated_response.status_code == 200
    assert secondary_number in dated_response.text
