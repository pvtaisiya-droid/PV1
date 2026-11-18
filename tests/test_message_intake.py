import re
from datetime import date

import pytest


def form_values(client, report_id=None):
    response = client.get(f"/safety-reports/{report_id}/intake" if report_id else "/safety-reports/new")
    assert response.status_code == 200
    values = dict(response.context["values"])
    values["csrf"] = re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)
    if report_id:
        values["expected_version"] = response.context["report"].version
    return values, response


def create(client, **changes):
    values, _ = form_values(client)
    values.update(received_date="2026-11-11", raw_text="Unique incoming message for this test")
    values.update(changes)
    response = client.post("/safety-reports/intake", data=values, follow_redirects=False)
    assert response.status_code == 303, response.text[:1200]
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "кейс" not in page.text.casefold()
    return page.context["report"].id, page


def test_compact_registration_persists_contact_only_when_requested(client):
    from app.database import SessionLocal
    from app.models import ContractContact, SafetyReport

    report_id, _ = create(client, sender_contact_id="__new__", sender_name="Known caller",
                          sender_email="caller@example.test", sender_phone="+7 999 111-22-33")
    with SessionLocal() as db:
        assert db.query(ContractContact).count() == 0
        report = db.get(SafetyReport, report_id)
        assert report.sender_name == "Known caller"
        assert report.reporter_name is None
        assert report.sender_contact_id is None
        assert report.received_date == date(2026, 11, 11)
        assert report.received_time is None
        assert report.source_type is None

    saved_id, page = create(client, raw_text="A different incoming report", sender_name="Saved caller",
                            sender_email="saved@example.test", save_sender_contact="true", reporter_same_as_sender="true")
    assert "Сохранить в контактных лицах для дальнейшего использования" in page.text
    with SessionLocal() as db:
        report = db.get(SafetyReport, saved_id)
        contact = db.get(ContractContact, report.sender_contact_id)
        assert contact.partner_id is None
        assert contact.display_name == "Saved caller"
        assert not contact.is_reconciliation_recipient
        assert not contact.is_pv_contact
        assert not contact.is_primary
        assert report.reporter_contact_id == contact.id
        assert report.reporter_name == report.sender_name
        assert report.reporter_email == report.sender_email
    assert client.get("/contract-contacts").status_code == 200
    assert "Saved caller" in client.get(f"/contract-contacts/{contact.id}").text


def test_contact_duplicates_use_normalized_email_phone_and_can_reuse(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import ContractContact, SafetyReport

    with SessionLocal() as db:
        contact = crud.create_contract_contact(db, schemas.ContractContactCreate(
            display_name="Existing caller", email="Caller@Example.Test", mobile_phone="8 (999) 111-22-33"))
        contact_id = contact.id
    values, _ = form_values(client)
    values.update(received_date="2026-11-11", raw_text="Message with duplicate sender", save_sender_contact="true",
                  sender_name="Caller", sender_email=" caller@example.test ", sender_phone="+7 999 1112233")
    response = client.post("/safety-reports/intake", data=values)
    assert response.status_code == 409
    assert f'<a href="/contract-contacts/{contact_id}"' in response.text
    assert response.context["values"]["raw_text"] == values["raw_text"]
    with SessionLocal() as db:
        assert db.query(SafetyReport).count() == 0
        assert db.query(ContractContact).count() == 1
    values["sender_contact_id"] = contact_id
    response = client.post("/safety-reports/intake", data=values, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.query(ContractContact).count() == 1
        report = db.query(SafetyReport).one()
        assert report.sender_contact_id == contact_id


def test_message_duplicate_warning_confirmation_and_double_submit(client):
    from app.database import SessionLocal
    from app.models import SafetyReport

    report_id, _ = create(client, raw_text="The same   MESSAGE")
    values, _ = form_values(client)
    values.update(received_date="2026-11-12", raw_text="the same message")
    response = client.post("/safety-reports/intake", data=values)
    assert response.status_code == 409
    assert f'<a href="/safety-reports/{report_id}/intake"' in response.text
    values["allow_duplicate_message"] = "true"
    response = client.post("/safety-reports/intake", data=values, follow_redirects=False)
    assert response.status_code == 303
    again = client.post("/safety-reports/intake", data=values, follow_redirects=False)
    assert again.status_code == 303
    assert again.headers["location"].split("?")[0] == response.headers["location"].split("?")[0]
    with SessionLocal() as db:
        assert db.query(SafetyReport).count() == 2


def test_routes_share_original_files_enforce_pv_and_save_medical_due(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import Attachment, SafetyReport, SafetyReportRoute, SafetyReportRouteDocument

    with SessionLocal() as db:
        product = crud.create_product(db, schemas.ProductCreate(product_name="Intake test product"))
        product_id = product.id
    values, _ = form_values(client)
    values.update(received_date="2026-11-11", raw_text="Quality complaint with photo", product_ids=[product_id])
    response = client.post("/safety-reports/intake", data=values, files=[
        ("original_files", ("original.txt", b"shared original evidence", "text/plain")),
        ("original_files", ("renamed.txt", b"shared original evidence", "text/plain"))], follow_redirects=False)
    assert response.status_code == 303, response.text[:1000]
    page = client.get(response.headers["location"])
    report_id = page.context["report"].id
    with SessionLocal() as db:
        assert db.query(Attachment).count() == 1
        original_id = db.query(Attachment).one().id
    values, _ = form_values(client, report_id)
    values.update(routes=["quality", "medical", "other"], quality_complaints=["Повреждение упаковки", "Другое"],
                  quality_other_text="Extra complaint description", quality_batch="LOT-1", quality_expiry="11.2027",
                  quality_detected_date="2026-11-10", quality_location="Pharmacy", quality_health="yes",
                  quality_materials="yes", quality_attachment_ids=[original_id], medical_person_source="sender",
                  sender_name="Known sender", sender_email="sender@example.test")
    response = client.post(f"/safety-reports/{report_id}/intake", data=values,
                           files={"quality_files": ("same.txt", b"shared original evidence", "text/plain")}, follow_redirects=False)
    assert response.status_code == 303, response.text[:1000]
    with SessionLocal() as db:
        report = db.get(SafetyReport, report_id)
        active = {route.route_kind: route for route in report.processing_routes if route.is_active}
        assert set(active) == {"quality", "medical", "other", "pv"}
        assert active["quality"].details["batch"] == "LOT-1"
        assert active["quality"].details["expiry"] == "11.2027"
        assert active["medical"].details["due_date"] == "2026-12-11"
        assert active["medical"].details["email"] == "sender@example.test"
        assert active["pv"].details["product_ids"] == [product_id]
        assert not report.is_valid_icsr
        assert db.query(Attachment).count() == 1
        assert db.query(SafetyReportRouteDocument).count() == 1
        assert active["quality"].document_links[0].attachment_id == original_id
        quality_number = active["quality"].route_number
    values, _ = form_values(client, report_id)
    values.update(routes=["medical"], medical_due_manual="true", medical_due_date="2026-12-25", received_date="2026-11-15")
    response = client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False)
    assert response.status_code == 303
    values, _ = form_values(client, report_id)
    values.update(routes=["quality", "medical"], quality_health="no")
    assert client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        quality = db.query(SafetyReportRoute).filter_by(safety_report_id=report_id, route_kind="quality").one()
        medical = db.query(SafetyReportRoute).filter_by(safety_report_id=report_id, route_kind="medical").one()
        assert quality.route_number == quality_number
        assert quality.details["batch"] == "LOT-1"
        assert medical.details["due_date"] == "2026-12-25"


def test_distinct_reporter_and_stale_save_do_not_overwrite(client):
    from app.database import SessionLocal
    from app.models import SafetyReport

    report_id, _ = create(client, sender_name="Transmitting contact", sender_email="transmitter@example.test",
                          reporter_name="Primary source", reporter_email="primary@example.test", reporter_phone="123456789")
    first, _ = form_values(client, report_id)
    stale, _ = form_values(client, report_id)
    first.update(sender_name="Updated transmitting contact")
    assert client.post(f"/safety-reports/{report_id}/intake", data=first, follow_redirects=False).status_code == 303
    stale.update(reporter_same_as_sender="true", sender_name="Stale overwrite")
    assert client.post(f"/safety-reports/{report_id}/intake", data=stale).status_code == 409
    with SessionLocal() as db:
        report = db.get(SafetyReport, report_id)
        assert report.sender_name == "Updated transmitting contact"
        assert report.reporter_name == "Primary source"
        assert report.reporter_phone == "123456789"


def test_invalid_document_link_rolls_back_contact_file_and_common_changes(client):
    from app.database import SessionLocal
    from app.models import Attachment, ContractContact, SafetyReport

    report_id, _ = create(client)
    values, _ = form_values(client, report_id)
    values.update(routes=["quality"], sender_name="Rolled back contact", sender_email="rollback@example.test",
                  save_sender_contact="true", quality_materials="yes", quality_attachment_ids=["not-this-message-document"])
    response = client.post(f"/safety-reports/{report_id}/intake", data=values,
                           files={"quality_files": ("rollback.txt", b"must be rolled back", "text/plain")})
    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.query(ContractContact).count() == 0
        assert db.query(Attachment).count() == 0
        report = db.get(SafetyReport, report_id)
        assert report.sender_name is None
    from app.config import get_settings
    assert not list(get_settings().storage_root.rglob("*rollback.txt"))


def test_handoff_saves_draft_icsr_and_does_not_claim_validity(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import Case, SafetyReport

    with SessionLocal() as db:
        product = crud.create_product(db, schemas.ProductCreate(product_name="Handoff medicine"))
        product_id = product.id
    report_id, _ = create(client, sender_name="Sender", reporter_name="Primary reporter", product_ids=[product_id])
    values, _ = form_values(client, report_id)
    values.update(routes=["pv"], pv_special="true", pv_special_types=["Применение во время беременности"],
                  pv_patient_unknown="true", pv_event_unknown="true", action="handoff")
    response = client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False)
    assert response.status_code == 303, response.text[:1000]
    assert response.headers["location"].startswith("/cases/")
    with SessionLocal() as db:
        report = db.get(SafetyReport, report_id)
        assert not report.is_valid_icsr
        assert report.case.safety_report_id == report_id
        assert report.case.workflow_status == "data_entry"
        assert report.case.case_products[0].product_id == product_id
        assert report.reporter_name == "Primary reporter"
        assert db.query(Case).count() == 1
    values, _ = form_values(client, report_id)
    values.update(action="handoff")
    assert client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.query(Case).count() == 1


def test_shared_phone_requires_review_before_creating_separate_contact(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import AuditTrail, ContractContact
    with SessionLocal() as db:
        crud.create_contract_contact(db, schemas.ContractContactCreate(
            display_name="First caller", mobile_phone="8 (999) 123-45-67"))
    values, _ = form_values(client)
    values.update(received_date="2026-11-11", raw_text="A distinct person using a shared telephone",
                  sender_name="Second caller", sender_phone="+7 999 1234567", save_sender_contact="true")
    assert client.post("/safety-reports/intake", data=values).status_code == 409
    values["allow_duplicate_contact"] = "true"
    assert client.post("/safety-reports/intake", data=values, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.query(ContractContact).count() == 2
        assert db.query(AuditTrail).filter_by(entity_type="ContractContact", action="duplicate_review").count() == 1


def test_duplicate_original_file_warns_across_messages_and_rolls_back(client):
    from app.database import SessionLocal
    from app.models import Attachment, SafetyReport
    values, _ = form_values(client)
    values.update(received_date="2026-11-11", raw_text="First original")
    assert client.post("/safety-reports/intake", data=values,
        files={"original_files": ("original.txt", b"same evidence", "text/plain")}, follow_redirects=False).status_code == 303
    values, _ = form_values(client)
    values.update(received_date="2026-11-12", raw_text="Another summary")
    response = client.post("/safety-reports/intake", data=values,
        files={"original_files": ("renamed.txt", b"same evidence", "text/plain")})
    assert response.status_code == 409
    assert "Найдены возможные дубли сообщения" in response.text
    with SessionLocal() as db:
        assert db.query(SafetyReport).count() == db.query(Attachment).count() == 1


def test_unknown_receiver_and_reporter_do_not_invent_source_details(client):
    from app.database import SessionLocal
    from app.models import SafetyReport
    report_id, _ = create(client, message_direction="needs_review", reporter_unknown="true",
                          reporter_name="Inactive old name", reporter_country_code="RU", reporter_email="inactive")
    with SessionLocal() as db:
        report = db.get(SafetyReport, report_id)
        assert report.message_direction == "needs_review"
        assert report.reporter_unknown
        assert report.reporter_name is report.reporter_email is report.reporter_country_code is None


def test_historical_contact_reference_keeps_message_snapshot(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import ContractContact, SafetyReport
    with SessionLocal() as db:
        contact = crud.create_contract_contact(db, schemas.ContractContactCreate(display_name="Original name", email="old@example.test"))
        contact_id = contact.id
    report_id, _ = create(client, sender_contact_id=contact_id, reporter_same_as_sender="true")
    with SessionLocal() as db:
        contact = db.get(ContractContact, contact_id)
        contact.display_name = "Directory renamed"
        contact.email = "new@example.test"
        contact.is_deleted = True
        db.commit()
    values, page = form_values(client, report_id)
    assert contact_id in {contact["id"] for contact in page.context["contact_data"]}
    values.update(raw_subject="Common fields kept unchanged")
    assert client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        report = db.get(SafetyReport, report_id)
        assert report.sender_contact_id == contact_id
        assert report.sender_name == report.reporter_name == "Original name"
        assert report.sender_email == report.reporter_email == "old@example.test"


def test_closed_icsr_rejects_source_changes_and_rolls_back_new_contact(client):
    from app.database import SessionLocal
    from app.models import ContractContact, SafetyReport
    report_id, _ = create(client)
    values, _ = form_values(client, report_id)
    values.update(routes=["pv"], action="handoff")
    assert client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        report = db.get(SafetyReport, report_id)
        report.case.workflow_status = "closed"
        report.case.is_locked = True
        db.commit()
    values, _ = form_values(client, report_id)
    values.update(sender_name="Blocked source change", save_sender_contact="true")
    assert client.post(f"/safety-reports/{report_id}/intake", data=values).status_code == 422
    with SessionLocal() as db:
        assert db.query(ContractContact).count() == 0
        assert db.get(SafetyReport, report_id).sender_name is None


@pytest.mark.parametrize("violation", ["csrf", "permissions"])
def test_intake_rejects_unprotected_write_and_duplicate_search(client, violation):
    from tests.test_access_control_matrix import set_permissions
    from app.database import SessionLocal
    from app.models import SafetyReport
    values, _ = form_values(client)
    values.update(received_date="2026-11-11", raw_text="Write requires protection")
    if violation == "csrf":
        values.pop("csrf")
    else:
        set_permissions(["view"])
    assert client.post("/safety-reports/intake", data=values).status_code == 403
    assert client.post("/safety-reports/check-duplicates", data=values).status_code == 403
    with SessionLocal() as db:
        assert db.query(SafetyReport).count() == 0


def test_late_intake_conflict_rolls_back_routes_contacts_files_and_audit(client, monkeypatch):
    from sqlalchemy.orm.exc import StaleDataError
    from app import message_intake
    from app.database import SessionLocal
    from app.models import Attachment, AuditTrail, ContractContact, SafetyReport, SafetyReportRoute
    from app.config import get_settings
    report_id, _ = create(client)
    values, _ = form_values(client, report_id)
    values.update(routes=["quality"], quality_batch="Uncommitted", save_sender_contact="true", sender_name="Uncommitted contact")
    with SessionLocal() as db:
        before_audit = db.query(AuditTrail).count()
        before_version = db.get(SafetyReport, report_id).version
    original = message_intake.save_routes
    def late_conflict(*args, **kwargs):
        original(*args, **kwargs)
        raise StaleDataError("Synthetic late conflict")
    monkeypatch.setattr(message_intake, "save_routes", late_conflict)
    assert client.post(f"/safety-reports/{report_id}/intake", data=values,
        files={"original_files": ("late-conflict.txt", b"rollback evidence", "text/plain")}).status_code == 409
    with SessionLocal() as db:
        assert db.get(SafetyReport, report_id).version == before_version
        assert db.query(AuditTrail).count() == before_audit
        assert db.query(Attachment).count() == db.query(ContractContact).count() == db.query(SafetyReportRoute).count() == 0
    assert not list(get_settings().storage_root.rglob("*late-conflict.txt"))


def test_registration_number_and_route_numbers_survive_saved_navigation(client):
    from app.database import SessionLocal
    from app.models import SafetyReport

    values, page = form_values(client)
    manual_form = re.search(r'<form\b[^>]*\bdata-message-intake\b[^>]*>(.*?)</form>', page.text, re.S).group(1)
    assert re.findall(r'name="([^"]+)"[^>]*\brequired', manual_form) == ["received_date", "raw_text"]
    values.update(received_date="2026-11-11", raw_text="Minimal registration before processing", action="save")
    response = client.post("/safety-reports/intake", data=values, follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    report_id = page.context["report"].id
    number = page.context["report"].safety_report_number
    assert number
    values, _ = form_values(client, report_id)
    values.update(routes=["quality", "other", "medical", "pv"], action="navigate", next_step="quality")
    response = client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False)
    assert response.status_code == 303
    assert "step=quality" in response.headers["location"]
    with SessionLocal() as db:
        numbers = {row.route_kind: row.route_number for row in db.get(SafetyReport, report_id).processing_routes}
        assert len(set(numbers.values())) == 4
    values, _ = form_values(client, report_id)
    values.update(quality_batch="Saved before leaving quality", action="navigate", next_step="medical",
                  received_date="2027-01-01")
    response = client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False)
    assert response.status_code == 303
    assert "step=medical" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert page.context["values"]["quality_batch"] == "Saved before leaving quality"
    assert page.context["report"].safety_report_number == number
    assert page.context["route_numbers"] == numbers
    assert page.text.count('data-source-message') == 4
    with SessionLocal() as db:
        assert db.get(SafetyReport, report_id).case_id is None


def test_manual_route_products_requester_and_due_date_survive_common_updates(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import ContractContact, SafetyReport

    with SessionLocal() as db:
        product_ids = [crud.create_product(db, schemas.ProductCreate(product_name=f"Inherited medicine {i}")).id
                       for i in range(3)]
        contact = crud.create_contract_contact(db, schemas.ContractContactCreate(
            display_name="Requesting contact", email="requester@example.test"))
        contact_id = contact.id
    report_id, _ = create(client, product_ids=[product_ids[0]], sender_name="Original transmitter")
    values, _ = form_values(client, report_id)
    values.update(routes=["medical", "pv"], medical_person_source="manual", medical_contact_id=contact_id,
                  medical_name="Name clarified for the request", medical_email="reply@example.test",
                  medical_due_manual="true", medical_due_date="2026-12-20",
                  medical_products_overridden="true", medical_product_ids=[product_ids[1]],
                  pv_products_overridden="true", pv_product_ids=[])
    assert client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        contact = db.get(ContractContact, contact_id)
        contact.display_name = "Renamed in directory"
        contact.email = "changed@example.test"
        contact.is_deleted = True
        db.commit()
    values, page = form_values(client, report_id)
    assert contact_id in {contact["id"] for contact in page.context["contact_data"]}
    values.update(product_ids=[product_ids[2]], sender_name="Changed transmitter", received_date="2026-11-15")
    assert client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        report = db.get(SafetyReport, report_id)
        details = {row.route_kind: row.details for row in report.processing_routes}
        assert details["medical"]["contact_id"] == contact_id
        assert details["medical"]["name"] == "Name clarified for the request"
        assert details["medical"]["email"] == "reply@example.test"
        assert details["medical"]["due_date"] == "2026-12-20"
        assert details["medical"]["product_ids"] == [product_ids[1]]
        assert details["pv"]["product_ids"] == []


def test_quality_physical_sample_needs_no_photo_and_requires_special_situation_step(client):
    from app.database import SessionLocal
    from app.models import Attachment, SafetyReport

    report_id, _ = create(client)
    values, _ = form_values(client, report_id)
    values.update(routes=["quality"], quality_materials="yes", quality_health="yes",
                  quality_complaints=["Повреждение упаковки"])
    assert client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        report = db.get(SafetyReport, report_id)
        assert {route.route_kind for route in report.processing_routes if route.is_active} == {"quality", "pv"}
        assert db.query(Attachment).count() == 0
        assert not report.is_valid_icsr


def test_quality_hlt_groups_save_multiple_selections_without_health_question(client):
    from app.database import SessionLocal
    from app.models import SafetyReport

    groups = [
        ("10069172", "Проблемы с контаминацией и стерильностью продукта"),
        ("10069219", "Проблемы с физическими характеристиками продукта"),
        ("10069265", "Проблемы с маркировкой продукта"),
        ("10069291", "Проблемы с упаковкой продукта"),
        ("10069324", "Проблемы с качеством продукта, НКДР"),
        ("10077538", "Контрафактные, фальсифицированные и недоброкачественные продукты"),
        ("10077544", "Проблемы с распространением и хранением продукта"),
        ("10077545", "Проблемы с поставками и доступностью продукта"),
    ]
    report_id, _ = create(client)
    values, page = form_values(client, report_id)
    assert re.findall(r'data-meddra-hlt="(\d+)"', page.text) == [code for code, _ in groups]
    assert 'name="quality_health"' not in page.text
    assert 'data-safety-information' not in page.text
    assert 'role="tooltip"' in page.text
    values.update(routes=["quality"], quality_complaints=[label for _, label in groups])
    response = client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False)
    assert response.status_code == 303, response.text[:1200]
    with SessionLocal() as db:
        report = db.get(SafetyReport, report_id)
        routes = [route for route in report.processing_routes if route.is_active]
        assert len(routes) == 1 and routes[0].route_kind == "quality"
        assert routes[0].details["complaints"] == [label for _, label in groups]
    _, page = form_values(client, report_id)
    assert page.text.count('data-meddra-hlt=') == 8
    assert 'value="Проблемы с упаковкой продукта" data-meddra-hlt="10069291" checked' in page.text


def test_quality_old_descriptions_and_health_are_preserved_when_question_removed(client):
    from app.database import SessionLocal
    from app.models import SafetyReport

    report_id, _ = create(client)
    values, _ = form_values(client, report_id)
    values.update(routes=["quality"], quality_complaints=["Повреждение упаковки", "Другое"],
                  quality_other_text="Original additional description", quality_health="yes")
    assert client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False).status_code == 303
    values, page = form_values(client, report_id)
    assert 'Ранее указанные претензии' in page.text
    values.pop("quality_health")  # The current browser form no longer submits this field.
    values["quality_batch"] = "UPDATED-LOT"
    assert client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        report = db.get(SafetyReport, report_id)
        quality = next(route for route in report.processing_routes if route.route_kind == "quality")
        assert quality.details["health"] == "yes"
        assert quality.details["complaints"] == ["Повреждение упаковки", "Другое"]
        assert quality.details["other_text"] == "Original additional description"
        assert quality.details["batch"] == "UPDATED-LOT"
        assert {route.route_kind for route in report.processing_routes if route.is_active} == {"quality", "pv"}


def test_detailed_icsr_contains_known_source_data_and_originals_without_automatic_validity(client):
    from app.database import SessionLocal
    from app.models import SafetyReport

    values, _ = form_values(client)
    values.update(received_date="2026-11-11", raw_text="Detailed handoff source", reporter_unknown="true")
    response = client.post("/safety-reports/intake", data=values,
        files={"original_files": ("source-note.txt", b"Original handoff evidence", "text/plain")}, follow_redirects=False)
    page = client.get(response.headers["location"])
    report_id = page.context["report"].id
    values, _ = form_values(client, report_id)
    values.update(routes=["pv"], pv_ae="true", pv_special="true",
                  pv_special_types=["Применение во время беременности"], pv_patient_unknown="true",
                  pv_event_text="Known source event description", pv_seriousness="yes",
                  pv_seriousness_text="Known source seriousness description", action="handoff")
    response = client.post(f"/safety-reports/{report_id}/intake", data=values, follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    for expected in ["Known source event description", "Known source seriousness description", "source-note.txt",
                     "Сведения о пациенте пока неизвестны", "Первичный репортёр пока неизвестен", "Применение во время беременности"]:
        assert expected in page.text
    with SessionLocal() as db:
        report = db.get(SafetyReport, report_id)
        assert not report.is_valid_icsr
        assert report.case.seriousness == "serious"
        assert len(report.case.safety_report.attachments) == 1
