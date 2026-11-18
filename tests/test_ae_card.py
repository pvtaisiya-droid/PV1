import json
import re


def form(client, report_id=None):
    page = client.get(f"/safety-reports/{report_id}/intake?step=pv" if report_id else "/safety-reports/new")
    assert page.status_code == 200
    values = dict(page.context["values"])
    values["csrf"] = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    if report_id:
        values["expected_version"] = page.context["report"].version
        if page.context["report"].case:
            values["expected_case_version"] = page.context["report"].case.version
    return values, page


def create(client):
    values, _ = form(client)
    values.update(received_date="2026-11-12", raw_text="Synthetic AE card source")
    response = client.post("/safety-reports/intake", data=values, follow_redirects=False)
    assert response.status_code == 303, response.text[:800]
    return client.get(response.headers["location"]).context["report"].id


def save(client, identifier, state, **changes):
    values, _ = form(client, identifier)
    values.update(routes=["pv"], pv_card_json=json.dumps(state), pv_card_dirty="true", current_step="pv")
    values.update(changes)
    return client.post(f"/safety-reports/{identifier}/intake", data=values, follow_redirects=False)


def test_shared_registration_hands_off_one_case_with_multiple_routes(client):
    from app.ae_card import empty_state
    from app.database import SessionLocal
    from app.models import Attachment, Case, SafetyReport

    entry = client.get("/cases/new")
    assert entry.url.path == "/safety-reports/new"
    values, _ = form(client)
    values.update(received_date="2026-11-16", raw_text="Synthetic quality complaint with an adverse event",
                  action="next", current_step="registration", next_step="directions")
    registered = client.post("/safety-reports/intake", data=values,
        files={"original_files": ("source.txt", b"synthetic original evidence", "text/plain")}, follow_redirects=False)
    assert registered.status_code == 303, registered.text[:1000]
    directions = client.get(registered.headers["location"])
    identifier = directions.context["report"].id
    with SessionLocal() as db:
        assert db.query(SafetyReport).count() == 1
        assert db.query(Case).count() == 0
        attachment_id = db.query(Attachment).one().id

    values, _ = form(client, identifier)
    values.update(routes=["quality", "pv"], action="next", current_step="directions", next_step="quality",
                  quality_health="yes", quality_materials="yes", quality_attachment_ids=[attachment_id])
    selected = client.post(f"/safety-reports/{identifier}/intake", data=values, follow_redirects=False)
    assert selected.status_code == 303, selected.text[:1000]

    state = empty_state()
    state["ae"] = True
    state["patient"]["initials"] = "Synthetic patient"
    state["description"] = "Synthetic adverse event"
    state["medications"] = [dict(id="shared-med", companyId="", reported="Synthetic medicine", caseProductId="")]
    handoff = save(client, identifier, state, action="handoff", routes=["quality", "pv"])
    assert handoff.status_code == 303, handoff.text[:1000]
    assert handoff.headers["location"].startswith("/cases/")
    detail = client.get(handoff.headers["location"])
    assert detail.status_code == 200
    assert detail.context["case"].safety_report_id == identifier

    # A later handoff opens the same case and retains the other direction and original.
    _, updated = form(client, identifier)
    again = save(client, identifier, updated.context["ae_state"], action="handoff", routes=["quality", "pv"])
    assert again.status_code == 303
    assert again.headers["location"].split("?")[0] == handoff.headers["location"].split("?")[0]
    with SessionLocal() as db:
        assert db.query(SafetyReport).count() == db.query(Case).count() == db.query(Attachment).count() == 1
        report = db.get(SafetyReport, identifier)
        assert report.intake_enabled and report.case_id
        assert report.received_date.isoformat() == "2026-11-16"
        active = {row.route_kind: row for row in report.processing_routes if row.is_active and not row.is_deleted}
        assert set(active) == {"quality", "pv"}
        assert [link.attachment_id for link in active["quality"].document_links] == [attachment_id]
        assert report.case.patients[0].full_name == "Synthetic patient"
        assert report.case.case_products[0].reported_product_name == "Synthetic medicine"


def test_new_card_defaults_and_legacy_patient_text_not_displayed(client):
    from app.database import SessionLocal
    from app.models import SafetyReport, SafetyReportRoute
    identifier = create(client)
    with SessionLocal() as db:
        report = db.get(SafetyReport, identifier)
        db.add(SafetyReportRoute(safety_report_id=identifier, route_kind="pv", route_number="SYNTHETIC/PV",
            details={"patient_text": "LEGACY PATIENT PARAGRAPH", "seriousness": "unknown"}))
        db.commit()
    _, page = form(client, identifier)
    state = page.context["ae_state"]
    assert not state["ae"] and not state["special"]
    assert state["serious"] == "no" and state["patient"]["other"] == ""
    assert "LEGACY PATIENT PARAGRAPH" not in page.text
    assert "пример записи" not in page.text and "Серьезное?" in page.text


def test_patient_sender_unknown_and_shared_local_contacts_persist(client):
    from app.ae_card import empty_state
    from app.database import SessionLocal
    from app.models import ContractContact, SafetyReport
    identifier = create(client)
    state = empty_state()
    state["contacts"]["sender"]["mode"] = "patient"
    state["unknown"]["patient"] = True
    assert save(client, identifier, state).status_code == 303
    _, page = form(client, identifier)
    assert page.context["ae_state"]["contacts"]["primary"]["mode"] == "patient"
    state = page.context["ae_state"]
    state["patient"].update(initials="Synthetic Patient", phone="5550100")
    state["people"] = [dict(id="local-one", name="Local person", country="Россия", phone="5550200", email="", saved=False)]
    state["contacts"]["additional"] = [dict(mode="local", personId="local-one", role="Родственник"),
                                          dict(mode="local", personId="local-one", role="Для уточнений")]
    assert save(client, identifier, state).status_code == 303
    with SessionLocal() as db:
        report = db.get(SafetyReport, identifier)
        assert db.query(ContractContact).count() == 0
        assert report.sender_name == report.reporter_name == "Synthetic Patient"
        card = report.processing_routes[0].details["card"]
        assert card["unknown"]["patient"]
        assert len(card["people"]) == 1
        assert card["contacts"]["additional"][0]["personId"] == card["contacts"]["additional"][1]["personId"]


def test_saving_shared_new_contact_creates_one_directory_entry(client):
    from app.ae_card import empty_state
    from app.database import SessionLocal
    from app.models import ContractContact, SafetyReport
    identifier = create(client)
    state = empty_state()
    state["people"] = [dict(id="new-person", name="Saved source", country="Россия", phone="", email="saved-ae@example.test", saved=True)]
    state["contacts"]["sender"] = dict(mode="new", personId="new-person", role="")
    state["contacts"]["primary"] = dict(mode="local", personId="new-person", role="")
    assert save(client, identifier, state).status_code == 303
    with SessionLocal() as db:
        contact = db.query(ContractContact).one()
        report = db.get(SafetyReport, identifier)
        assert contact.country == "Россия"
        assert report.sender_contact_id == report.reporter_contact_id == contact.id
        assert len(report.processing_routes[0].details["card"]["people"]) == 1


def test_multiple_drugs_free_text_and_serious_without_event_handoff(client):
    from app import crud, schemas
    from app.ae_card import empty_state
    from app.database import SessionLocal
    from app.models import Case, Reaction
    with SessionLocal() as db:
        product = crud.create_product(db, schemas.ProductCreate(product_name="Synthetic company medicine"))
        product_id = product.id
    identifier = create(client)
    state = empty_state()
    state["patient"].update(initials="Synthetic full patient name", age="6 месяцев", sex="female", phone="5550300", email="patient@example.test")
    state["serious"] = "yes"
    state["medications"] = [dict(id="med-one", companyId=product_id, reported="Original wording", caseProductId=""),
                             dict(id="med-two", companyId="", reported="Unmatched medicine", caseProductId="")]
    response = save(client, identifier, state, action="next")
    assert response.status_code == 303, response.text[:1200]
    assert response.headers["location"].startswith("/cases/")
    with SessionLocal() as db:
        case = db.query(Case).one()
        assert case.seriousness == "serious" and case.seriousness_criteria == []
        assert not case.narrative and db.query(Reaction).count() == 0
        assert case.patients[0].full_name == "Synthetic full patient name"
        assert case.patients[0].age_text == "6 месяцев"
        assert case.patients[0].age_value == 6
        assert case.patients[0].age_unit == "months"
        medicines = [row for row in case.case_products if not row.is_deleted]
        assert len(medicines) == 2
        assert {(row.product_id, row.reported_product_name) for row in medicines} == {
            (product_id, "Original wording"), (None, "Unmatched medicine")}
    page = client.get(response.headers["location"])
    assert page.status_code == 200 and 'id="case-ae-edit"' in page.text
    assert "Synthetic full patient name" in page.text
    _, compact = form(client, identifier)
    assert [med["reported"] for med in compact.context["ae_state"]["medications"]] == ["Original wording", "Unmatched medicine"]


def test_case_changes_sync_back_and_stale_message_is_rejected(client):
    from app.ae_card import empty_state
    from app.database import SessionLocal
    from app.models import Case
    identifier = create(client)
    state = empty_state()
    state["patient"]["initials"] = "Original patient"
    state["description"] = "Original event"
    state["medications"] = [dict(id="m", companyId="", reported="Original drug", caseProductId="")]
    assert save(client, identifier, state, action="handoff").status_code == 303
    stale, stale_page = form(client, identifier)
    with SessionLocal() as db:
        case = db.query(Case).one()
        case_id = case.id
        case.patients[0].full_name = "Updated in detailed case"
        case.case_products[0].reported_product_name = "Updated drug"
        case.narrative = "Updated event"
        case.seriousness = "serious"
        db.commit()
    _, page = form(client, identifier)
    state = page.context["ae_state"]
    assert state["patient"]["initials"] == "Updated in detailed case"
    assert state["description"] == "Updated event" and state["serious"] == "yes"
    assert state["medications"][0]["reported"] == "Updated drug"
    stale.update(routes=["pv"], pv_card_json=json.dumps(stale_page.context["ae_state"]), pv_card_dirty="true")
    assert client.post(f"/safety-reports/{identifier}/intake", data=stale).status_code == 409
    detail = client.get(f"/cases/{case_id}")
    state["description"] = "Saved from full case card"
    response = client.post(f"/cases/{case_id}/ae-card", data={
        "csrf": re.search(r'name="csrf" value="([^"]+)"', detail.text).group(1),
        "expected_case_version": detail.context["case"].version, "expected_version": detail.context["report"].version,
        "pv_card_json": json.dumps(state)}, follow_redirects=False)
    assert response.status_code == 303, response.text[:1000]
    _, page = form(client, identifier)
    assert page.context["ae_state"]["description"] == "Saved from full case card"


def test_invalid_reference_rolls_back_and_preserves_posted_card(client):
    from app.ae_card import empty_state
    from app.database import SessionLocal
    from app.models import SafetyReport
    identifier = create(client)
    state = empty_state()
    state["patient"]["initials"] = "Preserve this input"
    state["medications"][0]["companyId"] = "not-a-real-product"
    response = save(client, identifier, state, raw_text="Should roll back")
    assert response.status_code == 422
    assert response.context["ae_state"]["patient"]["initials"] == "Preserve this input"
    with SessionLocal() as db:
        assert db.get(SafetyReport, identifier).raw_text == "Synthetic AE card source"


def test_existing_primary_is_not_replaced_and_criteria_set_serious(client):
    from app.ae_card import CRITERIA, empty_state
    identifier = create(client)
    state = empty_state()
    state["contacts"]["sender"]["mode"] = "patient"
    state["people"] = [dict(id="local-primary", name="Primary", country="", phone="", email="", saved=False)]
    state["contacts"]["primary"] = dict(mode="new", personId="local-primary", role="")
    state["criteria"] = [CRITERIA[0], CRITERIA[2]]
    assert save(client, identifier, state).status_code == 303
    _, page = form(client, identifier)
    assert page.context["ae_state"]["contacts"]["primary"]["personId"] == "local-primary"
    assert page.context["ae_state"]["serious"] == "yes"


def test_contact_edits_do_not_change_directory_or_earlier_messages(client):
    from app import crud, schemas
    from app.ae_card import directory_person, empty_state
    from app.database import SessionLocal
    from app.models import ContractContact, SafetyReport
    with SessionLocal() as db:
        contact = crud.create_contract_contact(db, schemas.ContractContactCreate(display_name="Directory original", email="directory@example.test"))
        person = directory_person(contact)
        contact_id = contact.id
    identifier = create(client)
    state = empty_state()
    state["people"] = [person]
    state["contacts"]["sender"] = dict(mode="existing",personId=contact_id,role="")
    state["contacts"]["primary"] = dict(mode="same",personId="",role="")
    assert save(client, identifier, state).status_code == 303
    state["people"][0]["name"] = "For this message only"
    assert save(client, identifier, state).status_code == 303
    with SessionLocal() as db:
        assert db.get(ContractContact, contact_id).display_name == "Directory original"
        report = db.get(SafetyReport, identifier)
        assert report.sender_name == report.reporter_name == "For this message only"


def test_locked_case_rejects_card_changes_without_partial_writes(client):
    from app.ae_card import empty_state
    from app.database import SessionLocal
    from app.models import Case, SafetyReport
    identifier = create(client)
    state = empty_state()
    state["description"] = "Before locking"
    assert save(client, identifier, state, action="handoff").status_code == 303
    with SessionLocal() as db:
        case = db.query(Case).one()
        case.workflow_status = "closed"
        case.is_locked = True
        db.commit()
    state["description"] = "Must not be written"
    response = save(client, identifier, state)
    assert response.status_code in {409,422}
    with SessionLocal() as db:
        assert db.query(Case).one().narrative == "Before locking"
        assert db.get(SafetyReport,identifier).processing_routes[0].details["card"]["description"] == "Before locking"


def test_linked_requester_tracks_patient_in_same_message(client):
    from app.ae_card import empty_state
    from app.database import SessionLocal
    from app.models import SafetyReportRoute
    identifier = create(client)
    state = empty_state()
    state["patient"]["initials"] = "Patient as requester"
    state["contacts"]["sender"]["mode"] = "patient"
    assert save(client, identifier, state, routes=["medical", "pv"], medical_person_source="sender").status_code == 303
    with SessionLocal() as db:
        medical = db.query(SafetyReportRoute).filter_by(safety_report_id=identifier,route_kind="medical").one()
        assert medical.details["name"] == "Patient as requester"
    state["patient"]["initials"] = "Updated requester"
    assert save(client, identifier, state, routes=["medical", "pv"], medical_person_source="sender").status_code == 303
    with SessionLocal() as db:
        medical = db.query(SafetyReportRoute).filter_by(safety_report_id=identifier,route_kind="medical").one()
        assert medical.details["name"] == "Updated requester"
