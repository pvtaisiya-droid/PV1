"""Check real E2B reprocessing and business actions previously triggered by links."""
import hashlib
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from tests.test_access_control_matrix import set_permissions
from tests.test_concurrent_forms import Forms
from tests.test_e2b_import import e2b_xml, latest_batch, make_zip, seed_product, upload_package


@pytest.fixture(autouse=True)
def isolated_storage(client, tmp_path):
    from app.config import get_settings
    from app.database import engine
    from app.e2b_import import UPLOAD_ROOT

    assert Path(engine.url.database).resolve().is_relative_to(tmp_path.resolve())
    assert get_settings().storage_root == (tmp_path / "uploads").resolve()
    assert UPLOAD_ROOT.resolve().is_relative_to(get_settings().storage_root)


def state_snapshot():
    from app.config import get_settings
    from app.database import Base, SessionLocal

    with SessionLocal() as db:
        rows = {
            name: db.execute(select(table).order_by(*table.primary_key)).all()
            for name, table in sorted(Base.metadata.tables.items())
        }
    root = get_settings().storage_root
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    return rows, files


def prepare(client):
    seed_product()
    content = {"case.xml": e2b_xml(), "CIOMS.pdf": b"%PDF-1.4 synthetic", "note.txt": b"Synthetic note"}
    assert upload_package(client, make_zip(content)).status_code == 303
    return latest_batch().id, content


def recheck_form(client, batch_id):
    page = f"/safety-reports/e2b-imports/{batch_id}"
    response = client.get(page)
    assert response.status_code == 200
    url = page + "/recheck"
    return url, Forms(response.text).forms[url]


@pytest.mark.parametrize("permission", ("create", "icsr_workflow"))
def test_real_e2b_recheck_replaces_children_and_diagnostics_with_audit(client, permission):
    from app.database import SessionLocal
    from app.e2b_import import IMPORTER_VERSION
    from app.models import AuditTrail, E2BImportBatch, E2BImportIssue, User

    batch_id, content = prepare(client)
    with SessionLocal() as db:
        batch = db.get(E2BImportBatch, batch_id)
        batch.importer_version = "synthetic-old-importer"
        batch.issues.append(E2BImportIssue(issue_type="obsolete_check", message="Synthetic obsolete warning"))
        db.commit()
        old_item_ids = {item.id for item in batch.items}
        old_issue_ids = {issue.id for issue in batch.issues}
        old_paths = {item.stored_path for item in batch.items}
        source = Path(batch.source_zip_path)
        source_bytes = source.read_bytes()
        old_audits = db.query(AuditTrail).filter_by(entity_id=batch_id).count()
        actor_id = db.query(User).filter_by(login="test-admin").one().id

    set_permissions(["view", permission])
    url, form = recheck_form(client, batch_id)
    assert form["csrf"] and int(form["expected_version"]) > 0
    old_files = state_snapshot()[1]
    response = client.post(url, data=form, follow_redirects=False)
    assert response.status_code == 303
    assert "message" in parse_qs(urlsplit(response.headers["location"]).query)

    with SessionLocal() as db:
        batch = db.get(E2BImportBatch, batch_id)
        assert batch.version > int(form["expected_version"])
        assert batch.status == "requires_review" and batch.importer_version == IMPORTER_VERSION
        assert (batch.detected_xml_count, batch.detected_pdf_count, batch.detected_other_count) == (1, 1, 1)
        assert (batch.detected_case_count, batch.detected_message_count) == (1, 1)
        assert len(batch.items) == 3 and old_item_ids.isdisjoint(item.id for item in batch.items)
        assert old_issue_ids.isdisjoint(issue.id for issue in batch.issues)
        assert all(issue.issue_type != "obsolete_check" for issue in batch.issues)
        assert batch.error_count == sum(issue.severity == "error" for issue in batch.issues) == 0
        assert batch.warning_count == sum(issue.severity == "warning" for issue in batch.issues)
        assert batch.normalized_json and "Test Product" in batch.normalized_json
        assert batch.file_manifest_json and "CIOMS.pdf" in batch.file_manifest_json
        assert batch.zip_checksum_sha256 == hashlib.sha256(source_bytes).hexdigest()
        assert source.read_bytes() == source_bytes
        assert old_paths.isdisjoint(item.stored_path for item in batch.items)
        assert sorted(Path(item.stored_path).read_bytes() for item in batch.items) == sorted(content.values())
        audits = db.query(AuditTrail).filter_by(entity_id=batch_id, action="recheck").all()
        assert len(audits) == 1
        assert (audits[0].user_id, audits[0].new_value, audits[0].source_module) == (actor_id, batch.status, "E2B Import")
        assert db.query(AuditTrail).filter_by(entity_id=batch_id).count() == old_audits + 1
    new_files = state_snapshot()[1]
    assert all(new_files.get(path) == data for path, data in old_files.items())
    after = state_snapshot()
    assert client.get(url.rsplit("/", 1)[0] + "?recheck=1").status_code == 200
    assert state_snapshot() == after


@pytest.mark.parametrize("permissions", (["view"], ["view", "edit"]))
def test_read_permission_cannot_recheck_real_package(client, permissions):
    batch_id, _ = prepare(client)
    url, form = recheck_form(client, batch_id)
    set_permissions(permissions)
    before = state_snapshot()
    response = client.get(url.rsplit("/", 1)[0] + "?recheck=1")
    assert response.status_code == 200
    assert url not in Forms(response.text).forms
    assert client.post(url, data=form, follow_redirects=False).status_code == 403
    assert state_snapshot() == before


@pytest.mark.parametrize("csrf", (None, "invalid-token"))
def test_recheck_invalid_csrf_preserves_real_package(client, csrf):
    batch_id, _ = prepare(client)
    url, form = recheck_form(client, batch_id)
    form.pop("csrf")
    if csrf is not None:
        form["csrf"] = csrf
    before = state_snapshot()
    assert client.post(url, data=form, follow_redirects=False).status_code == 403
    assert state_snapshot() == before


@pytest.mark.parametrize("finalize", ("confirm", "mark-not-pv-ars"))
def test_finalized_e2b_batch_rejects_real_recheck_and_disables_button(client, finalize):
    from app.database import SessionLocal
    from app.models import Product

    batch_id, _ = prepare(client)
    if finalize == "mark-not-pv-ars":
        with SessionLocal() as db:
            db.query(Product).filter_by(product_code="TP-001").one().is_company_product = False
            db.commit()
        url, form = recheck_form(client, batch_id)
        assert client.post(url, data=form, follow_redirects=False).status_code == 303
    page = f"/safety-reports/e2b-imports/{batch_id}"
    form = Forms(client.get(page).text).forms[page + "/" + finalize]
    finalized = client.post(page + "/" + finalize, data=form, follow_redirects=False)
    assert finalized.status_code == 303
    assert "validation" not in parse_qs(urlsplit(finalized.headers["location"]).query)
    before = state_snapshot()
    response = client.get(page)
    button_form = re.search(r'<form[^>]+action="' + re.escape(page + "/recheck") + r'"[^>]*>(.*?)</form>', response.text, re.S)
    assert button_form and re.search(r'<button\b[^>]*\bdisabled\b', button_form[1])
    url, data = recheck_form(client, batch_id)
    response = client.post(url, data=data, follow_redirects=False)
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query)["validation"] == ["Finalized batch cannot be re-checked."]
    assert state_snapshot() == before


@pytest.mark.parametrize("permission", ("create", "manage_reference_data"))
def test_onboarding_start_requires_post_with_csrf(client, permission):
    from app.database import SessionLocal
    from app.models import PartnerOnboardingWizard, User

    set_permissions(["view", permission])
    before = state_snapshot()
    for method in ("GET", "HEAD"):
        response = client.request(method, "/partner-onboarding/new?start=1&action=create", follow_redirects=False)
        assert response.status_code == (200 if method == "GET" else 405)
    page = client.get("/partner-onboarding/new")
    dashboard = client.get("/dashboard")
    form = Forms(page.text).forms["/partner-onboarding/new"]
    assert Forms(dashboard.text).forms["/partner-onboarding/new"] == form
    assert state_snapshot() == before
    for invalid in ({}, {"csrf": "invalid-token"}):
        assert client.post("/partner-onboarding/new", data=invalid).status_code == 403
    assert state_snapshot() == before
    response = client.post("/partner-onboarding/new", data=form, follow_redirects=False)
    assert response.status_code == 303 and "/step/1" in response.headers["location"]
    with SessionLocal() as db:
        wizard = db.query(PartnerOnboardingWizard).one()
        assert wizard.current_step == 1 and wizard.status == "draft" and not wizard.partner_id
        assert wizard.user_id == db.query(User).filter_by(login="test-admin").one().id
        assert wizard.id in response.headers["location"]


def test_readonly_user_cannot_start_onboarding(client):
    form = Forms(client.get("/partner-onboarding/new").text).forms["/partner-onboarding/new"]
    set_permissions(["view"])
    before = state_snapshot()
    assert client.get("/partner-onboarding/new").status_code == 403
    assert client.post("/partner-onboarding/new", data=form).status_code == 403
    assert "/partner-onboarding/new" not in Forms(client.get("/dashboard").text).forms
    assert state_snapshot() == before


def test_action_query_parameters_do_not_change_records_or_files_on_read_pages(client):
    batch_id, _ = prepare(client)
    wizard_form = Forms(client.get("/partner-onboarding/new").text).forms["/partner-onboarding/new"]
    wizard = client.post("/partner-onboarding/new", data=wizard_form, follow_redirects=False)
    wizard_page = urlsplit(wizard.headers["location"]).path
    urls = (
        "/dashboard", "/partners", "/products", "/substances", "/dosage-forms",
        "/contracts", "/contract-contacts", "/tasks", "/literature-monitoring",
        "/accounting-data", "/incoming-requests", "/gpt-analysis", "/cases/new",
        "/safety-reports", "/submissions", "/psur", "/psmf?preview=1&tab=partner",
        "/partner-reconciliation", "/documents", "/training", "/users-roles",
        "/contract-imports", "/partner-onboarding/new", wizard_page,
        f"/safety-reports/e2b-imports/{batch_id}",
    )
    before = state_snapshot()
    for url in urls:
        separator = "&" if "?" in url else "?"
        response = client.get(url + separator + "recheck=1&action=delete&confirm=1&cancel=1&generate=1&sync=1", follow_redirects=False)
        assert response.status_code == 200, (url, response.text)
        assert state_snapshot() == before, url


@pytest.mark.parametrize("soft_deleted", (False, True))
def test_psmf_get_does_not_recreate_missing_settings(client, soft_deleted):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import PSMFSettings

    with SessionLocal() as db:
        partner = crud.create_partner(db, schemas.PartnerCreate(partner_code="READ-PSMF", partner_name="Synthetic PSMF partner"))
        partner_id = partner.id
        for settings in db.query(PSMFSettings).all():
            if soft_deleted:
                settings.is_deleted = True
            else:
                db.delete(settings)
        db.commit()
    before = state_snapshot()
    for url in ("/psmf?tab=settings", "/psmf?tab=chapters", f"/psmf?tab=partner&partner_id={partner_id}&preview=1"):
        response = client.get(url)
        assert response.status_code == 200
        assert state_snapshot() == before, url
    assert "/psmf/settings" not in Forms(client.get("/psmf?tab=settings").text).forms
    response = client.post("/psmf/settings", data={
        "expected_version": 1, "company_name": "Synthetic settings", "psmf_template_version": "Synthetic version",
        "default_empty_state_text": "No data", "enable_docx_export": "true",
    })
    assert response.status_code == 409
    assert state_snapshot() == before
