from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient

from conftest import clear_app_modules


def test_core_pages_and_server_pagination(client):
    for path in [
        "/health",
        "/",
        "/cases?page=1&per_page=2",
        "/documents?page=1&per_page=2",
        "/sops?page=1&per_page=2",
        "/audit-log?page=1&per_page=2",
    ]:
        response = client.get(path)
        assert response.status_code == 200, path


def test_query_user_switch_is_disabled_by_default(client):
    response = client.get("/?as_user=admin@example.com")
    assert response.status_code == 200
    assert "pv_user_id" not in response.headers.get("set-cookie", "")


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
