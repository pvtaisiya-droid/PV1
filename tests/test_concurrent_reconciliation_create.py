from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.orm.exc import StaleDataError

from tests.test_concurrent_forms import Forms
from tests.test_concurrent_reconciliation import snapshot
from tests.test_reconciliation_message_direction import create_partner, create_report_and_case


def preview_form(client):
    from app.database import SessionLocal
    with SessionLocal() as db:
        partner = create_partner(db)
        report, _ = create_report_and_case(db, partner, number="CREATE-CONCURRENT",
            source_type="partner", message_direction="partner_to_our_company")
        partner_id, report_id = partner.id, report.id
    page = (f"/partner-reconciliation?partner_id={partner_id}"
            "&period_start=2026-04-01&period_end=2026-04-30")
    response = client.get(page)
    assert response.status_code == 200, response.text
    forms = Forms(response.text).forms
    form = forms["/partner-reconciliation/save"]
    assert forms["/partner-reconciliation"]["expected_preview_version"] == form["expected_preview_version"]
    form.update(language="ru", document_format="xlsx")
    return partner_id, report_id, page, form


@pytest.mark.parametrize("value", [None, "bad", "0" * 64])
def test_create_requires_current_preview(client, value):
    _, _, _, form = preview_form(client)
    if value is None:
        form.pop("expected_preview_version")
    else:
        form["expected_preview_version"] = value
    before = snapshot()
    response = client.post("/partner-reconciliation/save", data=form, follow_redirects=False)
    assert response.status_code == (409 if value == "0" * 64 else 422)
    assert snapshot() == before


@pytest.mark.parametrize("change", ["partner", "report", "new_contact"])
def test_create_stale_input_and_reload(client, change):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import Partner, SafetyReport
    partner_id, report_id, page, form = preview_form(client)
    with SessionLocal() as db:
        if change == "partner":
            db.get(Partner, partner_id).partner_name = "Winner"
        elif change == "report":
            db.get(SafetyReport, report_id).raw_text = "Winner"
        else:
            crud.create_contract_contact(db, schemas.ContractContactCreate(
                partner_id=partner_id, first_name="New", last_name="Contact",
                position="PV", email="new@example.test"))
        db.commit()
    before = snapshot()
    assert client.post("/partner-reconciliation/save", data=form, follow_redirects=False).status_code == 409
    assert snapshot() == before
    form.update(Forms(client.get(page).text).forms["/partner-reconciliation/save"])
    assert client.post("/partner-reconciliation/save", data=form, follow_redirects=False).status_code == 303
    after = snapshot()
    assert client.post("/partner-reconciliation/save", data=form, follow_redirects=False).status_code == 409
    assert snapshot() == after


@pytest.mark.parametrize("failure", ["format", "partial_file", "second_file", "audit", "commit"])
def test_create_rolls_back_rows_audit_and_files(client, monkeypatch, failure):
    from app import crud
    from app.database import SessionLocal
    from app.routers import partner_reconciliation as routes
    from app.reconciliation_documents import RECONCILIATION_DOCUMENT_DIR
    _, _, _, form = preview_form(client)
    root = Path(RECONCILIATION_DOCUMENT_DIR)
    root.mkdir(parents=True, exist_ok=True)
    previous = root / "previous.xlsx"
    previous.write_bytes(b"keep")
    before = snapshot()
    if failure == "format":
        form["document_format"] = "invalid"
    elif failure == "partial_file":
        def fail_file(row, *, created_paths, **kwargs):
            path = root / f"{row.id}.xlsx"
            created_paths.append(path)
            path.write_bytes(b"partial")
            raise ValueError("File failed")
        monkeypatch.setattr(routes, "generate_reconciliation_document_file", fail_file)
    elif failure == "second_file":
        form["document_format"] = "pdf"
        original_write = Path.write_bytes
        def fail_second_file(path, content):
            if path.parent == root and path.suffix == ".pdf":
                original_write(path, b"partial")
                raise ValueError("Second file failed")
            return original_write(path, content)
        monkeypatch.setattr(Path, "write_bytes", fail_second_file)
    elif failure == "audit":
        original = crud.log_field_changes
        def fail_audit(*args, **kwargs):
            original(*args, **kwargs)
            raise StaleDataError("Audit failed")
        monkeypatch.setattr(crud, "log_field_changes", fail_audit)
    else:
        def fail_commit(self):
            raise StaleDataError("Commit failed")
        monkeypatch.setattr(SessionLocal.class_, "commit", fail_commit)
    response = client.post("/partner-reconciliation/save", data=form, follow_redirects=False)
    assert response.status_code == (303 if failure in {"format", "partial_file", "second_file"} else 409)
    assert snapshot() == before
    assert list(root.iterdir()) == [previous]
    assert previous.read_bytes() == b"keep"


def test_two_parallel_creates_commit_only_one(client):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, PartnerReconciliationItem, AuditTrail
    partner_id, _, _, form = preview_form(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post("/partner-reconciliation/save",
                           data=form, follow_redirects=False).status_code, range(2)))
    assert sorted(results) == [303, 409]
    with SessionLocal() as db:
        row = db.query(PartnerReconciliation).filter_by(partner_id=partner_id).one()
        assert row.reconciliation_status == "generated"
        assert row.partner_case_count == 1
        assert db.query(PartnerReconciliationItem).filter_by(reconciliation_id=row.id).count() == 1
        assert db.query(AuditTrail).filter_by(entity_id=row.id, action="create").count() == 1
        assert Path(row.document_path).is_file()


def test_product_registration_change_invalidates_preview(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import ProductRegistrationCertificate
    partner_id, _, page, form = preview_form(client)
    with SessionLocal() as db:
        product = crud.create_product(db, schemas.ProductCreate(
            product_code="PREVIEW-PRODUCT", product_name="Preview product", mah_partner_id=partner_id))
        registration = crud.create_product_registration_certificate(db,
            schemas.ProductRegistrationCertificateCreate(product_id=product.id,
                registration_number="PREVIEW-RU", registration_date="2026-01-01",
                valid_until="2099-12-31", status="active"))
        registration_id = registration.id
    # A new eligible product also changes the preview.
    before = snapshot()
    assert client.post("/partner-reconciliation/save", data=form, follow_redirects=False).status_code == 409
    assert snapshot() == before
    form.update(Forms(client.get(page).text).forms["/partner-reconciliation/save"])
    with SessionLocal() as db:
        db.get(ProductRegistrationCertificate, registration_id).registration_number = "Winner RU"
        db.commit()
    before = snapshot()
    assert client.post("/partner-reconciliation/save", data=form, follow_redirects=False).status_code == 409
    assert snapshot() == before
