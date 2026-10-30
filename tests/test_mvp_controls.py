from tests.form_helpers import post_current
from tests.test_concurrent_forms import Forms
from datetime import date
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient

from conftest import clear_app_modules


def xlsx_sheet_xml(response) -> str:
    with ZipFile(BytesIO(response.content)) as archive:
        return archive.read("xl/worksheets/sheet1.xml").decode("utf-8")


def create_test_partner(db, code: str):
    from app import crud, schemas

    return crud.create_partner(
        db,
        schemas.PartnerCreate(
            partner_code=code,
            partner_name=f"Test partner {code}",
            partner_type="fn",
            reconciliation_frequency="monthly",
        ),
    )


def test_partners_filter_by_reconciliation_frequency_and_hide_acme_hint(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        for frequency in ("monthly", "quarterly"):
            crud.create_partner(
                db,
                schemas.PartnerCreate(
                    partner_code=f"FREQ-{frequency}-{suffix}",
                    partner_name=f"Frequency {frequency} {suffix}",
                    partner_type="fn",
                    reconciliation_frequency=frequency,
                ),
            )

    response = client.get(
        "/partners",
        params={
            "search": suffix,
            "reconciliation_frequency_filter": "monthly",
        },
    )

    assert response.status_code == 200
    assert f"FREQ-MONTHLY-{suffix}".upper() in response.text
    assert f"FREQ-QUARTERLY-{suffix}".upper() not in response.text
    assert 'name="reconciliation_frequency_filter"' in response.text
    assert '<option value="monthly" selected>' in response.text
    assert "Латинские заглавные буквы, цифры и дефис" in response.text
    assert 'placeholder="ABC"' in response.text
    assert 'name="partner_name" placeholder="Партнер"' in response.text


def test_core_pages_and_server_pagination(client):
    for path in [
        "/health",
        "/",
        "/training",
        "/gpt-analysis",
        "/message-journal",
        "/cases?page=1&per_page=2",
        "/documents?page=1&per_page=2",
        "/literature-monitoring?page=1&per_page=2",
        "/sops?page=1&per_page=2",
        "/audit-log?page=1&per_page=2",
    ]:
        response = client.get(path)
        assert response.status_code == 200, path


def test_dashboard_overdue_tasks_are_visible_in_period_filters():
    from app.routers.dashboard import task_matches_filter

    today = date(2026, 6, 25)
    overdue_task = {
        "due_date": date(2026, 6, 24),
        "status": "Ожидает ответа",
        "plan": "День",
    }
    closed_task = {
        "due_date": date(2025, 6, 24),
        "status": "Закрыто",
        "plan": "День",
    }

    for filter_key in ["today", "week", "month", "quarter", "halfyear", "year"]:
        assert task_matches_filter(overdue_task, filter_key, today)
        assert not task_matches_filter(closed_task, filter_key, today)

    assert task_matches_filter(overdue_task, "overdue", today)
    assert not task_matches_filter(closed_task, "overdue", today)
    assert not task_matches_filter(overdue_task, "ad_hoc", today)


def test_sidebar_menu_target_order_and_direct_sections():
    from app.menu import build_menu_state

    state = build_menu_state(
        {
            "view",
            "manage_reference_data",
            "manage_users",
            "manage_system_settings",
            "audit_view",
        },
        active_page="dashboard",
    )

    assert [group["key"] for group in state.groups] == [
        "pv_plan",
        "partners_agreements",
        "products",
        "safety_workflow",
        "journals",
        "literature_monitoring",
        "pv_modules",
        "training",
        "document_registry",
        "submissions_filings",
        "administration",
    ]

    direct_groups = {
        "pv_plan": "/",
        "training": "/training",
        "journals": "/message-journal",
        "document_registry": "/documents",
        "literature_monitoring": "/literature-monitoring",
        "submissions_filings": "/submissions",
    }
    groups_by_key = {group["key"]: group for group in state.groups}
    for key, href in direct_groups.items():
        assert groups_by_key[key]["href"] == href
        assert groups_by_key[key]["children"] == []

    assert [child["href"] for child in groups_by_key["partners_agreements"]["children"]] == [
        "/partners",
        "/contract-contacts",
        "/contracts",
        "/contract-imports",
        "/accounting-data",
    ]
    assert [child["href"] for child in groups_by_key["safety_workflow"]["children"]] == [
        "/safety-reports",
        "/cases",
        "/partner-reconciliation",
        "/gpt-analysis",
    ]
    assert groups_by_key["safety_workflow"]["label"] == "Safety Messages"
    assert groups_by_key["safety_workflow"]["children"][0]["label"] == "All incoming messages"
    assert groups_by_key["safety_workflow"]["children"][-1]["label"] == "GPT analysis"
    assert groups_by_key["administration"]["children"][-1]["label"] == "Audit Log"
    assert [child["href"] for child in groups_by_key["pv_modules"]["children"]] == [
        "/psmf",
        "/psur",
        "/rmp",
        "/sops",
    ]


def test_sidebar_menu_filters_admin_items_by_permissions():
    from app.menu import build_menu_state

    viewer_state = build_menu_state({"view"}, active_page="dashboard")
    assert "administration" not in [group["key"] for group in viewer_state.groups]

    audit_state = build_menu_state({"audit_view"}, active_page="audit_log")
    assert [group["key"] for group in audit_state.groups] == ["administration"]
    assert audit_state.groups[0]["href"] == "/audit-log"
    assert audit_state.groups[0]["children"] == []


def test_query_user_switch_is_disabled_by_default(client):
    response = client.get("/?as_user=pv.admin@company.ru")
    assert response.status_code == 200
    assert "pv_user_id" not in response.headers.get("set-cookie", "")


def test_reconciliation_period_presets_are_calendar_periods():
    from app.routers.partner_reconciliation import reconciliation_period_presets

    presets = {
        preset["key"]: (preset["period_start"], preset["period_end"])
        for preset in reconciliation_period_presets(date(2026, 6, 30))
    }

    assert presets["previous_month"] == (date(2026, 5, 1), date(2026, 5, 31))
    assert presets["current_month"] == (date(2026, 6, 1), date(2026, 6, 30))
    assert presets["previous_quarter"] == (date(2026, 1, 1), date(2026, 3, 31))


def test_gpt_analysis_page_shows_development_notice(client):
    response = client.get("/gpt-analysis")
    assert response.status_code == 200
    assert "Модуль в стадии разработки" in response.text
    assert "не использует модель GPT" in response.text
    assert "/gpt-analysis/analyze" not in response.text
    assert "/gpt-analysis/confirm" not in response.text
    assert "Проанализировать с GPT" not in response.text


def test_gpt_analysis_confirm_saves_safety_report(client):
    from app.database import SessionLocal
    from app.models import SafetyReport

    response = post_current(client,
        "/gpt-analysis/confirm",
        data={
            "source_text": "Patient reported headache after product use.",
            "request_type": "possible ICSR",
            "possible_icsr": "yes",
            "patient_information": "Patient reported headache.",
            "adverse_event": "Headache",
            "seriousness": "not specified",
            "missing_information": "Reporter/contact details",
            "recommended_next_action": "Review minimum ICSR criteria.",
            "validity_assessment": "Human review required before ICSR creation.",
            "gpt_json_output": "{\"possible_icsr\":\"yes\"}",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"].startswith("/safety-reports/")

    with SessionLocal() as db:
        report = (
            db.query(SafetyReport)
            .filter(SafetyReport.source_type == "gpt_analysis")
            .one()
        )
        assert report.raw_subject == "GPT analysis draft: possible ICSR"
        assert "Patient reported headache after product use." in report.raw_text
        assert "Human review required before ICSR creation." in report.raw_text


def test_contract_contacts_page_supports_contact_details_and_duplicate(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"CC-{suffix}",
                partner_name=f"Contact Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
            ),
        )

    page = client.get("/contract-contacts")
    assert page.status_code == 200
    assert "Поиск" in page.text
    assert "Добавить контактное лицо" in page.text

    response = post_current(client,
        "/contract-contacts",
        data={
            "partner_id": partner.id,
            "last_name": "Ivanov",
            "first_name": "Ivan",
            "patronymic": "Ivanovich",
            "email": f"ivanov-{suffix}@company.ru",
            "position": "Quality manager",
            "mobile_phone": "+7 900 100 10 10",
            "work_phone": "+7 495 100 10 10",
            "extension_phone": "123",
            "company_website": "partner.company.ru",
            "address": "Moscow",
            "contact_status": "current",
            "contact_type": "quality",
            "reconciliation_participation": "cc",
            "comments": "Created from UI test",
        },
    )
    assert response.status_code == 200
    assert "Контакт сохранен." in response.text

    with SessionLocal() as db:
        contact = (
            db.query(crud.ContractContact)
            .filter(crud.ContractContact.email == f"ivanov-{suffix}@company.ru")
            .one()
        )
        assert contact.mobile_phone == "+7 900 100 10 10"
        assert contact.work_phone == "+7 495 100 10 10"
        assert contact.extension_phone == "123"
        assert contact.company_website == "partner.company.ru"
        assert contact.address == "Moscow"
        assert contact.contact_type == "quality"
        assert contact.cc_reconciliation is True
        assert contact.is_reconciliation_recipient is False

        duplicate_page = client.get(f"/contract-contacts?duplicate_id={contact.id}")
    assert duplicate_page.status_code == 200
    assert "+7 900 100 10 10" in duplicate_page.text
    assert "Подготовлена копия контакта" in duplicate_page.text


def test_safety_messages_export_and_create_case(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"MSG-{suffix}",
                partner_name=f"Message Partner {suffix}",
                partner_type="fn",
            ),
        )
        product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"MSG-PROD-{suffix}",
                product_name=f"Message Product {suffix}",
            ),
        )
        second_product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"MSG-PROD-2-{suffix}",
                product_name=f"Second Message Product {suffix}",
            ),
        )
        partner_id = partner.id
        product_id = product.id
        second_product_id = second_product.id

    response = post_current(client,
        "/incoming-requests/register",
        data={
            "source_text": f"Patient had headache after Message Product {suffix}.",
            "request_type": "safety_message",
            "partner_id": partner_id,
            "product_id": product_id,
            "product_ids": [product_id, second_product_id],
            "possible_icsr": "yes",
            "patient_information": "Adult patient",
            "adverse_event": "Headache",
            "seriousness": "non-serious",
            "missing_information": "Reporter contact",
            "status_value": "valid_icsr",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        row = (
            db.query(crud.IncomingRequest)
            .filter(crud.IncomingRequest.product_id == product_id)
            .one()
        )
        request_id = row.id
        assert row.message_number.startswith("MSG-")
        assert row.triage_status == "not_started"
        assert row.product_ids == [product_id, second_product_id]
        assert len([link for link in row.product_links if not link.is_deleted]) == 2

    export_response = client.get("/incoming-requests/export.xlsx")
    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    incoming_sheet = xlsx_sheet_xml(export_response)
    assert "Headache" in incoming_sheet
    assert "Message Product" in incoming_sheet
    assert "Second Message Product" in incoming_sheet

    def message_version():
        with SessionLocal() as db:
            return crud.get_incoming_request(db, request_id).version

    blocked_case_response = post_current(client, f"/api/incoming-requests/{request_id}/create-case", json={"expected_version": message_version()})
    assert blocked_case_response.status_code == 400
    assert "Complete triage" in blocked_case_response.text

    invalid_triage_response = client.patch(
        f"/api/incoming-requests/{request_id}/triage",
        json={
            "expected_version": message_version(),
            "triage_status": "completed",
            "safety_relevance": "safety_related",
            "potential_icsr": "yes",
            "validity_status": "valid_icsr",
            "has_identifiable_patient": True,
            "has_identifiable_reporter": False,
            "has_suspect_product": True,
            "has_adverse_event": True,
            "triage_result": "create_case",
        },
    )
    assert invalid_triage_response.status_code == 400
    assert "requires patient, reporter, product, and adverse event criteria" in invalid_triage_response.text

    triage_response = client.patch(
        f"/api/incoming-requests/{request_id}/triage",
        json={
            "expected_version": message_version(),
            "triage_status": "completed",
            "safety_relevance": "safety_related",
            "potential_icsr": "yes",
            "validity_status": "valid_icsr",
            "has_identifiable_patient": True,
            "has_identifiable_reporter": True,
            "has_suspect_product": True,
            "has_adverse_event": True,
            "triage_result": "create_case",
            "triage_comment": "Minimum ICSR criteria confirmed.",
        },
    )
    assert triage_response.status_code == 200, triage_response.text
    assert triage_response.json()["triage_status"] == "completed"
    assert triage_response.json()["validity_status"] == "valid_icsr"

    preview_response = client.get(f"/incoming-requests/{request_id}/case-preview")
    assert preview_response.status_code == 200, preview_response.text
    assert "Confirm and create case" in preview_response.text
    assert "Patient had headache" in preview_response.text

    case_response = post_current(client,
        f"/incoming-requests/{request_id}/create-case",
        data={
            "expected_version": message_version(),
            "case_type": "spontaneous",
            "report_type": "safety_message",
            "initial_received_date": "2026-06-23",
            "latest_received_date": "2026-06-23",
            "seriousness": "non-serious",
            "workflow_status": "data_entry",
            "narrative": "Confirmed preview narrative.",
        },
        follow_redirects=False,
    )
    assert case_response.status_code == 303, case_response.text
    location = case_response.headers["location"]
    case_id = location.rstrip("/").split("/")[-1].split("?")[0]

    repeat_case_response = post_current(client, f"/api/incoming-requests/{request_id}/create-case", json={"expected_version": message_version()})
    assert repeat_case_response.status_code == 201, repeat_case_response.text
    assert repeat_case_response.json()["id"] == case_id

    cases_export_response = client.get("/api/cases/export.xlsx")
    assert cases_export_response.status_code == 200
    assert cases_export_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    cases_sheet = xlsx_sheet_xml(cases_export_response)
    assert "Headache" in cases_sheet
    assert "Message Product" in cases_sheet

    with SessionLocal() as db:
        row = crud.get_incoming_request(db, request_id)
        case = crud.get_case(db, case_id)
        message_number = row.message_number
        assert row.case_id == case_id
        assert row.status == "converted_to_case"
        assert case.partner_id == partner_id
        assert case.narrative == "Confirmed preview narrative."
        assert {case_product.product_id for case_product in case.case_products} == {
            product_id,
            second_product_id,
        }
        assert case.reactions[0].reported_term == "Headache"
        active_links = [link for link in row.case_links if not link.is_deleted]
        assert len(active_links) == 1
        assert active_links[0].case_id == case_id
        assert active_links[0].link_type == "created_from_message"
        assert case.incoming_request_links[0].incoming_request_id == request_id

    journal_response = client.get("/incoming-requests", params={"quick_filter": "converted_to_case"})
    assert journal_response.status_code == 200, journal_response.text
    assert message_number in journal_response.text
    assert f"/cases/{case_id}" in journal_response.text

    follow_up_response = post_current(client,
        f"/api/incoming-requests/{request_id}/case-links",
        json={
            "expected_version": message_version(),
            "case_id": case_id,
            "expected_case_version": repeat_case_response.json()["version"],
            "link_type": "follow_up",
            "comment": "Follow-up message linked for traceability.",
        },
    )
    assert follow_up_response.status_code == 201, follow_up_response.text
    assert follow_up_response.json()["link_type"] == "follow_up"

    case_page_response = client.get(f"/cases/{case_id}")
    assert case_page_response.status_code == 200
    assert "Source messages" in case_page_response.text
    assert "MSG-" in case_page_response.text


def test_safety_report_supports_multiple_products_and_copies_them_to_case(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import SafetyReport

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        products = [
            crud.create_product(
                db,
                schemas.ProductCreate(
                    product_code=f"SR-MULTI-{index}-{suffix}",
                    product_name=f"Safety report product {index} {suffix}",
                ),
            )
            for index in (1, 2)
        ]
        product_ids = [product.id for product in products]

    response = post_current(client,
        "/safety-reports",
        data={
            "source_type": "email",
            "message_direction": "partner_to_our_company",
            "raw_subject": f"Multiple products {suffix}",
            "raw_text": "A safety report mentioning two products.",
            "product_ids": product_ids,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        report = (
            db.query(SafetyReport)
            .filter(SafetyReport.raw_subject == f"Multiple products {suffix}")
            .one()
        )
        report_id = report.id
        assert set(report.product_ids) == set(product_ids)
        assert report.minimum_criteria_product is True

    api_response = client.get(f"/api/safety-reports/{report_id}")
    assert api_response.status_code == 200, api_response.text
    assert set(api_response.json()["product_ids"]) == set(product_ids)

    case_response = post_current(client,
        f"/safety-reports/{report_id}/create-case",
        data={"expected_version": api_response.json()["version"]},
        follow_redirects=False,
    )
    assert case_response.status_code == 303, case_response.text
    case_id = case_response.headers["location"].rstrip("/").split("/")[-1].split("?")[0]

    with SessionLocal() as db:
        case = crud.get_case(db, case_id)
        assert {row.product_id for row in case.case_products} == set(product_ids)

def test_icsr_workflow_lock_and_reopen(client):
    response = post_current(client,
        "/api/cases",
        json={"case_number": "TEST-WF-001", "workflow_status": "new"},
    )
    assert response.status_code == 201, response.text
    case_id = response.json()["id"]

    response = client.patch(
        f"/api/cases/{case_id}/status",
        json={"expected_version": client.get(f"/api/cases/{case_id}").json()["version"], "workflow_status": "submitted"},
    )
    assert response.status_code == 400
    assert "Invalid ICSR workflow transition" in response.text

    for workflow_status in [
        "triage",
        "data_entry",
        "medical_review",
        "qc",
        "ready_for_submission",
        "submitted",
    ]:
        response = client.patch(
            f"/api/cases/{case_id}/status",
            json={"expected_version": client.get(f"/api/cases/{case_id}").json()["version"], "workflow_status": workflow_status, "change_reason": "workflow test"},
        )
        assert response.status_code == 200, response.text

    submitted_case = response.json()
    assert submitted_case["workflow_status"] == "submitted"
    assert submitted_case["is_locked"] is True

    response = post_current(client,
        f"/api/cases/{case_id}/patients",
        json={"patient_initials": "AB"},
    )
    assert response.status_code == 400
    assert "locked" in response.text.lower()

    response = client.patch(
        f"/api/cases/{case_id}/status",
        json={"expected_version": client.get(f"/api/cases/{case_id}").json()["version"], "workflow_status": "reopened"},
    )
    assert response.status_code == 400
    assert "Change reason is required" in response.text

    response = client.patch(
        f"/api/cases/{case_id}/status",
        json={"expected_version": client.get(f"/api/cases/{case_id}").json()["version"], "workflow_status": "reopened", "change_reason": "follow-up received"},
    )
    assert response.status_code == 200, response.text
    reopened_case = response.json()
    assert reopened_case["workflow_status"] == "reopened"
    assert reopened_case["is_locked"] is False


def test_document_and_sop_versions(client):
    from app import crud, schemas
    from app.database import SessionLocal

    with SessionLocal() as db:
        document = crud.create_attachment(
            db,
            file_name="controlled-document.pdf",
            document_title="Controlled document",
            document_type="other",
            document_version="1.0",
            status="draft",
            comment="Initial version",
        )
        document_versions = crud.list_document_versions(db, document.id)
        assert [version.version_label for version in document_versions] == ["1.0"]

        sop = crud.create_sop(
            db,
            schemas.SOPCreate(
                sop_code="SOP-TEST",
                title="Test SOP",
                version="1.0",
                owner="QA",
                effective_date=date(2026, 1, 1),
                next_review_date=date(2027, 1, 1),
                revision_reason="Initial version",
            ),
        )
        assert [version.version_label for version in crud.list_sop_versions(db, sop.id)] == ["1.0"]

        crud.update_sop(
            db,
            sop,
            schemas.SOPCreate(
                sop_code="SOP-TEST",
                title="Test SOP",
                version="1.1",
                status="Under Review",
                owner="QA",
                effective_date=date(2026, 1, 1),
                next_review_date=date(2027, 1, 1),
                revision_reason="Annual review",
            ),
        )
        assert [
            version.version_label for version in crud.list_sop_versions(db, sop.id)
        ] == ["1.1", "1.0"]


def test_literature_monitoring_crud_and_case_link(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"LIT-{suffix}",
                partner_name=f"Literature Partner {suffix}",
                partner_type="fn",
            ),
        )
        product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"LIT-PROD-{suffix}",
                product_name=f"Literature Product {suffix}",
                active_substance=f"Substance {suffix}",
            ),
        )
        substance = crud.list_substances(db)[0]

        plan = crud.create_literature_plan(
            db,
            schemas.LiteratureMonitoringPlanCreate(
                partner_id=partner.id,
                product_ids=[product.id],
                active_substance_id=substance.id,
                monitoring_sources="PubMed; eLIBRARY",
                frequency="monthly",
                search_strategy="product AND safety",
                keywords="safety, adverse event",
                start_date=date(2026, 1, 1),
            ),
        )
        assert plan.status == "active"
        assert [link.product_id for link in plan.products] == [product.id]

        search_log = crud.create_literature_search_log(
            db,
            schemas.LiteratureSearchLogCreate(
                plan_id=plan.id,
                search_date=date(2026, 2, 1),
                period_start=date(2026, 1, 1),
                period_end=date(2026, 1, 31),
                search_source="PubMed",
                publications_found=3,
                relevant_publications=1,
                result="potential_icsr",
            ),
        )
        assert search_log.partner_id == partner.id

        result = crud.create_literature_result(
            db,
            schemas.LiteratureResultCreate(
                search_log_id=search_log.id,
                plan_id=plan.id,
                publication_title="Case report from literature",
                journal_source="Journal",
                publication_year=2026,
                result_type="potential_icsr",
                pv_decision="create_icsr",
                abstract="Potential safety case.",
            ),
        )
        result = crud.get_literature_result(db, result.id)
        case = crud.create_case_from_literature_result(db, result)

        assert case.report_type == "literature"
        assert case.workflow_status == "new"
        assert crud.get_literature_result(db, result.id).linked_case_id == case.id
        audit_count = (
            db.query(crud.AuditTrail)
            .filter(crud.AuditTrail.source_module == "Literature Monitoring")
            .count()
        )
        assert audit_count >= 3


def test_prod_mode_disables_user_switch_and_docs(tmp_path, monkeypatch, database_template):
    database_path = tmp_path / "pv_prod_test.db"
    import shutil
    shutil.copy2(database_template, database_path)
    monkeypatch.setenv("PV_STORAGE_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("PV_APP_MODE", "prod")
    monkeypatch.delenv("PV_USER_SWITCH_ENABLED", raising=False)
    clear_app_modules()

    from app.config import get_settings
    from app.main import app

    settings = get_settings()
    assert settings.is_prod is True
    assert settings.user_switch_enabled is False
    assert app.docs_url is None
    assert app.openapi_url is None

    with TestClient(app) as prod_client:
        assert prod_client.get("/", follow_redirects=False).status_code == 303
        assert prod_client.get("/docs").status_code == 404

    clear_app_modules()


def test_upload_name_validation(client):
    from app.routers.placeholders import validate_upload_name

    assert validate_upload_name("report.pdf") == "report.pdf"
    try:
        validate_upload_name("script.exe")
    except ValueError as exc:
        assert "Unsupported file type" in str(exc)
    else:
        raise AssertionError("Executable uploads must be rejected.")


def test_reconciliation_recipients_use_active_flagged_contacts(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.reconciliation import contact_email_list, reconciliation_recipients

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"RECIP-{suffix}",
                partner_name=f"Recipient Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
            ),
        )
        crud.create_contract_contact(
            db,
            schemas.ContractContactCreate(
                partner_id=partner.id,
                last_name="To",
                first_name="Active",
                email=f"to-{suffix}@company.ru",
                position="PV",
                is_reconciliation_recipient=True,
            ),
        )
        crud.create_contract_contact(
            db,
            schemas.ContractContactCreate(
                partner_id=partner.id,
                last_name="Cc",
                first_name="Active",
                email=f"cc-{suffix}@company.ru",
                position="PV",
                is_reconciliation_recipient=False,
                cc_reconciliation=True,
            ),
        )
        inactive = crud.create_contract_contact(
            db,
            schemas.ContractContactCreate(
                partner_id=partner.id,
                last_name="Inactive",
                first_name="User",
                email=f"inactive-{suffix}@company.ru",
                position="PV",
                is_reconciliation_recipient=True,
            ),
        )
        inactive.is_active = False
        crud.create_contract_contact(
            db,
            schemas.ContractContactCreate(
                partner_id=partner.id,
                last_name="No",
                first_name="Email",
                email=None,
                position="PV",
                is_reconciliation_recipient=True,
            ),
        )
        crud.create_contract_contact(
            db,
            schemas.ContractContactCreate(
                partner_id=partner.id,
                last_name="NotCurrent",
                first_name="To",
                email=f"not-current-to-{suffix}@company.ru",
                position="PV",
                is_current=False,
                is_reconciliation_recipient=True,
            ),
        )
        crud.create_contract_contact(
            db,
            schemas.ContractContactCreate(
                partner_id=partner.id,
                last_name="NotCurrent",
                first_name="Cc",
                email=f"not-current-cc-{suffix}@company.ru",
                position="PV",
                is_current=False,
                is_reconciliation_recipient=False,
                cc_reconciliation=True,
            ),
        )
        db.commit()

        recipients = reconciliation_recipients(db, partner.id)
        assert contact_email_list(recipients["to"]) == [f"to-{suffix}@company.ru"]
        assert contact_email_list(recipients["cc"]) == [f"cc-{suffix}@company.ru"]


def test_partner_reconciliation_autofills_current_contact_user_and_products(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, User

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"RECON-AUTO-{suffix}",
                partner_name=f"Reconciliation Auto Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
            ),
        )
        old_contact = crud.create_contract_contact(
            db,
            schemas.ContractContactCreate(
                partner_id=partner.id,
                last_name="Old",
                first_name="Contact",
                email=f"old-{suffix}@company.ru",
                position="PV",
                is_primary=True,
                is_reconciliation_recipient=True,
                is_current=False,
            ),
        )
        current_contact = crud.create_contract_contact(
            db,
            schemas.ContractContactCreate(
                partner_id=partner.id,
                last_name="Primary",
                first_name="Person",
                email=f"primary-{suffix}@company.ru",
                position="PV",
                is_primary=True,
                is_reconciliation_recipient=True,
            ),
        )
        current_product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"RECON-CURRENT-{suffix}",
                product_name=f"Current Reconciliation Product {suffix}",
            ),
        )
        crud.create_product_registration_certificate(
            db,
            schemas.ProductRegistrationCertificateCreate(
                product_id=current_product.id,
                registration_number=f"ЛП-№(CURRENT-{suffix})-(РГ-RU)",
                registration_date=date(2026, 1, 1),
                valid_until=date(2027, 1, 1),
                status="active",
            ),
        )
        expired_product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"RECON-EXPIRED-{suffix}",
                product_name=f"Expired Reconciliation Product {suffix}",
            ),
        )
        legacy_duplicate_product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"RECON-LEGACY-DUP-{suffix}",
                product_name=current_product.product_name,
            ),
        )
        crud.create_product_registration_certificate(
            db,
            schemas.ProductRegistrationCertificateCreate(
                product_id=legacy_duplicate_product.id,
                registration_number=f"П N{suffix}",
                status="active",
            ),
        )
        inactive_product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"RECON-INACTIVE-{suffix}",
                product_name=f"Inactive Reconciliation Product {suffix}",
                is_active=False,
            ),
        )
        crud.create_product_registration_certificate(
            db,
            schemas.ProductRegistrationCertificateCreate(
                product_id=inactive_product.id,
                registration_number=f"RU-INACTIVE-{suffix}",
                registration_date=date(2026, 1, 1),
                valid_until=date(2027, 1, 1),
                status="active",
            ),
        )
        expired_ru_product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"RECON-EXPIRED-RU-{suffix}",
                product_name=f"Expired RU Reconciliation Product {suffix}",
            ),
        )
        crud.create_product_registration_certificate(
            db,
            schemas.ProductRegistrationCertificateCreate(
                product_id=expired_ru_product.id,
                registration_number=f"RU-EXPIRED-{suffix}",
                registration_date=date(2025, 1, 1),
                valid_until=date(2025, 12, 31),
                status="active",
            ),
        )
        crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=partner.id,
                product_id=current_product.id,
                contract_type="pharmacovigilance_agreement",
                contract_number=f"PV-CURRENT-{suffix}",
                contract_date=date(2026, 1, 1),
                valid_until=date(2027, 1, 1),
            ),
        )
        crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=partner.id,
                product_id=legacy_duplicate_product.id,
                contract_type="pharmacovigilance_agreement",
                contract_number=f"PV-LEGACY-DUP-{suffix}",
                contract_date=date(2026, 1, 1),
                valid_until=date(2027, 1, 1),
            ),
        )
        crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=partner.id,
                product_id=expired_ru_product.id,
                contract_type="pharmacovigilance_agreement",
                contract_number=f"PV-EXPIRED-RU-{suffix}",
                contract_date=date(2026, 1, 1),
                valid_until=date(2027, 1, 1),
            ),
        )
        crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=partner.id,
                product_id=inactive_product.id,
                contract_type="pharmacovigilance_agreement",
                contract_number=f"PV-INACTIVE-{suffix}",
                contract_date=date(2026, 1, 1),
                valid_until=date(2027, 1, 1),
            ),
        )
        crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=partner.id,
                product_id=expired_product.id,
                contract_type="pharmacovigilance_agreement",
                contract_number=f"PV-EXPIRED-{suffix}",
                contract_date=date(2025, 1, 1),
                valid_until=date(2025, 12, 31),
            ),
        )
        current_user = db.query(User).filter(User.is_active.is_(True)).order_by(User.created_at.asc()).first()
        partner_id = partner.id
        current_contact_id = current_contact.id
        old_contact_id = old_contact.id
        current_product_name = current_product.product_name
        expired_product_name = expired_product.product_name
        inactive_product_name = inactive_product.product_name
        expired_ru_product_name = expired_ru_product.product_name
        current_user_name = current_user.full_name or current_user.email

    page = client.get(
        f"/partner-reconciliation?partner_id={partner_id}"
        "&period_start=2026-01-01&period_end=2026-01-31"
    )
    assert page.status_code == 200, page.text
    assert "Primary Person" in page.text
    assert f"primary-{suffix}@company.ru" in page.text
    assert 'name="contact_id"' not in page.text
    assert f'value="{old_contact_id}"' not in page.text
    assert f'name="prepared_by" value="{current_user_name}"' in page.text
    assert current_product_name in page.text
    assert expired_product_name not in page.text
    assert inactive_product_name not in page.text
    assert expired_ru_product_name not in page.text
    assert f"ЛП-№(CURRENT-{suffix})-(РГ-RU)" in page.text
    assert f"П N{suffix}" not in page.text
    assert 'name="product_id"' not in page.text
    assert '<select class="form-select" name="document_format"' not in page.text
    assert "Язык документа и письма" in page.text
    assert 'id="reconciliation-language" name="language"' in page.text
    assert '<option value="ru" selected>Русский</option>' in page.text
    assert '<option value="en" >English</option>' in page.text
    assert page.text.count("Сформировать документ и письмо") == 1
    assert 'type="submit" name="language"' not in page.text
    assert 'name="document_format" value="pdf"' in page.text
    assert "Русская версия" not in page.text
    assert "English version" not in page.text
    assert "Текущий месяц" in page.text
    assert "Прошлый месяц" in page.text
    assert "Прошлый квартал" in page.text
    assert 'class="recon-metric-grid"' not in page.text
    assert 'data-bs-target="#recon-discrepancies"' not in page.text
    assert "Подставляется автоматически" not in page.text
    assert "Все действующие препараты партнёра" not in page.text
    assert 'class="recon-builder-step"' not in page.text
    assert "Только с действующим РУ" in page.text
    assert 'class="recon-product-list"' in page.text
    assert "Результат" not in page.text
    assert "Дополнительные действия" not in page.text
    assert 'class="surface recon-advanced' not in page.text
    assert "Обновить предварительный просмотр" in page.text
    assert "Сохранить" in page.text
    assert 'formaction="/partner-reconciliation/save"' in page.text

    response = post_current(client,
        "/partner-reconciliation/save",
        data={
            "expected_preview_version": Forms(page.text).forms.get("/partner-reconciliation/save", {}).get("expected_preview_version", "0" * 64),
            "partner_id": partner_id,
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "contact_id": old_contact_id,
            "language": "ru",
            "reconciliation_type": "periodic",
            "document_format": "xlsx",
            "prepared_by": "Manual Override",
            "products": expired_product_name,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        reconciliation = (
            db.query(PartnerReconciliation)
            .filter(PartnerReconciliation.partner_id == partner_id)
            .order_by(PartnerReconciliation.created_at.desc())
            .first()
        )
        assert reconciliation.contact_id == current_contact_id
        assert reconciliation.contact_name == "Primary Person"
        assert reconciliation.prepared_by == current_user_name
        assert reconciliation.products == current_product_name


def test_reconciliation_prefers_unambiguous_modern_registration_number():
    from types import SimpleNamespace

    from app.reconciliation import current_product_registration

    legacy = SimpleNamespace(
        registration_number="П N 00001",
        registration_date=None,
        valid_until=None,
        status="active",
        is_deleted=False,
        created_at=None,
    )
    modern = SimpleNamespace(
        registration_number="ЛП-000001-(РГ-RU)",
        registration_date=None,
        valid_until=None,
        status="active",
        is_deleted=False,
        created_at=None,
    )
    product = SimpleNamespace(
        product_name="Препарат с заменённым РУ",
        registration_certificates=[legacy, modern],
    )

    assert current_product_registration(product, on_date=date(2026, 8, 14)) is modern


def test_partner_reconciliation_reports_ambiguous_current_registration(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import PartnerReconciliation

    suffix = uuid4().hex[:8]
    registration_numbers = [f"П N {suffix}-1", f"П N {suffix}-2"]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"RECON-RU-ERROR-{suffix}",
                partner_name=f"Ambiguous RU Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
            ),
        )
        for index, registration_number in enumerate(registration_numbers, start=1):
            product = crud.create_product(
                db,
                schemas.ProductCreate(
                    product_code=f"RECON-RU-ERROR-{suffix}-{index}",
                    product_name=f"Ambiguous RU Product {suffix}",
                ),
            )
            crud.create_product_registration_certificate(
                db,
                schemas.ProductRegistrationCertificateCreate(
                    product_id=product.id,
                    registration_number=registration_number,
                    status="active",
                ),
            )
            crud.create_contract(
                db,
                schemas.ContractCreate(
                    partner_id=partner.id,
                    product_id=product.id,
                    contract_type="pharmacovigilance_agreement",
                    contract_number=f"PV-RU-ERROR-{suffix}-{index}",
                    contract_date=date(2020, 1, 1),
                    valid_until=date(2099, 1, 1),
                ),
            )
        partner_id = partner.id

    page = client.get(
        f"/partner-reconciliation?partner_id={partner_id}"
        "&period_start=2026-07-01&period_end=2026-07-31"
    )
    assert page.status_code == 200, page.text
    assert "Не удалось определить актуальное РУ" in page.text
    assert "несколько равнозначных действующих РУ" in page.text
    assert all(number in page.text for number in registration_numbers)
    assert "Открыть препараты партнёра" in page.text
    assert 'class="recon-preview-document"' not in page.text

    response = post_current(client,
        "/partner-reconciliation/save",
        data={
            "expected_preview_version": Forms(page.text).forms.get("/partner-reconciliation/save", {}).get("expected_preview_version", "0" * 64),
            "partner_id": partner_id,
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
            "language": "ru",
            "document_format": "pdf",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    with SessionLocal() as db:
        assert (
            db.query(PartnerReconciliation)
            .filter(PartnerReconciliation.partner_id == partner_id)
            .count()
            == 0
        )


def test_partner_reconciliation_lists_only_scheduled_partners(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    partner_names = {
        "monthly": f"Monthly Reconciliation Partner {suffix}",
        "quarterly": f"Quarterly Reconciliation Partner {suffix}",
        "not_conducted": f"No Reconciliation Partner {suffix}",
    }
    with SessionLocal() as db:
        for frequency, partner_name in partner_names.items():
            crud.create_partner(
                db,
                schemas.PartnerCreate(
                    partner_code=f"RECON-LIST-{frequency.replace('_', '-')}-{suffix}",
                    partner_name=partner_name,
                    partner_type="fn",
                    reconciliation_frequency=frequency,
                ),
            )

    page = client.get("/partner-reconciliation")
    assert page.status_code == 200, page.text
    assert partner_names["monthly"] in page.text
    assert partner_names["quarterly"] in page.text
    assert partner_names["not_conducted"] not in page.text
    assert 'class="surface recon-schedule-panel"' in page.text
    assert "План сверок" in page.text
    assert "Требуется сверка" in page.text
    assert "Месяц" in page.text
    assert "Квартал" in page.text
    assert 'name="document_format" value="xlsx"' not in page.text


def test_partner_reconciliation_schedule_uses_due_period_and_existing_form():
    from types import SimpleNamespace

    from app.routers.partner_reconciliation import reconciliation_schedule_rows

    monthly_partner = SimpleNamespace(
        id="monthly-partner",
        partner_name="Monthly partner",
        reconciliation_frequency="monthly",
    )
    quarterly_partner = SimpleNamespace(
        id="quarterly-partner",
        partner_name="Quarterly partner",
        reconciliation_frequency="quarterly",
    )
    completed = SimpleNamespace(
        id="completed-form",
        partner_id=monthly_partner.id,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
    )

    rows = reconciliation_schedule_rows(
        partners=[monthly_partner, quarterly_partner],
        reconciliations=[completed],
        today=date(2026, 8, 14),
    )

    quarterly_row, monthly_row = rows
    assert quarterly_row["period_start"] == date(2026, 4, 1)
    assert quarterly_row["period_end"] == date(2026, 6, 30)
    assert quarterly_row["completed"] is False
    assert "partner_id=quarterly-partner" in quarterly_row["href"]
    assert monthly_row["period_start"] == date(2026, 7, 1)
    assert monthly_row["period_end"] == date(2026, 7, 31)
    assert monthly_row["completed"] is True
    assert monthly_row["href"].endswith("reconciliation_id=completed-form")


def test_contracts_support_edit_delete_and_attached_additional_agreements(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"CTR-{suffix}",
                partner_name=f"Contract Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
            ),
        )
        product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"CTR-PROD-{suffix}",
                product_name=f"Contract Product {suffix}",
            ),
        )
        base_contract = crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=partner.id,
                product_id=product.id,
                contract_type="pharmacovigilance_agreement",
                contract_number=f"PV-{suffix}",
                contract_date=date(2026, 1, 1),
                valid_until=date(2027, 1, 1),
            ),
        )
        partner_id = partner.id
        product_id = product.id
        base_contract_id = base_contract.id

    duplicate_response = post_current(client,
        "/api/contracts",
        json={
            "partner_id": partner_id,
            "product_id": product_id,
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": f"PV-DUP-{suffix}",
            "contract_date": "2026-02-01",
            "valid_until": "2027-02-01",
        },
    )
    assert duplicate_response.status_code == 409

    additional_response = post_current(client,
        "/api/contracts",
        json={
            "partner_id": partner_id,
            "product_id": product_id,
            "parent_contract_id": base_contract_id,
            "contract_type": "additional_agreement",
            "contract_number": f"DS-{suffix}",
            "contract_date": "2026-03-01",
            "valid_until": "2027-03-01",
        },
    )
    assert additional_response.status_code == 201, additional_response.text
    additional_contract = additional_response.json()
    assert additional_contract["parent_contract_id"] == base_contract_id
    assert additional_contract["partner_id"] == partner_id
    assert additional_contract["product_id"] == product_id

    blocked_delete = client.delete(f"/api/contracts/{base_contract_id}",
                                   params={"expected_version": client.get(f"/api/contracts/{base_contract_id}").json()["version"]})
    assert blocked_delete.status_code == 409
    assert "additional agreements" in blocked_delete.text

    updated_response = client.put(
        f"/api/contracts/{additional_contract['id']}",
        params={"expected_version": additional_contract["version"]},
        json={
            "partner_id": partner_id,
            "product_id": product_id,
            "parent_contract_id": base_contract_id,
            "contract_type": "additional_agreement",
            "contract_number": f"DS-EDIT-{suffix}",
            "contract_date": "2026-04-01",
            "valid_until": "2027-04-01",
        },
    )
    assert updated_response.status_code == 200, updated_response.text
    assert updated_response.json()["contract_number"] == f"DS-EDIT-{suffix}"

    delete_additional = client.delete(f"/api/contracts/{additional_contract['id']}",
                                      params={"expected_version": updated_response.json()["version"]})
    assert delete_additional.status_code == 200, delete_additional.text
    delete_base = client.delete(f"/api/contracts/{base_contract_id}",
                                params={"expected_version": client.get(f"/api/contracts/{base_contract_id}").json()["version"]})
    assert delete_base.status_code == 200, delete_base.text

    list_response = client.get("/api/contracts")
    assert list_response.status_code == 200
    remaining_ids = {contract["id"] for contract in list_response.json()}
    assert additional_contract["id"] not in remaining_ids
    assert base_contract_id not in remaining_ids


def test_contract_edit_form_can_toggle_current_status(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"CTR-ACTIVE-{suffix}",
                partner_name=f"Contract Active Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
            ),
        )
        product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"CTR-ACTIVE-PROD-{suffix}",
                product_name=f"Contract Active Product {suffix}",
            ),
        )
        contract = crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=partner.id,
                product_id=product.id,
                contract_type="pharmacovigilance_agreement",
                contract_number=f"PV-ACTIVE-{suffix}",
                contract_date=date(2020, 1, 1),
                valid_until=date(2099, 1, 1),
            ),
        )
        partner_id = partner.id
        product_id = product.id
        contract_id = contract.id

    inactive_response = post_current(client,
        f"/contracts/{contract_id}/edit",
        data={
            "expected_version": client.get(f"/api/contracts/{contract_id}").json()["version"],
            "partner_id": partner_id,
            "product_id": product_id,
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": f"PV-ACTIVE-{suffix}",
            "contract_date": "2020-01-01",
            "valid_until": "2099-01-01",
        },
        follow_redirects=False,
    )
    assert inactive_response.status_code == 303, inactive_response.text
    with SessionLocal() as db:
        contract = crud.get_contract(db, contract_id)
        assert contract.is_active is False
        assert contract.is_current is False

    active_response = post_current(client,
        f"/contracts/{contract_id}/edit",
        data={
            "expected_version": client.get(f"/api/contracts/{contract_id}").json()["version"],
            "partner_id": partner_id,
            "product_id": product_id,
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": f"PV-ACTIVE-{suffix}",
            "contract_date": "2020-01-01",
            "valid_until": "2099-01-01",
            "is_active": "true",
        },
        follow_redirects=False,
    )
    assert active_response.status_code == 303, active_response.text
    with SessionLocal() as db:
        contract = crud.get_contract(db, contract_id)
        assert contract.is_active is True
        assert contract.is_current is True


def test_contracts_page_allows_contract_without_primary_product(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"CTR-NOPROD-{suffix}",
                partner_name=f"Contract No Product Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
            ),
        )
        partner_id = partner.id

    page = client.get("/contracts")
    assert page.status_code == 200
    assert "Основной препарат" not in page.text
    assert "Reset" not in page.text
    assert "Очистить поиск" in page.text

    response = post_current(client,
        "/contracts",
        data={
            "partner_id": partner_id,
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": f"PV-NOPROD-{suffix}",
            "contract_date": "2026-01-01",
            "valid_until": "2027-01-01",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        contract = (
            db.query(crud.Contract)
            .filter(crud.Contract.contract_number == f"PV-NOPROD-{suffix}")
            .one()
        )
        assert contract.partner_id == partner_id
        assert contract.product_id is None
        assert contract.product_ids == []


def test_unlinked_products_can_assign_partner_inline(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"PRD-LINK-{suffix}",
                partner_name=f"Product Link Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
            ),
        )
        product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"PRD-LINK-{suffix}",
                product_name=f"Product Link Product {suffix}",
            ),
        )
        partner_id = partner.id
        product_id = product.id

    page = client.get("/products")
    assert page.status_code == 200
    assert f"/products/{product_id}/partner-link" in page.text

    response = post_current(client,
        f"/products/{product_id}/partner-link",
        data={"mah_partner_id": partner_id, "expected_version": product.version},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        product = crud.get_product(db, product_id)
        assert product.mah_partner_id == partner_id


def test_products_form_generates_code_and_links_active_substance(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        substance = crud.create_substance(
            db,
            schemas.SubstanceCreate(
                substance_name=f"Form Substance {suffix}",
                substance_type="active",
            ),
        )
        substance_id = substance.id

    response = post_current(client,
        "/products",
        data={
            "product_name": f"Form Product {suffix}",
            "active_substance_id": substance_id,
            "dosage_form_id": "7",
            "strength": "10 mg",
            "authorization_number": f"RU-{suffix}",
            "authorization_status": "registration_active",
            "comment": f"Comment {suffix}",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        product = (
            db.query(crud.Product)
            .filter(crud.Product.product_name == f"Form Product {suffix}")
            .one()
        )
        assert product.product_code.startswith("PRD-")
        assert product.dosage_form_id == 7
        assert product.dosage_form == "таблетки"
        assert product.dosage_form_name_en == "tablets"
        assert product.authorization_status == "registration_active"
        assert product.comment == f"Comment {suffix}"
        link = (
            db.query(crud.ProductSubstance)
            .filter(
                crud.ProductSubstance.product_id == product.id,
                crud.ProductSubstance.substance_id == substance_id,
                crud.ProductSubstance.is_deleted.is_(False),
            )
            .one()
        )
        assert link.substance_role == "active"
        assert link.is_primary is True

    page = client.get("/products")
    assert page.status_code == 200
    assert "таблетки" in page.text


def test_dosage_forms_dictionary_crud(client):
    from app import crud
    from app.database import SessionLocal

    page = client.get("/dosage-forms")
    assert page.status_code == 200, page.text
    assert "таблетки подъязычные" in page.text
    assert "/dosage-forms/1/edit" in page.text

    suffix = uuid4().hex[:8]
    response = post_current(client,
        "/dosage-forms",
        data={
            "name_ru": f"форма {suffix}",
            "name_en": f"form {suffix}",
            "group_ru": "тест",
            "group_en": "test",
            "sort_order": "99",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        dosage_form = (
            db.query(crud.DosageForm)
            .filter(crud.DosageForm.name_ru == f"форма {suffix}")
            .one()
        )
        dosage_form_id = dosage_form.id
        dosage_form_version = dosage_form.row_version
        assert dosage_form.is_active is True
        assert dosage_form.sort_order == 99

    response = post_current(client,
        f"/dosage-forms/{dosage_form_id}/edit",
        data={
            "expected_version": dosage_form_version,
            "name_ru": f"форма измененная {suffix}",
            "name_en": f"updated form {suffix}",
            "group_ru": "тест",
            "group_en": "test",
            "is_active": "true",
            "sort_order": "100",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        dosage_form_version = crud.get_dosage_form(db, dosage_form_id).row_version
    response = post_current(client,
        f"/dosage-forms/{dosage_form_id}/deactivate",
        data={"expected_version": dosage_form_version},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        dosage_form = crud.get_dosage_form(db, dosage_form_id)
        assert dosage_form.name_ru == f"форма измененная {suffix}"
        assert dosage_form.is_active is False


def test_contracts_support_multiple_products_per_contract(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"CTR-MULTI-{suffix}",
                partner_name=f"Contract Multi Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
            ),
        )
        product_one = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"CTR-MULTI-P1-{suffix}",
                product_name=f"Contract Multi Product 1 {suffix}",
            ),
        )
        product_two = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"CTR-MULTI-P2-{suffix}",
                product_name=f"Contract Multi Product 2 {suffix}",
            ),
        )
        product_unlinked = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"CTR-MULTI-P3-{suffix}",
                product_name=f"Contract Multi Product 3 {suffix}",
            ),
        )
        partner_id = partner.id
        product_one_id = product_one.id
        product_two_id = product_two.id
        product_unlinked_name = product_unlinked.product_name

    create_response = post_current(client,
        "/api/contracts",
        json={
            "partner_id": partner_id,
            "product_id": product_one_id,
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": f"PV-MULTI-{suffix}",
            "contract_date": "2026-01-01",
            "valid_until": "2027-01-01",
        },
    )
    assert create_response.status_code == 201, create_response.text
    base_contract = create_response.json()
    assert base_contract["product_ids"] == [product_one_id]
    assert base_contract["product_id"] == product_one_id

    link_response = post_current(client,
        f"/products/{product_two_id}/contract-links",
        data={"contract_id": base_contract["id"], "expected_contract_version": base_contract["version"],
              "expected_version": next(p["version"] for p in client.get("/api/products").json() if p["id"] == product_two_id)},
    )
    assert link_response.status_code == 200, link_response.text

    list_after_link = client.get("/api/contracts")
    linked_base_contract = next(
        contract
        for contract in list_after_link.json()
        if contract["id"] == base_contract["id"]
    )
    assert set(linked_base_contract["product_ids"]) == {product_one_id, product_two_id}

    products_page = client.get(f"/products?partner_id={partner_id}")
    assert products_page.status_code == 200, products_page.text
    assert f"Contract Multi Product 2 {suffix}" in products_page.text
    assert product_unlinked_name in products_page.text
    assert "product-unlinked-section" in products_page.text

    contracts_page = client.get("/contracts")
    assert contracts_page.status_code == 200
    assert f"/products?partner_id={partner_id}" in contracts_page.text

    duplicate_response = post_current(client,
        "/api/contracts",
        json={
            "partner_id": partner_id,
            "product_id": product_two_id,
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": f"PV-MULTI-DUP-{suffix}",
            "contract_date": "2026-02-01",
            "valid_until": "2027-02-01",
        },
    )
    assert duplicate_response.status_code == 409

    additional_response = post_current(client,
        "/api/contracts",
        json={
            "partner_id": partner_id,
            "product_id": product_one_id,
            "parent_contract_id": base_contract["id"],
            "contract_type": "additional_agreement",
            "contract_number": f"DS-MULTI-{suffix}",
            "contract_date": "2026-03-01",
            "valid_until": "2027-03-01",
        },
    )
    assert additional_response.status_code == 201, additional_response.text
    additional_contract = additional_response.json()
    assert additional_contract["parent_contract_id"] == base_contract["id"]
    assert set(additional_contract["product_ids"]) == {product_one_id, product_two_id}

    unlink_response = post_current(client,
        f"/products/{product_two_id}/contract-links/{base_contract['id']}/delete",
        data={"expected_contract_version": client.get(f"/api/contracts/{base_contract['id']}").json()["version"],
              "expected_version": next(p["version"] for p in client.get("/api/products").json() if p["id"] == product_two_id)},
    )
    assert unlink_response.status_code == 200, unlink_response.text


def test_partner_onboarding_wizard_saves_real_entities(client):
    from app import crud
    from app.database import SessionLocal
    from app.models import (
        Contract,
        ContractContact,
        PartnerOnboardingWizard,
        PartnerProductRegistrationLink,
        Product,
        ProductRegistrationCertificate,
    )

    suffix = uuid4().hex[:8]
    start = client.post("/partner-onboarding/new", follow_redirects=False,
                        data=Forms(client.get("/partner-onboarding/new").text).forms["/partner-onboarding/new"])
    assert start.status_code == 303
    wizard_id = start.headers["location"].split("?")[0].split("/")[2]

    response = post_current(client,
        f"/partner-onboarding/{wizard_id}/step/1",
        data={
            "partner_code": f"ONB-{suffix}",
            "partner_name": f"Onboarding Partner {suffix}",
            "partner_type": "registration_in_progress",
            "reconciliation_frequency": "monthly",
            "submit_action": "save_close",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/partners/")

    with SessionLocal() as db:
        wizard = db.query(PartnerOnboardingWizard).filter(PartnerOnboardingWizard.id == wizard_id).one()
        partner_id = wizard.partner_id
        assert wizard.current_step == 2
        assert wizard.status == "draft"

    continue_response = client.get(f"/partner-onboarding/{wizard_id}", follow_redirects=False)
    assert continue_response.status_code == 303
    assert f"/partner-onboarding/{wizard_id}/step/2" in continue_response.headers["location"]

    for index in range(2):
        response = post_current(client,
            f"/partner-onboarding/{wizard_id}/step/2",
            data={
                "last_name": f"Contact{index}",
                "first_name": "Person",
                "email": f"contact-{index}-{suffix}@company.ru",
                "position": "PV specialist",
                "contact_status": "current",
                "contact_type": "pv",
                "reconciliation_participation": "primary" if index == 0 else "cc",
                "submit_action": "save_continue",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

    response = post_current(client,
        f"/partner-onboarding/{wizard_id}/step/3",
        data={
            "contract_number": f"PV-{suffix}",
            "contract_date": "2026-07-01",
            "valid_until": "2027-07-01",
            "submit_action": "save_continue",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        base_contract = (
            db.query(Contract)
            .filter(Contract.partner_id == partner_id, Contract.contract_number == f"PV-{suffix}")
            .one()
        )
        base_contract_id = base_contract.id
        service_id = crud.list_accounting_services(db)[0].id

    response = post_current(client,
        f"/partner-onboarding/{wizard_id}/step/3",
        data={
            "parent_contract_id": base_contract_id,
            "additional_number": f"DS-{suffix}",
            "additional_date": "2026-08-01",
            "additional_valid_until": "2027-07-01",
            "submit_action": "save_continue",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = post_current(client,
        f"/partner-onboarding/{wizard_id}/step/4",
        data={
            "product_mode": "new",
            "product_name": f"Wizard Product {suffix}",
            "registration_number": f"RU-1-{suffix}",
            "authorization_status": "active",
            "contract_id": base_contract_id,
            "partner_role": "mah",
            "submit_action": "save_continue",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        product = db.query(Product).filter(Product.product_name == f"Wizard Product {suffix}").one()
        product_id = product.id

    response = post_current(client,
        f"/partner-onboarding/{wizard_id}/step/4",
        data={
            "product_mode": "existing",
            "product_id": product_id,
            "registration_number": f"RU-2-{suffix}",
            "authorization_status": "active",
            "contract_id": base_contract_id,
            "partner_role": "mah",
            "submit_action": "save_continue",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = post_current(client,
        f"/partner-onboarding/{wizard_id}/step/5",
        data={
            "reconciliation_enabled": "yes",
            "reconciliation_frequency": "monthly",
            "service_contract_id": base_contract_id,
            "service_id": service_id,
            "service_unit": "case",
            "service_price": "10.50",
            "submit_action": "save_continue",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = post_current(client,
        f"/partner-onboarding/{wizard_id}/step/7",
        data={"submit_action": "save_continue"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/partners/{partner_id}")

    with SessionLocal() as db:
        assert db.query(ContractContact).filter(ContractContact.partner_id == partner_id).count() == 2
        assert db.query(Contract).filter(Contract.partner_id == partner_id).count() == 2
        assert (
            db.query(ProductRegistrationCertificate)
            .filter(ProductRegistrationCertificate.product_id == product_id)
            .count()
            == 2
        )
        assert (
            db.query(PartnerProductRegistrationLink)
            .filter(PartnerProductRegistrationLink.partner_id == partner_id)
            .count()
            == 2
        )
        assert db.query(PartnerOnboardingWizard).filter(PartnerOnboardingWizard.id == wizard_id).one().status == "completed"

    partner_page = client.get(f"/partners/{partner_id}")
    assert partner_page.status_code == 200
    assert "Состояние подключения" in partner_page.text
    products_page = client.get(f"/products?partner_id={partner_id}")
    assert products_page.status_code == 200
    assert f"Wizard Product {suffix}" in products_page.text


def test_partner_onboarding_duplicate_warning_and_finish_without_contract(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import PartnerOnboardingWizard

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"DUP-BASE-{suffix}",
                partner_name=f"Duplicate Candidate {suffix}",
                partner_type="fn",
            ),
        )

    start = client.post("/partner-onboarding/new", follow_redirects=False,
                        data=Forms(client.get("/partner-onboarding/new").text).forms["/partner-onboarding/new"])
    wizard_id = start.headers["location"].split("?")[0].split("/")[2]
    duplicate_response = post_current(client,
        f"/partner-onboarding/{wizard_id}/step/1",
        data={
            "partner_code": f"DUP-NEW-{suffix}",
            "partner_name": f"Duplicate Candidate {suffix}",
            "partner_type": "registration_in_progress",
            "reconciliation_frequency": "not_conducted",
            "submit_action": "save_continue",
        },
    )
    assert duplicate_response.status_code == 200
    assert "Похожие партнёры" in duplicate_response.text

    response = post_current(client,
        f"/partner-onboarding/{wizard_id}/step/1",
        data={
            "partner_code": f"DUP-NEW-{suffix}",
            "partner_name": f"Duplicate Candidate {suffix}",
            "partner_type": "registration_in_progress",
            "reconciliation_frequency": "not_conducted",
            "duplicate_ack": "true",
            "submit_action": "save_continue",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        wizard = db.query(PartnerOnboardingWizard).filter(PartnerOnboardingWizard.id == wizard_id).one()
        partner_id = wizard.partner_id

    for step in [2, 3, 4, 5, 6]:
        skip = post_current(client,
            f"/partner-onboarding/{wizard_id}/step/{step}",
            data={"submit_action": "skip"},
            follow_redirects=False,
        )
        assert skip.status_code == 303

    finish = post_current(client,
        f"/partner-onboarding/{wizard_id}/step/7",
        data={"submit_action": "save_continue"},
        follow_redirects=False,
    )
    assert finish.status_code == 303
    assert finish.headers["location"].startswith(f"/partners/{partner_id}")


def test_partner_detail_and_schema_exclude_removed_legacy_fields(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"DETAIL-{suffix}",
                partner_name=f"Detail Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
                address="Старый адрес",
            ),
        )
        partner_id = partner.id

    page = client.get(f"/partners/{partner_id}")
    assert page.status_code == 200
    assert 'name="address"' in page.text
    for field_name in (
        "country_code",
        "region",
        "email",
        "phone",
        "pv_responsible_person",
        "sdea_required",
    ):
        assert f'name="{field_name}"' not in page.text

    response = post_current(client,
        f"/partners/{partner_id}/edit",
        data={
            "expected_version": client.get(f"/api/partners/{partner_id}").json()["version"],
            "partner_code": f"DETAIL-{suffix}",
            "partner_name": f"Detail Partner {suffix}",
            "partner_type": "fn",
            "reconciliation_frequency": "monthly",
            "address": "Новый адрес",
            "detail_form": "true",
            "return_to": f"/partners/{partner_id}",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        saved = crud.get_partner(db, partner_id)
        assert saved.address == "Новый адрес"
        for field_name in (
            "country_code",
            "region",
            "email",
            "phone",
            "pv_responsible_person",
            "sdea_required",
        ):
            assert not hasattr(saved, field_name)

    partner_schema = schemas.PartnerRead.model_json_schema()
    for field_name in (
        "country_code",
        "region",
        "email",
        "phone",
        "pv_responsible_person",
        "sdea_required",
    ):
        assert field_name not in partner_schema["properties"]


def test_psmf_partner_source_uses_only_current_partner_fields(client):
    from types import SimpleNamespace

    from app import crud, psmf, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"PSMF-{suffix}",
                partner_name=f"PSMF Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
                address="Partner address",
            ),
        )
        source = psmf.source_data_for_block(
            db,
            SimpleNamespace(source_module="partners"),
            partner,
        )

    assert source["rows"] == [
        ("Код партнера", f"PSMF-{suffix}".upper()),
        ("Название", f"PSMF Partner {suffix}"),
        ("Адрес", "Partner address"),
    ]


def test_partner_onboarding_uses_requested_fast_scenario_fields(client):
    suffix = uuid4().hex[:8]
    start = client.post("/partner-onboarding/new", follow_redirects=False,
                        data=Forms(client.get("/partner-onboarding/new").text).forms["/partner-onboarding/new"])
    wizard_id = start.headers["location"].split("?")[0].split("/")[2]
    saved = post_current(client,
        f"/partner-onboarding/{wizard_id}/step/1",
        data={
            "partner_code": f"FIELDS-{suffix}",
            "partner_name": f"Fields Partner {suffix}",
            "partner_type": "fn",
            "reconciliation_frequency": "monthly",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303

    step_1 = client.get(f"/partner-onboarding/{wizard_id}/step/1").text
    assert all(label in step_1 for label in ("Код партнёра", "Название", "Статус", "Частота сверки"))
    assert "Ответственный по ФН" not in step_1
    assert "SDEA нужен" not in step_1

    step_2 = client.get(f"/partner-onboarding/{wizard_id}/step/2").text
    assert all(label in step_2 for label in ("Статус контакта", "Актуальный", "Добавочный номер", "Сайт компании", "Участие в сверке сообщений"))

    step_3 = client.get(f"/partner-onboarding/{wizard_id}/step/3").text
    assert all(label in step_3 for label in ("Тип договора", "Основной договор", "Номер", "Дата", "Действителен до"))
    assert "Файл договора или ДС" not in step_3

    step_4 = client.get(f"/partner-onboarding/{wizard_id}/step/4").text
    assert all(label in step_4 for label in ("Торговое наименование", "Наименование RU", "Наименование EN", "АТХ / ATC-код", "ЛФ на русском", "Группа на английском", "Дозировка", "Комментарий"))
    assert "Статус РУ" not in step_4

    step_5 = client.get(f"/partner-onboarding/{wizard_id}/step/5").text
    assert all(label in step_5 for label in ("Услуги и цены", "Договор", "Услуга", "Ед.", "Цена", "Комментарий к услуге"))
    assert "E2B" not in step_5


def test_substances_support_edit_delete_and_archive_product_links(client):
    from app import crud, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"SUB-PROD-{suffix}",
                product_name=f"Substance Product {suffix}",
            ),
        )
        product_id = product.id

    create_response = post_current(client,
        "/api/substances",
        json={
            "substance_name": f"Substance {suffix}",
            "inn_name": f"INN {suffix}",
            "atc_code": "A00",
            "cas_number": f"CAS-{suffix}",
            "substance_type": "active",
        },
    )
    assert create_response.status_code == 201, create_response.text
    substance = create_response.json()

    link_response = post_current(client,
        "/api/product-substances",
        json={
            "product_id": product_id,
            "substance_id": substance["id"],
            "substance_role": "active",
            "is_primary": True,
        },
    )
    assert link_response.status_code == 201, link_response.text

    update_response = client.put(
        f"/api/substances/{substance['id']}",
        params={"expected_version": substance["version"]},
        json={
            "substance_name": f"Updated Substance {suffix}",
            "inn_name": f"Updated INN {suffix}",
            "atc_code": "B00",
            "cas_number": f"UPDATED-CAS-{suffix}",
            "substance_type": "active",
        },
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["substance_name"] == f"Updated Substance {suffix}"

    delete_response = client.delete(f"/api/substances/{substance['id']}",
                                    params={"expected_version": update_response.json()["version"]})
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["is_active"] is False

    list_response = client.get("/api/substances")
    assert list_response.status_code == 200
    assert substance["id"] not in {row["id"] for row in list_response.json()}

    with SessionLocal() as db:
        link = (
            db.query(crud.ProductSubstance)
            .filter(
                crud.ProductSubstance.product_id == product_id,
                crud.ProductSubstance.substance_id == substance["id"],
            )
            .one()
        )
        assert link.is_deleted is True
        assert link.is_active is False


def outlook_form(client, reconciliation_id, action):
    from tests.test_concurrent_forms import Forms
    return Forms(client.get(f"/partner-reconciliation?reconciliation_id={reconciliation_id}").text).forms[
        f"/partner-reconciliation/{reconciliation_id}/{action}"]


def test_outlook_draft_is_not_created_without_to_recipients(client, monkeypatch, tmp_path):
    from app import outlook_service
    from app.database import SessionLocal

    reconciliation_id = create_saved_reconciliation(
        tmp_path,
        partner_suffix="no-to",
        contact_email=None,
    )
    called = {"draft": False}

    def fake_create_draft(**kwargs):
        called["draft"] = True
        return {"id": "MSG-NO-TO"}

    monkeypatch.setattr(outlook_service, "is_configured", lambda: True)
    monkeypatch.setattr(outlook_service, "is_authorized", lambda user_key="default": True)
    monkeypatch.setattr(outlook_service, "create_outlook_draft", fake_create_draft)

    response = post_current(client,
        f"/partner-reconciliation/{reconciliation_id}/outlook-draft",
        data={**outlook_form(client, reconciliation_id, "outlook-draft"), "email_subject": "Subject", "email_body": "Body"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert called["draft"] is False
    with SessionLocal() as db:
        from app import crud

        reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
        assert reconciliation.outlook_message_id is None
        assert reconciliation.reconciliation_status == "generated"


def test_outlook_draft_creates_message_and_attaches_document(client, monkeypatch, tmp_path):
    from app import crud, outlook_service
    from app.database import SessionLocal

    reconciliation_id = create_saved_reconciliation(
        tmp_path,
        partner_suffix="draft",
        contact_email="draft-to@company.ru",
    )
    attached = {}

    monkeypatch.setattr(outlook_service, "is_configured", lambda: True)
    monkeypatch.setattr(outlook_service, "is_authorized", lambda user_key="default": True)
    monkeypatch.setattr(
        outlook_service,
        "create_outlook_draft",
        lambda **kwargs: {"id": "MSG-123", "webLink": "https://outlook.example/draft"},
    )

    def fake_add_attachment(**kwargs):
        attached.update(kwargs)
        assert kwargs["file_path"]
        return {"id": "ATT-1"}

    monkeypatch.setattr(outlook_service, "add_attachment_to_draft", fake_add_attachment)

    response = post_current(client,
        f"/partner-reconciliation/{reconciliation_id}/outlook-draft",
        data={**outlook_form(client, reconciliation_id, "outlook-draft"), "email_subject": "Subject", "email_body": "Body"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert attached["message_id"] == "MSG-123"
    with SessionLocal() as db:
        reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
        assert reconciliation.outlook_message_id == "MSG-123"
        assert reconciliation.outlook_draft_web_link == "https://outlook.example/draft"
        assert reconciliation.outlook_status == "draft_created"
        assert reconciliation.reconciliation_status == "outlook_draft_created"
        assert reconciliation.email_to == "draft-to@company.ru"
        audit_count = (
            db.query(crud.AuditTrail)
            .filter(
                crud.AuditTrail.entity_id == reconciliation_id,
                crud.AuditTrail.action == "outlook_draft_created",
            )
            .count()
        )
        assert audit_count > 0


def test_outlook_graph_error_is_saved_on_reconciliation(client, monkeypatch, tmp_path):
    from app import crud, outlook_service
    from app.database import SessionLocal

    reconciliation_id = create_saved_reconciliation(
        tmp_path,
        partner_suffix="graph-error",
        contact_email="error-to@company.ru",
    )

    monkeypatch.setattr(outlook_service, "is_configured", lambda: True)
    monkeypatch.setattr(outlook_service, "is_authorized", lambda user_key="default": True)

    def raise_graph_error(**kwargs):
        raise outlook_service.OutlookGraphError("Graph unavailable")

    monkeypatch.setattr(outlook_service, "create_outlook_draft", raise_graph_error)

    response = post_current(client,
        f"/partner-reconciliation/{reconciliation_id}/outlook-draft",
        data={**outlook_form(client, reconciliation_id, "outlook-draft"), "email_subject": "Subject", "email_body": "Body"},
        follow_redirects=False,
    )
    assert response.status_code == 409
    with SessionLocal() as db:
        reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
        assert reconciliation.reconciliation_status == "generated"
        assert reconciliation.outlook_status == "draft_pending"
        assert "Outlook" in reconciliation.outlook_error


def test_sent_reconciliation_cannot_be_sent_again(client, monkeypatch, tmp_path):
    from app import crud, outlook_service
    from app.database import SessionLocal

    reconciliation_id = create_saved_reconciliation(
        tmp_path,
        partner_suffix="sent",
        contact_email="sent-to@company.ru",
    )
    with SessionLocal() as db:
        reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
        reconciliation.reconciliation_status = "sent"
        reconciliation.outlook_status = "sent"
        reconciliation.outlook_message_id = "MSG-SENT"
        reconciliation.email_to = "sent-to@company.ru"
        db.commit()

    called = {"send": False}

    def fake_send(**kwargs):
        called["send"] = True

    monkeypatch.setattr(outlook_service, "is_configured", lambda: True)
    monkeypatch.setattr(outlook_service, "is_authorized", lambda user_key="default": True)
    monkeypatch.setattr(outlook_service, "send_outlook_message", fake_send)

    response = post_current(client,
        f"/partner-reconciliation/{reconciliation_id}/outlook-send",
        data=outlook_form(client, reconciliation_id, "outlook-send"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert called["send"] is False


def test_psmf_builder_seed_blocks_and_draft_docx(client, tmp_path):
    from pathlib import Path

    from app import psmf as psmf_service
    from app.database import SessionLocal
    from app.models import Partner, PSMFBlock, PSMFComponent, PSMFPartnerBuild

    psmf_service.PSMF_DOCUMENT_DIR = tmp_path / "psmf"

    structure_page = client.get("/psmf?tab=structure")
    assert structure_page.status_code == 200, structure_page.text
    assert "Главы МФСФ" in structure_page.text

    with SessionLocal() as db:
        partner = create_test_partner(db, "PSMF-BUILDER")
        partner_id = partner.id
        legacy_component = (
            db.query(PSMFComponent)
            .filter(
                PSMFComponent.code == "Приложение Б-1.1",
                PSMFComponent.is_deleted.is_(False),
            )
            .first()
        )
        partner_block = (
            db.query(PSMFBlock)
            .join(PSMFComponent)
            .filter(
                PSMFComponent.code == "Приложение Б-1",
                PSMFBlock.original_code == "Приложение Б-1.1",
                PSMFBlock.block_type == "PARTNER_BLOCK",
                PSMFBlock.is_deleted.is_(False),
            )
            .one()
        )
        assert legacy_component is None
        assert partner_block.title_ru
        partner_component_id = partner_block.component_id
        partner_block_id = partner_block.id
        partner_component = db.query(PSMFComponent).filter(PSMFComponent.id == partner_component_id).one()
        partner_root_id = partner_component.parent_component_id

    structure_page = client.get(f"/psmf?tab=annexes&root_component_id={partner_root_id}")
    assert structure_page.status_code == 200, structure_page.text
    assert "Приложение Б-1.1" not in structure_page.text
    assert "Приложение Б-1" in structure_page.text
    assert (
        f"/psmf?tab=annexes&component_id={partner_component_id}" in structure_page.text
        or f"/psmf?tab=annexes&amp;component_id={partner_component_id}" in structure_page.text
    )

    component_page = client.get(f"/psmf?tab=components&component_id={partner_component_id}")
    assert component_page.status_code == 200, component_page.text
    assert (
        f"/psmf/blocks/{partner_block_id}/edit" in component_page.text
    )

    chapters_page = client.get(f"/psmf?tab=chapters&component_id={partner_component_id}")
    assert chapters_page.status_code == 200, chapters_page.text
    assert partner_block_id in chapters_page.text
    assert "Приложения" in chapters_page.text

    block_edit_page = client.get(f"/psmf/blocks/{partner_block_id}/edit")
    assert block_edit_page.status_code == 200, block_edit_page.text
    assert "К приложению" in block_edit_page.text

    partner_page = client.get(f"/psmf?tab=partner&partner_id={partner_id}")
    assert partner_page.status_code == 200, partner_page.text
    assert "Сформировать черновик (PDF + ZIP)" in partner_page.text
    assert partner_page.text.index("Выгрузка МФСФ") < partner_page.text.index("Состав МФСФ")

    response = post_current(client,
        "/psmf/partner-build",
        data={"partner_id": partner_id, "build_type": "DRAFT"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        build = (
            db.query(PSMFPartnerBuild)
            .filter(PSMFPartnerBuild.partner_id == partner_id)
            .order_by(PSMFPartnerBuild.generated_at.desc())
            .first()
        )
        assert build.build_type == "DRAFT"
        assert build.docx_file_path
        assert Path(build.docx_file_path).exists()
        assert build.pdf_file_path
        assert Path(build.pdf_file_path).read_bytes().startswith(b"%PDF")
        assert build.package_file_path
        assert Path(build.package_file_path).exists()

        pdf_response = client.get(f"/psmf/builds/{build.id}/download.pdf")
        assert pdf_response.status_code == 200
        assert pdf_response.headers["content-type"].startswith("application/pdf")
        assert pdf_response.content.startswith(b"%PDF")

        package_response = client.get(f"/psmf/builds/{build.id}/download-package")
        assert package_response.status_code == 200
        assert package_response.headers["content-type"].startswith("application/zip")
        with ZipFile(BytesIO(package_response.content)) as archive:
            package_names = set(archive.namelist())
        assert "3. Приложения Партнера/Приложение З-1.pdf" in package_names
        assert "3. Приложения Партнера/Приложение З-4.pdf" in package_names
        assert "3. Приложения Партнера/Приложение Ж-4.pdf" in package_names
        assert "3. Приложения Партнера/Приложение З-3.pdf" in package_names
        assert "3. Приложения Партнера/Приложение З-5.pdf" in package_names
        assert "3. Приложения Партнера/Приложение З-6.pdf" in package_names
        assert "3. Приложения Партнера/Приложение К.pdf" in package_names
        assert "3. Приложения Партнера/Приложение Е-1.pdf" not in package_names
        assert "3. Приложения Партнера/Приложение Л-1.pdf" not in package_names
        assert not any(name.endswith("_п.pdf") for name in package_names)

        partner_page = client.get(f"/psmf?tab=partner&partner_id={partner_id}")
        assert "Скачать единый PDF" in partner_page.text
        assert "Скачать комплект ZIP" in partner_page.text


def test_psmf_page_without_selected_partner_handles_bound_partner_blocks(client):
    from app.database import SessionLocal
    from app.models import PSMFBlock

    client.get("/psmf")
    with SessionLocal() as db:
        partner = create_test_partner(db, "PSMF-BOUND-BLOCK")
        block = (
            db.query(PSMFBlock)
            .filter(
                PSMFBlock.block_type == "PARTNER_BLOCK",
                PSMFBlock.is_deleted.is_(False),
            )
            .first()
        )
        block.partner_id = partner.id
        db.commit()

    response = client.get("/psmf?tab=structure")
    assert response.status_code == 200, response.text
    assert "Главы МФСФ" in response.text


def test_psmf_pdf_package_appends_partner_pdf_attachments(client, tmp_path):
    from pathlib import Path

    from pypdf import PdfReader, PdfWriter

    from app import psmf as psmf_service
    from app.database import SessionLocal
    from app.models import Attachment, PSMFBlock, PSMFComponent, PSMFPartnerBuild

    psmf_service.PSMF_DOCUMENT_DIR = tmp_path / "psmf"

    client.get("/psmf")
    with SessionLocal() as db:
        partner = create_test_partner(db, "PSMF-PDF-ATTACHMENT")
        partner_id = partner.id
        block = (
            db.query(PSMFBlock)
            .join(PSMFComponent)
            .filter(
                PSMFComponent.code == "Приложение Б-3",
                PSMFBlock.code == "B-3-PARTNER-CONTRACTS",
                PSMFBlock.is_deleted.is_(False),
            )
            .one()
        )
        block_id = block.id

    source_pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(source_pdf)
    response = post_current(client,
        f"/psmf/blocks/{block_id}/attachments",
        data={"partner_id": partner_id, "document_title": "Договор по фармаконадзору"},
        files={"document_file": ("contract.pdf", source_pdf.getvalue(), "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert "message=" in response.headers["location"], response.headers["location"]
    with SessionLocal() as db:
        attachment = (
            db.query(Attachment)
            .filter(
                Attachment.related_object_type == "PSMFBlock",
                Attachment.related_object_id == block_id,
                Attachment.partner_id == partner_id,
            )
            .one()
        )
        uploaded_path = (Path.cwd() / attachment.storage_path).resolve()

    response = post_current(client,
        "/psmf/partner-build",
        data={"partner_id": partner_id, "build_type": "DRAFT"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        build = (
            db.query(PSMFPartnerBuild)
            .filter(PSMFPartnerBuild.partner_id == partner_id)
            .order_by(PSMFPartnerBuild.generated_at.desc())
            .first()
        )
        package_bytes = Path(build.package_file_path).read_bytes()

    with ZipFile(BytesIO(package_bytes)) as archive:
        annex_pdf = archive.read("3. Приложения Партнера/Приложение Б-3.1.pdf")
    assert len(PdfReader(BytesIO(annex_pdf)).pages) >= 2
    uploaded_path.unlink(missing_ok=True)


def test_psmf_can_skip_empty_partner_blocks(client):
    from app import psmf as psmf_service
    from app.database import SessionLocal
    from app.models import Partner, PSMFBlock, PSMFComponent

    client.get("/psmf")
    with SessionLocal() as db:
        partner = create_test_partner(db, "PSMF-SKIP-EMPTY")
        block = (
            db.query(PSMFBlock)
            .join(PSMFComponent)
            .filter(
                PSMFBlock.block_type == "PARTNER_BLOCK",
                PSMFBlock.is_deleted.is_(False),
                PSMFComponent.is_deleted.is_(False),
            )
            .first()
        )
        settings = psmf_service.get_psmf_settings(db)
        settings.skip_empty_partner_specific_blocks = True
        block.versions[0].content = psmf_service.DEFAULT_COMPONENT_TEXT
        db.commit()

        preview = psmf_service.build_partner_psmf(db, partner.id)
        included_block_ids = {
            block_row["block"].id
            for row in preview["rows"]
            for block_row in row["blocks"]
        }

    assert block.id not in included_block_ids


def test_psmf_splits_common_and_partner_annexes(client):
    from app.database import SessionLocal
    from app.models import Partner, PSMFBlock, PSMFComponent

    client.get("/psmf")
    with SessionLocal() as db:
        partner = create_test_partner(db, "PSMF-ANNEXES")
        partner_block = (
            db.query(PSMFBlock)
            .join(PSMFComponent)
            .filter(
                PSMFBlock.block_type == "PARTNER_BLOCK",
                PSMFBlock.is_deleted.is_(False),
                PSMFComponent.is_deleted.is_(False),
            )
            .first()
        )
        partner_id = partner.id
        component_id = partner_block.component_id
        block_id = partner_block.id
        parent_component_id = partner_block.component.parent_component_id
        common_sibling = (
            db.query(PSMFComponent)
            .filter(
                PSMFComponent.parent_component_id == parent_component_id,
                PSMFComponent.id != component_id,
                PSMFComponent.is_deleted.is_(False),
            )
            .all()
        )
        common_sibling_id = next(
            (
                component.id
                for component in common_sibling
                if not any(
                    not block.is_deleted and block.block_type == "PARTNER_BLOCK"
                    for block in component.blocks
                )
            ),
            None,
        )

    common_page = client.get(f"/psmf?tab=annexes&component_id={component_id}")
    assert common_page.status_code == 200, common_page.text
    assert block_id not in common_page.text

    partner_page = client.get(
        f"/psmf?tab=partner_annexes&partner_id={partner_id}&component_id={component_id}"
    )
    assert partner_page.status_code == 200, partner_page.text
    assert block_id in partner_page.text
    assert f'value="{parent_component_id}"' in partner_page.text
    if common_sibling_id:
        assert common_sibling_id not in partner_page.text
    assert "partner_annexes" in partner_page.text


def test_psmf_approval_can_return_component_for_revision(client):
    from app.database import SessionLocal
    from app.models import AuditTrail, PSMFComponent

    client.get("/psmf")
    with SessionLocal() as db:
        component = (
            db.query(PSMFComponent)
            .filter(PSMFComponent.is_deleted.is_(False), PSMFComponent.versions.any())
            .first()
        )
        component.status = "under_review"
        component.versions[0].status = "under_review"
        component.versions[0].is_locked = True
        component_id = component.id
        version_id = component.versions[0].id
        db.commit()
        expected_version = component.version
        expected_content_version = component.versions[0].row_version

    response = post_current(client,
        f"/psmf/components/{component_id}/return-revision",
        data={"return_comment": "Needs clarification before approval",
              "expected_version": expected_version,
              "expected_content_version": expected_content_version},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        component = db.query(PSMFComponent).filter(PSMFComponent.id == component_id).one()
        version = next(row for row in component.versions if row.id == version_id)
        audit_entry = (
            db.query(AuditTrail)
            .filter(
                AuditTrail.entity_id == component_id,
                AuditTrail.action == "returned_for_revision",
            )
            .first()
        )
        assert component.status == "draft"
        assert version.status == "draft"
        assert version.is_locked is False
        assert version.change_reason == "Needs clarification before approval"
        assert audit_entry is not None


def test_psmf_version_history_opens_archived_versions_read_only(client):
    from app.database import SessionLocal
    from app.models import PSMFBlock, PSMFBlockVersion, PSMFComponent, PSMFComponentVersion

    with SessionLocal() as db:
        component = (
            db.query(PSMFComponent)
            .filter(PSMFComponent.is_deleted.is_(False))
            .first()
        )
        current_component_version = component.versions[0]
        archived_component_version = PSMFComponentVersion(
            component_id=component.id,
            version="archive-test",
            content="Архивный текст раздела",
            status="approved",
            change_summary="Проверка истории версий",
            is_locked=True,
        )
        db.add(archived_component_version)

        block = (
            db.query(PSMFBlock)
            .filter(PSMFBlock.is_deleted.is_(False))
            .first()
        )
        current_block_version = block.versions[0]
        archived_block_version = PSMFBlockVersion(
            block_id=block.id,
            version="archive-test",
            content="Архивный текст блока",
            status="approved",
            change_summary="Проверка истории версий блока",
            is_locked=True,
        )
        db.add(archived_block_version)
        db.commit()
        component_id = component.id
        current_component_version_id = current_component_version.id
        archived_component_version_id = archived_component_version.id
        block_id = block.id
        current_block_version_id = current_block_version.id
        archived_block_version_id = archived_block_version.id

    component_page = client.get(f"/psmf?tab=chapters&component_id={component_id}")
    assert component_page.status_code == 200, component_page.text
    assert "История версий" in component_page.text
    assert f"version_id={archived_component_version_id}" in component_page.text
    assert f"version_id={current_component_version_id}" not in component_page.text

    archived_component_page = client.get(
        f"/psmf?tab=chapters&component_id={component_id}&version_id={archived_component_version_id}"
    )
    assert archived_component_page.status_code == 200, archived_component_page.text
    assert "Архивный текст раздела" in archived_component_page.text
    assert "Редактирование недоступно" in archived_component_page.text
    assert f"/psmf?tab=chapters&amp;component_id={component_id}" in archived_component_page.text

    block_page = client.get(f"/psmf/blocks/{block_id}/edit")
    assert block_page.status_code == 200, block_page.text
    assert "История версий" in block_page.text
    assert f"block_version_id={archived_block_version_id}" in block_page.text
    assert f"block_version_id={current_block_version_id}" not in block_page.text

    archived_block_page = client.get(
        f"/psmf/blocks/{block_id}/edit?block_version_id={archived_block_version_id}"
    )
    assert archived_block_page.status_code == 200, archived_block_page.text
    assert "Архивный текст блока" in archived_block_page.text
    assert "Редактирование недоступно" in archived_block_page.text
    assert f"/psmf/blocks/{block_id}/edit" in archived_block_page.text


def create_saved_reconciliation(
    tmp_path,
    *,
    partner_suffix: str,
    contact_email: str | None,
) -> str:
    from app import crud, reconciliation_documents, schemas
    from app.database import SessionLocal
    from app.reconciliation import build_reconciliation_preview

    reconciliation_documents.RECONCILIATION_DOCUMENT_DIR = tmp_path
    suffix = f"{partner_suffix}-{uuid4().hex[:8]}"
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"PARTNER-{suffix}",
                partner_name=f"Partner {suffix}",
                partner_type="fn",
                reconciliation_frequency="monthly",
            ),
        )
        contact = crud.create_contract_contact(
            db,
            schemas.ContractContactCreate(
                partner_id=partner.id,
                last_name="Contact",
                first_name="Person",
                email=contact_email,
                position="PV",
                is_reconciliation_recipient=True,
            ),
        )
        crud.create_case(
            db,
            schemas.CaseCreate(
                case_number=f"CASE-{suffix}",
                partner_id=partner.id,
                case_type="spontaneous",
                report_type="spontaneous",
                initial_received_date=date(2026, 1, 10),
                latest_received_date=date(2026, 1, 10),
                seriousness="non-serious",
                workflow_status="data_entry",
            ),
        )
        preview = build_reconciliation_preview(
            db,
            partner_id=partner.id,
            contact_id=contact.id,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )
        reconciliation = crud.create_partner_reconciliation(
            db,
            schemas.PartnerReconciliationCreate(
                partner_id=partner.id,
                contact_id=contact.id,
                period_start=date(2026, 1, 1),
                period_end=date(2026, 1, 31),
                prepared_by="Tester",
            ),
            preview["items"],
        )
        document_info = reconciliation_documents.generate_reconciliation_document_file(
            reconciliation,
            document_format="xlsx",
        )
        crud.save_partner_reconciliation_document(db, reconciliation, document_info)
        return reconciliation.id


def test_contextual_immutable_document_uses_one_registry_record_and_pins_build_version(client):
    from io import BytesIO
    from pathlib import Path

    from pypdf import PdfWriter

    from app import crud, psmf, schemas
    from app.database import SessionLocal
    from app.models import Attachment, PSMFBuildDocument, PSMFDocumentLink, PSMFPartnerBuild

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = create_test_partner(db, f"IMMUTABLE-{suffix}")
        contract = crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=partner.id,
                contract_number=f"PV-{suffix}",
                contract_date=date(2026, 1, 1),
                valid_until=date(2027, 1, 1),
                contract_type="pharmacovigilance_agreement",
            ),
        )
        partner_id = partner.id
        contract_id = contract.id

    source_pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(source_pdf)
    response = post_current(client,
        "/documents",
        data={
            "document_title": "Подписанный договор",
            "document_type": "PV agreement",
            "related_object_type": "Contract",
            "related_object_id": contract_id,
            "partner_id": partner_id,
            "document_version": "1.0",
            "document_status": "active",
            "is_signed": "true",
            "scope": "PARTNER_SPECIFIC",
            "psmf_annex_code": "Приложение Б-3.1",
            "include_in_psmf": "true",
            "inclusion_order": "20",
            "return_to": f"/contracts?partner_id={partner_id}",
        },
        files={"document_file": ("contract-v1.pdf", source_pdf.getvalue(), "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        document = (
            db.query(Attachment)
            .filter(
                Attachment.related_object_type == "Contract",
                Attachment.related_object_id == contract_id,
                Attachment.document_title == "Подписанный договор",
            )
            .one()
        )
        document_id = document.id
        original_path = (Path.cwd() / document.storage_path).resolve()
        original_version_id = document.versions[0].id
        link = db.query(PSMFDocumentLink).filter(PSMFDocumentLink.attachment_id == document.id).one()
        assert link.document_version_id == original_version_id
        assert document.page_count == 1
        build = PSMFPartnerBuild(
            partner_id=partner_id,
            build_type="FINAL",
            build_status="approved",
            version="99.0",
        )
        db.add(build)
        db.flush()
        psmf.snapshot_build_documents(db, build, partner_id=partner_id)
        db.commit()
        build_id = build.id
        document_row_version = document.version

    contracts_page = client.get(f"/contracts?partner_id={partner_id}")
    assert contracts_page.status_code == 200, contracts_page.text
    assert "Нередактируемые документы договора" in contracts_page.text
    assert "Подписанный договор" in contracts_page.text
    partner_page = client.get(f"/partners/{partner_id}")
    assert partner_page.status_code == 200, partner_page.text
    assert "Подписанный договор" in partner_page.text

    next_pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=595, height=842)
    writer.write(next_pdf)
    response = post_current(client,
        f"/documents/{document_id}/versions",
        data={
            "expected_version": document_row_version,
            "document_version": "2.0",
            "document_status": "active",
            "change_reason": "Дополнительное соглашение",
            "return_to": "/documents",
        },
        files={"document_file": ("contract-v2.pdf", next_pdf.getvalue(), "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    with SessionLocal() as db:
        document = crud.get_attachment(db, document_id)
        link = db.query(PSMFDocumentLink).filter(PSMFDocumentLink.attachment_id == document_id).one()
        pinned = db.query(PSMFBuildDocument).filter(PSMFBuildDocument.build_id == build_id).one()
        new_path = (Path.cwd() / document.storage_path).resolve()
        assert document.document_version == "2.0"
        assert len(document.versions) == 2
        assert link.document_version_id != original_version_id
        assert pinned.document_version_id == original_version_id
        assert pinned.version_label_snapshot == "1.0"
    original_path.unlink(missing_ok=True)
    new_path.unlink(missing_ok=True)
