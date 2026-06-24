from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient

from conftest import clear_app_modules


def test_core_pages_and_server_pagination(client):
    for path in [
        "/health",
        "/",
        "/training",
        "/cases?page=1&per_page=2",
        "/documents?page=1&per_page=2",
        "/literature-monitoring?page=1&per_page=2",
        "/sops?page=1&per_page=2",
        "/audit-log?page=1&per_page=2",
    ]:
        response = client.get(path)
        assert response.status_code == 200, path


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
        "safety_messages",
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
    ]
    assert [child["href"] for child in groups_by_key["safety_messages"]["children"]] == [
        "/incoming-requests",
        "/safety-reports",
        "/cases",
        "/partner-reconciliation",
    ]
    assert groups_by_key["safety_messages"]["children"][0]["label"] == "GPT analysis"
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
    response = client.get("/?as_user=admin@example.com")
    assert response.status_code == 200
    assert "pv_user_id" not in response.headers.get("set-cookie", "")


def test_incoming_requests_shows_demo_gpt_notice(client):
    response = client.get("/incoming-requests")
    assert response.status_code == 200
    assert "Демонстрационная версия страницы GPT-анализа" in response.text
    assert "локальный mock-анализ" in response.text


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
        partner_id = partner.id
        product_id = product.id

    response = client.post(
        "/incoming-requests/register",
        data={
            "source_text": f"Patient had headache after Message Product {suffix}.",
            "request_type": "safety_message",
            "partner_id": partner_id,
            "product_id": product_id,
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

    export_response = client.get("/incoming-requests/export.csv")
    assert export_response.status_code == 200
    assert "Headache" in export_response.text
    assert "Message Product" in export_response.text

    case_response = client.post(f"/api/incoming-requests/{request_id}/create-case")
    assert case_response.status_code == 201, case_response.text
    case_id = case_response.json()["id"]

    with SessionLocal() as db:
        row = crud.get_incoming_request(db, request_id)
        case = crud.get_case(db, case_id)
        assert row.case_id == case_id
        assert row.status == "converted_to_case"
        assert case.partner_id == partner_id
        assert case.case_products[0].product_id == product_id
        assert case.reactions[0].reported_term == "Headache"


def test_icsr_workflow_lock_and_reopen(client):
    response = client.post(
        "/api/cases",
        json={"case_number": "TEST-WF-001", "workflow_status": "new"},
    )
    assert response.status_code == 201, response.text
    case_id = response.json()["id"]

    response = client.patch(
        f"/api/cases/{case_id}/status",
        json={"workflow_status": "submitted"},
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
            json={"workflow_status": workflow_status, "change_reason": "workflow test"},
        )
        assert response.status_code == 200, response.text

    submitted_case = response.json()
    assert submitted_case["workflow_status"] == "submitted"
    assert submitted_case["is_locked"] is True

    response = client.post(
        f"/api/cases/{case_id}/patients",
        json={"patient_initials": "AB"},
    )
    assert response.status_code == 400
    assert "locked" in response.text.lower()

    response = client.patch(
        f"/api/cases/{case_id}/status",
        json={"workflow_status": "reopened"},
    )
    assert response.status_code == 400
    assert "Change reason is required" in response.text

    response = client.patch(
        f"/api/cases/{case_id}/status",
        json={"workflow_status": "reopened", "change_reason": "follow-up received"},
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


def test_prod_mode_disables_demo_switch_and_docs(tmp_path, monkeypatch):
    database_path = tmp_path / "pv_prod_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("PV_APP_MODE", "prod")
    monkeypatch.delenv("PV_DEMO_USER_SWITCH", raising=False)
    clear_app_modules()

    from app.config import get_settings
    from app.main import app

    settings = get_settings()
    assert settings.is_prod is True
    assert settings.demo_user_switch_enabled is False
    assert app.docs_url is None
    assert app.openapi_url is None

    with TestClient(app) as prod_client:
        assert prod_client.get("/").status_code == 200
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
                email=f"to-{suffix}@example.test",
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
                email=f"cc-{suffix}@example.test",
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
                email=f"inactive-{suffix}@example.test",
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
        db.commit()

        recipients = reconciliation_recipients(db, partner.id)
        assert contact_email_list(recipients["to"]) == [f"to-{suffix}@example.test"]
        assert contact_email_list(recipients["cc"]) == [f"cc-{suffix}@example.test"]


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

    duplicate_response = client.post(
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

    additional_response = client.post(
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

    blocked_delete = client.delete(f"/api/contracts/{base_contract_id}")
    assert blocked_delete.status_code == 409
    assert "additional agreements" in blocked_delete.text

    updated_response = client.put(
        f"/api/contracts/{additional_contract['id']}",
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

    delete_additional = client.delete(f"/api/contracts/{additional_contract['id']}")
    assert delete_additional.status_code == 200, delete_additional.text
    delete_base = client.delete(f"/api/contracts/{base_contract_id}")
    assert delete_base.status_code == 200, delete_base.text

    list_response = client.get("/api/contracts")
    assert list_response.status_code == 200
    remaining_ids = {contract["id"] for contract in list_response.json()}
    assert additional_contract["id"] not in remaining_ids
    assert base_contract_id not in remaining_ids


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

    create_response = client.post(
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

    link_response = client.post(
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

    delete_response = client.delete(f"/api/substances/{substance['id']}")
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

    response = client.post(
        f"/partner-reconciliation/{reconciliation_id}/outlook-draft",
        data={"email_subject": "Subject", "email_body": "Body"},
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
        contact_email="draft-to@example.test",
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

    response = client.post(
        f"/partner-reconciliation/{reconciliation_id}/outlook-draft",
        data={"email_subject": "Subject", "email_body": "Body"},
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
        assert reconciliation.email_to == "draft-to@example.test"
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
        contact_email="error-to@example.test",
    )

    monkeypatch.setattr(outlook_service, "is_configured", lambda: True)
    monkeypatch.setattr(outlook_service, "is_authorized", lambda user_key="default": True)

    def raise_graph_error(**kwargs):
        raise outlook_service.OutlookGraphError("Graph unavailable")

    monkeypatch.setattr(outlook_service, "create_outlook_draft", raise_graph_error)

    response = client.post(
        f"/partner-reconciliation/{reconciliation_id}/outlook-draft",
        data={"email_subject": "Subject", "email_body": "Body"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
        assert reconciliation.reconciliation_status == "error"
        assert reconciliation.outlook_status == "error"
        assert reconciliation.outlook_error == "Graph unavailable"


def test_sent_reconciliation_cannot_be_sent_again(client, monkeypatch, tmp_path):
    from app import crud, outlook_service
    from app.database import SessionLocal

    reconciliation_id = create_saved_reconciliation(
        tmp_path,
        partner_suffix="sent",
        contact_email="sent-to@example.test",
    )
    with SessionLocal() as db:
        reconciliation = crud.get_partner_reconciliation(db, reconciliation_id)
        reconciliation.reconciliation_status = "sent"
        reconciliation.outlook_status = "sent"
        reconciliation.outlook_message_id = "MSG-SENT"
        reconciliation.email_to = "sent-to@example.test"
        db.commit()

    called = {"send": False}

    def fake_send(**kwargs):
        called["send"] = True

    monkeypatch.setattr(outlook_service, "is_configured", lambda: True)
    monkeypatch.setattr(outlook_service, "is_authorized", lambda user_key="default": True)
    monkeypatch.setattr(outlook_service, "send_outlook_message", fake_send)

    response = client.post(
        f"/partner-reconciliation/{reconciliation_id}/outlook-send",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert called["send"] is False


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
