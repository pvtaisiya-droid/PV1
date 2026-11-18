import json
import io
import zipfile
from pathlib import Path

from tests.form_helpers import post_current
from tests.test_concurrent_forms import Forms
from tests.test_e2b_import import e2b_xml, make_zip, seed_product, upload_package


def group_form(client, path, index=0, action="confirm"):
    page = client.get(path)
    group = page.context["preview"]["case_groups"][index]
    target = f"{path}/{action}?case_group={group['token']}"
    data = Forms(page.text).forms[target]
    if action == "confirm":
        xml_ids = set(group["item_ids"])
        data["document_ids"] = [identifier for identifier in group["attachment_ids"] if identifier not in xml_ids]
    return target, data, group


def test_multi_case_import_merges_variants_and_isolates_documents_and_repeats(client, tmp_path):
    from app.database import SessionLocal
    from app.models import Attachment, Case, CaseProduct, E2BImportBatch, E2BTransmission, IncomingRequest, Patient, Reaction, SafetyReport

    seed_product()
    incomplete = e2b_xml(message_id="A-1", case_id="CASE-A").replace(b"<patientonsetage>42</patientonsetage>", b"")
    complete = e2b_xml(message_id="A-2", case_id="CASE-A", receiver="Receiver B")
    package = make_zip({"a/first.xml": incomplete, "a/second.xml": complete, "a/CIOMS.pdf": b"%PDF A",
                        "b/case.xml": e2b_xml(message_id="B-1", case_id="CASE-B"), "b/CIOMS.pdf": b"%PDF B",
                        "common.pdf": b"%PDF common"})
    uploaded = upload_package(client, package)
    path = uploaded.headers["location"].split("?", 1)[0]
    target, data, group = group_form(client, path)
    assert group["merged"]["patient"]["age_value"] == 42
    assert not group["unresolved_count"]
    first = post_current(client, target, data=data)
    assert first.status_code == 200 and "Выбранный случай импортирован" in first.text, first.context.get("error") or first.context.get("validation")
    with SessionLocal() as db:
        batch = db.query(E2BImportBatch).one()
        assert batch.status == "partially_imported"
        assert db.query(Case).count() == 1
        assert db.query(E2BTransmission).count() == 2
    target, data, _ = group_form(client, path, 1)
    assert post_current(client, target, data=data).status_code == 200
    with SessionLocal() as db:
        batch = db.query(E2BImportBatch).one()
        assert batch.status == "imported"
        results = json.loads(batch.import_summary_json)["groups"]
        assert all(result["source_provenance"] and result["importer_version"] == "e2b-importer-3" for result in results.values())
        assert db.query(Case).count() == db.query(Patient).count() == db.query(Reaction).count() == db.query(CaseProduct).count() == 2
        assert db.query(E2BTransmission).count() == 3
        for case in db.query(Case).all():
            own_prefix = "a/" if case.worldwide_case_id == "CASE-A" else "b/"
            paths = [Path(row.storage_path).resolve() for row in db.query(Attachment).filter(Attachment.case_id == case.id)]
            assert all(path.is_relative_to(tmp_path.resolve()) for path in paths)
            assert len(paths) == (3 if own_prefix == "a/" else 2)
            contents = {path.read_bytes() for path in paths}
            assert (b"%PDF A" if own_prefix == "a/" else b"%PDF B") in contents
            assert (b"%PDF B" if own_prefix == "a/" else b"%PDF A") not in contents
            assert b"%PDF common" not in contents
        for incoming in db.query(IncomingRequest):
            payload = json.loads(incoming.gpt_json_output)
            assert payload["message"]["worldwide_case_id"] in {"CASE-A", "CASE-B"}
            assert "files" not in payload  # Another case's source data cannot leak into this record.
        before = {model.__name__: db.query(model).count() for model in [Case, Patient, Reaction, CaseProduct, SafetyReport, E2BTransmission]}
    repeat = upload_package(client, package)
    repeat_path = repeat.headers["location"].split("?", 1)[0]
    for index in range(2):
        target, data, _ = group_form(client, repeat_path, index)
        assert post_current(client, target, data=data).status_code == 200
    with SessionLocal() as db:
        assert {model.__name__: db.query(model).count() for model in [Case, Patient, Reaction, CaseProduct, SafetyReport, E2BTransmission]} == before
        assert all(case.case_version == 1 for case in db.query(Case))
    # Even a byte change under the same transmission identity requires review,
    # rather than silently creating another clinical version or transmission.
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        changed = make_zip({name: archive.read(name).replace(b"\n  ", b"\n    ") if name.endswith(".xml") else archive.read(name)
                            for name in archive.namelist()})
    changed_upload = upload_package(client, changed)
    changed_path = changed_upload.headers["location"].split("?", 1)[0]
    target, data, _ = group_form(client, changed_path)
    rejected = post_current(client, target, data=data)
    assert "Содержимое ранее импортированного сообщения изменилось" in rejected.text
    with SessionLocal() as db:
        assert {model.__name__: db.query(model).count() for model in [Case, Patient, Reaction, CaseProduct, SafetyReport, E2BTransmission]} == before


def test_conflict_blocks_only_affected_case_and_resolution_is_versioned(client):
    from app.database import SessionLocal
    from app.models import AuditTrail, Case

    seed_product()
    package = make_zip({"a/first.xml": e2b_xml(message_id="A-1", case_id="CASE-A"),
        "a/second.xml": e2b_xml(message_id="A-2", case_id="CASE-A").replace(b"<patientsex>2", b"<patientsex>1"),
        "b/case.xml": e2b_xml(message_id="B-1", case_id="CASE-B")})
    uploaded = upload_package(client, package)
    path = uploaded.headers["location"].split("?", 1)[0]
    stale_url, stale_data, a = group_form(client, path)
    assert a["unresolved_count"] == 1 and not a["can_import"]
    target, data, b = group_form(client, path, 1)
    assert b["can_import"]
    assert post_current(client, target, data=data).status_code == 200
    assert client.post(stale_url, data=stale_data).status_code == 409
    target, data, a = group_form(client, path)
    blocked = post_current(client, target, data=data)
    assert "Перед импортом этого случая требуется проверка" in blocked.text
    with SessionLocal() as db:
        assert db.query(Case).count() == 1
    target, data, a = group_form(client, path, action="resolve")
    conflict = a["conflicts"][0]
    chosen = next(choice for choice in conflict["choices"] if choice["value"] == "male")
    data.update(choice_paths=[conflict["path"]], choice_sources=[chosen["file_id"]], reason="Synthetic source verification")
    no_csrf = dict(data)
    no_csrf.pop("csrf")
    assert client.post(target, data=no_csrf).status_code == 403
    forged = dict(data, choice_sources=["unrelated-source"])
    rejected = client.post(target, data=forged)
    assert "Выберите допустимые значения источников" in rejected.text
    assert client.post(target, data=data).status_code == 200
    target, data, a = group_form(client, path)
    assert a["can_import"] and a["merged"]["patient"]["sex"] == "male"
    assert post_current(client, target, data=data).status_code == 200
    with SessionLocal() as db:
        assert db.query(Case).count() == 2
        assert db.query(AuditTrail).filter(AuditTrail.action == "resolve_case_sources").count() == 1


def test_feature_gate_and_company_scope_are_enforced_per_case(client, monkeypatch):
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import Case
    seed_product()
    uploaded = upload_package(client, make_zip({
        "a/case.xml": e2b_xml(case_id="CASE-A"),
        "b/case.xml": e2b_xml(case_id="CASE-B", message_id="B").replace(b"Test Product", b"Unknown Product")}))
    path = uploaded.headers["location"].split("?", 1)[0]
    monkeypatch.setattr(get_settings(), "e2b_group_import_enabled", False)
    target, data, a = group_form(client, path)
    assert a["availability_blocked"] and not a["can_import"]
    blocked = post_current(client, target, data=data)
    assert "Импорт нескольких случаев в этой установке пока недоступен" in blocked.text
    monkeypatch.setattr(get_settings(), "e2b_group_import_enabled", True)
    target, data, a = group_form(client, path)
    assert a["can_import"]
    assert post_current(client, target, data=data).status_code == 200
    target, data, b = group_form(client, path, 1)
    assert not b["has_company_product"] and not b["can_import"]
    assert "Перед импортом этого случая требуется проверка" in post_current(client, target, data=data).text
    with SessionLocal() as db:
        assert db.query(Case).count() == 1


def test_failure_in_second_case_preserves_first_case_and_original_files(client, monkeypatch):
    from app import e2b_import
    from tests.test_concurrent_e2b import snapshot
    seed_product()
    uploaded = upload_package(client, make_zip({"a/case.xml": e2b_xml(case_id="CASE-A"),
        "b/case.xml": e2b_xml(case_id="CASE-B", message_id="B"), "b/CIOMS.pdf": b"%PDF synthetic"}))
    path = uploaded.headers["location"].split("?", 1)[0]
    target, data, _ = group_form(client, path)
    assert post_current(client, target, data=data).status_code == 200
    target, data, _ = group_form(client, path, 1)
    before = snapshot()
    directories = {str(path) for path in e2b_import.UPLOAD_ROOT.rglob("*") if path.is_dir()}
    original = e2b_import.log_audit
    def fail(db, **kwargs):
        if kwargs.get("action") == "case_import_confirmed":
            db.flush()
            raise RuntimeError("Synthetic group audit failure")
        return original(db, **kwargs)
    monkeypatch.setattr(e2b_import, "log_audit", fail)
    result = post_current(client, target, data=data, follow_redirects=False)
    assert result.status_code == 303 and "error=" in result.headers["location"]
    assert snapshot() == before
    assert {str(path) for path in e2b_import.UPLOAD_ROOT.rglob("*") if path.is_dir()} == directories


def test_identical_local_numbers_from_different_senders_create_separate_cases(client):
    from app.database import SessionLocal
    from app.models import Case, CaseIdentifier, E2BTransmission
    seed_product()
    first = e2b_xml(case_id="REMOVE", extra="<companynumb>LOCAL-1</companynumb>").replace(b"<safetyreportid>REMOVE</safetyreportid>", b"")
    second = first.replace(b"Partner One", b"Other Partner").replace(b"MSG-E2B-001", b"MSG-OTHER")
    uploaded = upload_package(client, make_zip({"same-folder/first.xml": first, "same-folder/second.xml": second}))
    path = uploaded.headers["location"].split("?", 1)[0]
    for index in range(2):
        target, data, _ = group_form(client, path, index)
        result = post_current(client, target, data=data)
        assert result.status_code == 200, result.text
        assert "Выбранный случай импортирован" in result.text
    with SessionLocal() as db:
        assert db.query(Case).count() == 2
        assert db.query(CaseIdentifier).filter(CaseIdentifier.identifier_value == "LOCAL-1").count() == 2
        assert len({row.case_id for row in db.query(E2BTransmission)}) == 2


def test_legacy_package_requires_recheck_before_import(client):
    from app.database import SessionLocal
    from app.models import Case, E2BImportBatch
    from tests.test_e2b_import import preview_data
    seed_product()
    uploaded = upload_package(client, make_zip({"case.xml": e2b_xml()}))
    path = uploaded.headers["location"].split("?", 1)[0]
    with SessionLocal() as db:
        db.query(E2BImportBatch).one().importer_version = "e2b-importer-2"
        db.commit()
    target, data, group = group_form(client, path)
    assert group["recheck_required"] and not group["can_import"]
    assert "Источник разобран прежней версией импортёра" in post_current(client, target, data=data).text
    with SessionLocal() as db:
        assert db.query(Case).count() == 0
    checked = post_current(client, f"{path}/recheck", data=preview_data(client, path, "recheck"))
    assert checked.status_code == 200
    target, data, group = group_form(client, path)
    assert not group["recheck_required"] and group["can_import"]
    assert len(group["merged"]["products"]) == 1
    assert post_current(client, target, data=data).status_code == 200
