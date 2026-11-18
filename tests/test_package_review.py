import json
import re
from pathlib import Path

from tests.test_concurrent_forms import Forms
from tests.test_e2b_import import e2b_xml, make_zip, seed_product, upload_package


def package(client, files=None):
    seed_product()
    uploaded = upload_package(client, make_zip(files or {"a/case.xml": e2b_xml(), "a/source.pdf": b"%PDF source"}))
    path = uploaded.headers["location"].split("?", 1)[0]
    page = client.get(path)
    assert page.status_code == 200
    return path, page


def draft(client, path, index=0):
    group = client.get(path).context["preview"]["case_groups"][index]
    url = f"{path}/intake?case_group={group['token']}&embed=package&step=pv"
    page = client.get(url)
    assert page.status_code == 200
    values = dict(page.context["values"])
    tokens = page.context["e2b_draft"]
    values.update(csrf=re.search(r'name="csrf" value="([^"]+)"', page.text).group(1),
        expected_import_version=str(tokens["version"]), expected_case_token=tokens["case_token"],
        expected_inputs=tokens["inputs"], pv_card_dirty="false", action="save", current_step="pv")
    return url, page, values


def test_draft_is_full_form_opening_pv_without_creating_records(client):
    from app.database import SessionLocal
    from app.models import Case, SafetyReport
    path, review = package(client)
    assert "Обзор случая" in review.text and "Противоречия и замечания" in review.text
    assert "3. Подробнее" in review.text
    _, page, values = draft(client, path)
    assert 'data-current-step="pv"' in page.text and 'data-registered="true"' in page.text
    assert 'data-intake-step="directions"' in page.text and 'data-intake-panel="pv"' in page.text
    assert 'class="app-sidebar' not in page.text
    assert values["routes"] == ["pv"]
    state = page.context["ae_state"]
    assert state["patient"]["initials"] == "AB" and state["patient"]["age"] == "42 лет"
    assert state["medications"][0]["reported"] == "Test Product"
    assert state["contacts"]["primary"]["mode"] == "new"
    with SessionLocal() as db:
        assert db.query(Case).count() == db.query(SafetyReport).count() == 0


def test_draft_import_saves_directions_and_preserves_xml_details(client):
    from app.database import SessionLocal
    from app.models import Case, CaseProduct, Patient, Reaction, SafetyReport
    path, _ = package(client)
    url, _, values = draft(client, path)
    values.update(routes=["pv", "medical"], medical_question="A question about this source", medical_person_source="reporter")
    result = client.post(url, data=values, follow_redirects=False)
    assert result.status_code == 303, result.text[:2000]
    assert "step=pv&embed=package" in result.headers["location"]
    with SessionLocal() as db:
        assert db.query(Case).count() == db.query(Patient).count() == db.query(Reaction).count() == 1
        product = db.query(CaseProduct).one()
        assert product.dose_value == "10" and product.drug_role == "suspect"
        report = db.query(SafetyReport).one()
        routes = {route.route_kind: route for route in report.processing_routes}
        assert set(routes) == {"pv", "medical"}
        assert routes["medical"].details["question"] == "A question about this source"
        assert db.query(Patient).one().birth_date_raw == "1984"
    assert client.get(result.headers["location"]).status_code == 200
    repeated = client.post(url, data=values)
    assert repeated.status_code == 409


def test_editing_draft_card_retains_imported_product_role_and_dose(client):
    from app.database import SessionLocal
    from app.models import CaseProduct, Patient
    path, _ = package(client)
    url, page, values = draft(client, path)
    state = page.context["ae_state"]
    state["patient"]["initials"] = "Corrected patient"
    values.update(pv_card_json=json.dumps(state), pv_card_dirty="true")
    result = client.post(url, data=values, follow_redirects=False)
    assert result.status_code == 303, result.text[:2000]
    with SessionLocal() as db:
        products = [row for row in db.query(CaseProduct) if not row.is_deleted]
        assert len(products) == 1 and products[0].dose_value == "10" and products[0].drug_role == "suspect"
        assert db.query(Patient).one().full_name == "Corrected patient"


def test_invalid_draft_rolls_back_import_and_embedded_files(client, tmp_path):
    from tests.test_e2b_import import e2b_r3_xml_with_attachment
    from app.database import SessionLocal
    from app.models import Case, SafetyReport, E2BImportBatch, E2BTransmission
    xml, _ = e2b_r3_xml_with_attachment()
    xml = xml.replace(b"PACLITAXEL", b"Test Product")
    path, _ = package(client, {"a/case.xml": xml})
    url, _, values = draft(client, path)
    before = {file.resolve() for file in tmp_path.rglob("*") if file.is_file()}
    values.update(routes=["pv", "medical"], medical_due_manual="true", medical_due_date="bad-date")
    result = client.post(url, data=values)
    assert result.status_code == 422, result.text[:1500]
    assert result.context["values"]["medical_due_date"] == "bad-date"
    with SessionLocal() as db:
        assert db.query(Case).count() == db.query(SafetyReport).count() == db.query(E2BTransmission).count() == 0
        assert db.query(E2BImportBatch).one().status != "imported"
    assert {file.resolve() for file in tmp_path.rglob("*") if file.is_file()} == before


def test_selection_imports_only_chosen_groups_and_checks_freshness(client):
    from app.database import SessionLocal
    from app.models import Case, Attachment
    path, page = package(client, {"a/case.xml": e2b_xml(case_id="CASE-A", message_id="A"), "a/source.pdf": b"%PDF A",
        "b/case.xml": e2b_xml(case_id="CASE-B", message_id="B"), "b/source.pdf": b"%PDF B"})
    target = f"{path}/confirm-selection"
    values = Forms(page.text).forms[target]
    groups = page.context["preview"]["case_groups"]
    values["selected_groups"] = [groups[1]["token"]]
    assert client.post(target, data={**values, "csrf": "invalid"}).status_code == 403
    result = client.post(target, data=values, follow_redirects=False)
    assert result.status_code == 303
    with SessionLocal() as db:
        assert db.query(Case).one().worldwide_case_id == "CASE-B"
        contents = {Path(row.storage_path).read_bytes() for row in db.query(Attachment)}
        assert b"%PDF B" in contents and b"%PDF A" not in contents
    assert client.post(target, data=values).status_code == 409


def test_conflicted_case_cannot_save_through_intake(client):
    from app.database import SessionLocal
    from app.models import Case
    path, _ = package(client, {"a/one.xml": e2b_xml(message_id="A"),
        "a/two.xml": e2b_xml(message_id="B").replace(b"<patientsex>2", b"<patientsex>1")})
    url, page, values = draft(client, path)
    assert not page.context["e2b_draft"]["can_import"]
    assert client.post(url, data=values).status_code == 422
    with SessionLocal() as db:
        assert db.query(Case).count() == 0


def test_selection_is_atomic_when_second_case_token_is_stale(client):
    from app.database import SessionLocal
    from app.models import Case, SafetyReport, E2BTransmission
    path, page = package(client, {"a/case.xml": e2b_xml(case_id="CASE-A", message_id="A"),
        "b/case.xml": e2b_xml(case_id="CASE-B", message_id="B")})
    target = f"{path}/confirm-selection"
    values = Forms(page.text).forms[target]
    groups = page.context["preview"]["case_groups"]
    tokens = json.loads(values["case_tokens"])
    tokens[groups[1]["token"]] = "stale"
    values.update(selected_groups=[group["token"] for group in groups], case_tokens=json.dumps(tokens))
    assert client.post(target, data=values).status_code == 409
    with SessionLocal() as db:
        assert db.query(Case).count() == db.query(SafetyReport).count() == db.query(E2BTransmission).count() == 0
    values["case_tokens"] = json.dumps(page.context["group_tokens"])
    assert client.post(target, data=values, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.query(Case).count() == db.query(SafetyReport).count() == db.query(E2BTransmission).count() == 2


def test_followup_form_links_each_case_in_multi_case_package(client):
    from app.database import SessionLocal
    from app.models import Case, Patient, SafetyReport
    path, page = package(client, {"a/case.xml": e2b_xml(case_id="CASE-A", message_id="A"),
        "b/case.xml": e2b_xml(case_id="CASE-B", message_id="B")})
    target = f"{path}/confirm-selection"
    values = Forms(page.text).forms[target]
    values["selected_groups"] = [row["token"] for row in page.context["preview"]["case_groups"]]
    assert client.post(target, data=values, follow_redirects=False).status_code == 303
    followup = upload_package(client, make_zip({"a/case.xml": e2b_xml(case_id="CASE-A", message_id="A-2", version="2"),
        "b/case.xml": e2b_xml(case_id="CASE-B", message_id="B-2", version="2")}))
    path = followup.headers["location"].split("?", 1)[0]
    for index in range(2):
        url, page, data = draft(client, path, index)
        data["raw_subject"] = f"Follow-up form {index}"
        saved = client.post(url, data=data, follow_redirects=False)
        assert saved.status_code == 303, saved.text[:1000]
        opened = client.get(saved.headers["location"])
        assert opened.context["ae_linked_case"].worldwide_case_id == ("CASE-A" if index == 0 else "CASE-B")
        assert opened.context["ae_state"]["patient"]["initials"] == "AB"
    with SessionLocal() as db:
        assert db.query(Case).count() == 2 and db.query(SafetyReport).count() == 4
        assert db.query(Patient).filter_by(is_deleted=False).count() == 2


def test_unresolved_death_date_conflict_can_be_reviewed(client):
    path, page = package(client, {
        "a/one.xml": e2b_xml(message_id="A").replace(b"</patient>", b"<patientdeathdate>20260101</patientdeathdate></patient>"),
        "a/two.xml": e2b_xml(message_id="B").replace(b"</patient>", b"<patientdeathdate>20260102</patientdeathdate></patient>")})
    group = page.context["preview"]["case_groups"][0]
    assert any(conflict["path"] == "patient.death_date" for conflict in group["conflicts"])
    _, form, _ = draft(client, path)
    assert not form.context["e2b_draft"]["can_import"]


def test_document_list_explains_all_cases_without_selecting_other_case_files(client):
    from tests.test_e2b_i18n import VisibleText
    path, review = package(client, {
        "a/case.xml": e2b_xml(case_id="CASE-A", message_id="A"), "a/source.pdf": b"%PDF A",
        "b/case.xml": e2b_xml(case_id="CASE-B", message_id="B"), "b/source.pdf": b"%PDF B",
        "common.pdf": b"%PDF unclear allocation"})
    groups = review.context["preview"]["case_groups"]
    current = groups[0]
    documents = {row["path"]: row for row in current["review_documents"]}
    assert documents["a/source.pdf"]["assignment"] == "current"
    assert documents["a/source.pdf"]["selected"]
    assert documents["b/source.pdf"]["assignment"] == "other"
    assert documents["b/source.pdf"]["case_labels"] == ["CASE-B"]
    assert not documents["b/source.pdf"]["selected"]
    assert documents["common.pdf"]["assignment"] == "unassigned"
    assert not documents["common.pdf"]["selected"]
    _, intake, _ = draft(client, path)
    assert intake.context["e2b_draft"]["documents"] == current["review_documents"]
    assert intake.context["e2b_draft"]["case_count"] == 2
    visible = VisibleText(review.text).text
    assert "В архиве несколько случаев" in visible
    assert "все PDF и другие вложения архива" in visible
    assert "Отнесён к другому случаю" in visible and "CASE-B" in visible
    assert "Не распределён — проверьте вручную" in visible
    for path in documents:
        assert path in visible
        assert path not in intake.text
    assert "Все документы архива" not in intake.text
    assert "В архиве несколько случаев" not in intake.text
    assert "Отмеченные файлы будут приложены" not in intake.text
    assert "Неотмеченные документы остаются" not in intake.text
    assert 'data-package-documents' not in intake.text
    assert '<div hidden data-package-document-selection>' in intake.text
    fields = re.findall(r'<input type="checkbox" name="document_ids" value="([^"]+)"([^>]*)>', intake.text)
    assert {identifier for identifier, attributes in fields if 'checked' in attributes} == {
        documents["a/source.pdf"]["id"]}
