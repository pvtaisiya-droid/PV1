from html.parser import HTMLParser
from pathlib import Path

import pytest
from sqlalchemy.orm.exc import StaleDataError


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = {}
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = attrs.get("action")
            self.forms[self.current] = {}
        elif tag == "input" and self.current:
            if attrs.get("name"):
                self.forms[self.current][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


def target(kind):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import Partner, Product, Substance, SOP

    with SessionLocal() as db:
        if kind == "contract":
            partner = crud.create_partner(db, schemas.PartnerCreate(
                partner_code="CONCURRENT-CONTRACT", partner_name="Contract parent"))
            row = crud.create_contract(db, schemas.ContractCreate(
                partner_id=partner.id, contract_number="Concurrent form",
                contract_date="2026-01-01", valid_until="2099-01-01"))
            data = dict(partner_id=partner.id, contract_number=row.contract_number,
                        contract_date="2026-01-01", valid_until="2099-01-01", is_active="true")
            page, url, field = "/contracts", f"/contracts/{row.id}/edit", "contract_number"
        elif kind == "product":
            row = crud.create_product(db, schemas.ProductCreate(product_name="Concurrent form"))
            data = dict(product_name=row.product_name)
            page, url, field = "/products", f"/products/{row.id}/edit", "product_name"
        elif kind == "partner":
            row = crud.create_partner(db, schemas.PartnerCreate(partner_code="CONCURRENT", partner_name="Original"))
            data = dict(partner_code=row.partner_code, partner_name=row.partner_name)
            page, url, field = "/partners", f"/partners/{row.id}/edit", "partner_name"
        elif kind == "contact":
            partner = crud.create_partner(db, schemas.PartnerCreate(
                partner_code="CONTACT-PARENT", partner_name="Contact parent"))
            row = crud.create_contract_contact(db, schemas.ContractContactCreate(
                partner_id=partner.id, last_name="Original", first_name="Contact", position="PV"))
            data = dict(partner_id=partner.id, last_name=row.last_name,
                        first_name=row.first_name, position=row.position)
            page, url, field = "/contract-contacts", f"/contract-contacts/{row.id}/edit", "last_name"
        elif kind == "substance":
            row = crud.create_substance(db, schemas.SubstanceCreate(substance_name="Concurrent form"))
            data = dict(substance_name=row.substance_name)
            page, url, field = "/substances", f"/substances/{row.id}/edit", "substance_name"
        elif kind == "certificate":
            product = crud.create_product(db, schemas.ProductCreate(product_name="Certificate parent"))
            row = crud.create_product_registration_certificate(db, schemas.ProductRegistrationCertificateCreate(
                product_id=product.id, registration_number="CONCURRENT"))
            data = dict(registration_number=row.registration_number)
            page, url, field = "/products", f"/products/registrations/{row.id}/edit", "registration_number"
        elif kind == "sop":
            row = db.query(SOP).filter_by(is_deleted=False).first()
            data = dict(sop_code=row.sop_code, title=row.title, version=row.version, owner=row.owner,
                        effective_date=str(row.effective_date), next_review_date=str(row.next_review_date))
            page, url, field = "/sops", f"/sops/{row.id}/edit", "title"
        elif kind == "dosage_form":
            row = crud.create_dosage_form(db, schemas.DosageFormCreate(name_ru="Concurrent form"))
            data = dict(name_ru=row.name_ru)
            page, url, field = "/dosage-forms", f"/dosage-forms/{row.id}/edit", "name_ru"
        return type(row), row.id, page, url, field, data


@pytest.mark.parametrize("kind", ["contract", "product", "partner", "contact", "substance", "certificate", "sop", "dosage_form"])
def test_open_form_rejects_later_change_and_accepts_reload(client, kind):
    from app.database import SessionLocal
    from app.models import AuditTrail

    model, row_id, page, url, field, data = target(kind)
    response = client.get(page)
    assert response.status_code == 200, response.text
    original = Forms(response.text).forms[url]["expected_version"]
    with SessionLocal() as db:
        row = db.get(model, row_id)
        setattr(row, field, "Other user's change")
        db.commit()
        count = db.query(AuditTrail).count()
        version_column = model.__mapper__.version_id_col.key
        version = getattr(row, version_column)

    stale = client.post(url, data={**data, "expected_version": original}, follow_redirects=False)
    assert stale.status_code == 409, stale.text
    assert "Обновите страницу" in stale.json()["detail"]
    missing = client.post(url, data=data, follow_redirects=False)
    assert missing.status_code == 422, missing.text
    if kind != "sop":
        deleted = client.post(url.removesuffix("edit") + ("deactivate" if kind == "dosage_form" else "delete"),
                              data={"expected_version": original}, follow_redirects=False)
        assert deleted.status_code == 409, deleted.text
    with SessionLocal() as db:
        assert getattr(db.get(model, row_id), field) == "Other user's change"
        assert db.query(AuditTrail).count() == count

    refreshed = Forms(client.get(page).text).forms[url]["expected_version"]
    assert int(refreshed) == version
    saved = client.post(url, data={**data, "expected_version": refreshed}, follow_redirects=False)
    assert saved.status_code == 303, saved.text
    with SessionLocal() as db:
        assert getattr(db.get(model, row_id), field) == data[field]
        assert getattr(db.get(model, row_id), version_column) > version


@pytest.mark.parametrize("kind", ["contract", "substance", "dosage_form"])
def test_api_requires_version_and_does_not_overwrite_stale_data(client, kind):
    from app.database import SessionLocal
    from app.models import AuditTrail

    model, row_id, _, _, field, data = target(kind)
    collection = "dosage-forms" if kind == "dosage_form" else f"{kind}s"
    url = f"/api/{collection}/{row_id}"
    version_column = model.__mapper__.version_id_col.key
    with SessionLocal() as db:
        original = getattr(db.get(model, row_id), version_column)
    with SessionLocal() as db:
        row = db.get(model, row_id)
        setattr(row, field, "API winner")
        db.commit()
        current = getattr(row, version_column)
        count = db.query(AuditTrail).count()
    assert client.put(url, json=data).status_code == 422
    assert client.put(url, params={"expected_version": original}, json=data).status_code == 409
    assert client.delete(url, params={"expected_version": original}).status_code == 409
    with SessionLocal() as db:
        assert getattr(db.get(model, row_id), field) == "API winner"
        assert db.query(AuditTrail).count() == count
    result = client.put(url, params={"expected_version": current}, json=data)
    assert result.status_code == 200, result.text
    result = client.delete(url, params={"expected_version": result.json()[version_column]})
    assert result.status_code == 200, result.text


def document(client):
    from app.database import SessionLocal
    from app.models import Attachment, PSMFDocumentLink

    response = client.post("/documents", data={
        "document_title": "Concurrent upload", "attachment_type": "Other", "document_type": "Other",
        "include_in_psmf": "true", "psmf_annex_code": "A", "document_status": "active",
    }, files={"document_file": ("initial.txt", b"initial", "text/plain")}, follow_redirects=False)
    assert response.status_code == 303, response.text
    with SessionLocal() as db:
        row = db.query(Attachment).filter_by(document_title="Concurrent upload").one()
        return row.id, row.version, Path(row.storage_path)


def test_document_stale_forms_leave_files_versions_links_and_audit_intact(client):
    from app.database import SessionLocal
    from app.models import Attachment, AuditTrail, DocumentVersion, PSMFDocumentLink

    row_id, original, path = document(client)
    inclusion_url = f"/documents/{row_id}/psmf-inclusion"
    version_url = f"/documents/{row_id}/versions"
    page = client.get("/documents")
    assert int(Forms(page.text).forms[version_url]["expected_version"]) == original
    saved = client.post(inclusion_url, data={"expected_version": original}, follow_redirects=False)
    assert saved.status_code == 303, saved.text
    files = set(path.parent.iterdir())
    with SessionLocal() as db:
        count = db.query(AuditTrail).count()
        versions = db.query(DocumentVersion).count()
        links = [(r.id, r.document_version_id, r.is_included, r.version) for r in db.query(PSMFDocumentLink)]
    for url in (inclusion_url, f"/documents/{row_id}/delete"):
        assert client.post(url, data={"expected_version": original}, follow_redirects=False).status_code == 409
    stale = client.post(version_url, data={"expected_version": original},
                        files={"document_file": ("stale.txt", b"stale", "text/plain")})
    assert stale.status_code == 409, stale.text
    assert set(path.parent.iterdir()) == files
    with SessionLocal() as db:
        row = db.get(Attachment, row_id)
        assert row.include_in_psmf is False and row.is_deleted is False
        assert db.query(AuditTrail).count() == count
        assert db.query(DocumentVersion).count() == versions
        assert [(r.id, r.document_version_id, r.is_included, r.version) for r in db.query(PSMFDocumentLink)] == links
        current = row.version
    result = client.post(version_url, data={"expected_version": current, "document_version": "2.0"},
                         files={"document_file": ("fresh.txt", b"fresh", "text/plain")}, follow_redirects=False)
    assert result.status_code == 303, result.text
    assert path.read_bytes() == b"initial"
    with SessionLocal() as db:
        assert len(db.get(Attachment, row_id).versions) == 2


def test_upload_race_rolls_back_snapshot_and_cleans_new_file(client, monkeypatch):
    from app.database import SessionLocal
    from app.models import Attachment, AuditTrail, DocumentVersion
    from app.routers import placeholders

    row_id, version, path = document(client)
    files = set(path.parent.iterdir())
    with SessionLocal() as db:
        count = db.query(AuditTrail).count()
        snapshots = db.query(DocumentVersion).count()
    store = placeholders.store_document_file

    def store_then_change(upload):
        result = store(upload)
        with SessionLocal() as db:
            row = db.get(Attachment, row_id)
            row.comment = "Winner after version check"
            db.commit()
        return result

    monkeypatch.setattr(placeholders, "store_document_file", store_then_change)
    response = client.post(f"/documents/{row_id}/versions", data={"expected_version": version},
                           files={"document_file": ("loser.txt", b"loser", "text/plain")})
    assert response.status_code == 409, response.text
    assert set(path.parent.iterdir()) == files
    with SessionLocal() as db:
        assert db.get(Attachment, row_id).comment == "Winner after version check"
        assert db.query(DocumentVersion).count() == snapshots
        assert db.query(AuditTrail).count() == count


def test_product_form_rolls_back_fields_and_audit_when_child_validation_fails(client):
    from app.database import SessionLocal
    from app.models import AuditTrail

    model, row_id, _, url, field, data = target("product")
    with SessionLocal() as db:
        version = db.get(model, row_id).version
        count = db.query(AuditTrail).count()
    response = client.post(url, data={**data, "product_name": "Should rollback",
        "active_substance_id": "missing-substance", "expected_version": version}, follow_redirects=False)
    assert response.status_code == 303 and "validation=" in response.headers["location"]
    with SessionLocal() as db:
        assert db.get(model, row_id).product_name == data[field]
        assert db.get(model, row_id).version == version
        assert db.query(AuditTrail).count() == count


def test_password_reset_bulk_revocation_versions_sessions_and_rolls_back_on_conflict(client):
    from app import crud
    from app.database import SessionLocal
    from app.models import User, AuthSession, AuditTrail

    with SessionLocal() as first, SessionLocal() as second:
        user = first.query(User).filter_by(login="test-admin").one()
        stale = second.get(User, user.id)
        session = first.query(AuthSession).filter_by(user_id=user.id, is_deleted=False).one()
        session_id, original_version = session.id, session.version
        crud.set_user_password(first, user, password="Reset-first-2026!")
        count = first.query(AuditTrail).count()
        with pytest.raises(StaleDataError):
            crud.set_user_password(second, stale, password="Reset-stale-2026!")
        second.rollback()
        first.expire_all()
        session = first.get(AuthSession, session_id)
        assert session.is_deleted and not session.is_active
        assert session.version == original_version + 1
        assert first.query(AuditTrail).count() == count


def test_bulk_revocation_prevents_stale_session_reactivation(client):
    from app import crud
    from app.database import SessionLocal
    from app.models import User, AuthSession

    with SessionLocal() as first, SessionLocal() as second:
        user = first.query(User).filter_by(login="test-admin").one()
        stale = second.query(AuthSession).filter_by(user_id=user.id, is_deleted=False).one()
        crud.set_user_password(first, user, password="Revoke-all-2026!")
        stale.is_active = True
        stale.version += 1
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()
        assert second.get(AuthSession, stale.id).is_active is False


def test_contract_conflict_rolls_back_product_links_and_audit(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import Contract, ContractProduct, Product, AuditTrail

    model, row_id, _, _, _, data = target("contract")
    with SessionLocal() as db:
        a = crud.create_product(db, schemas.ProductCreate(product_name="First link"))
        b = crud.create_product(db, schemas.ProductCreate(product_name="Stale link"))
        a_id, b_id = a.id, b.id
    with SessionLocal() as first, SessionLocal() as second:
        winner, stale = first.get(Contract, row_id), second.get(Contract, row_id)
        # Load the original collection as an open edit form would do.
        assert not stale.product_links
        crud.update_contract(first, winner, schemas.ContractUpdate(**data, product_ids=[a_id]))
        count = first.query(AuditTrail).count()
        with pytest.raises(StaleDataError):
            crud.update_contract(second, stale, schemas.ContractUpdate(**data, product_ids=[b_id]))
        second.rollback()
        with SessionLocal() as verify:
            assert [r.product_id for r in verify.query(ContractProduct).filter_by(contract_id=row_id)] == [a_id]
            assert verify.query(AuditTrail).count() == count


def test_dosage_form_migration_preserves_rows_and_repeats_safely(tmp_path):
    import sqlite3
    from tests.test_migrations import migrate_test_database

    path = tmp_path / "dosage-versions.db"
    result = migrate_test_database(path, "20260930_0026")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO dosage_forms (id, name_ru, name_en, is_active) VALUES (9999, 'Test', 'Original', 1)")
        before = db.execute("SELECT id, name_ru, name_en, is_active FROM dosage_forms ORDER BY id").fetchall()
    result = migrate_test_database(path)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id, name_ru, name_en, is_active FROM dosage_forms ORDER BY id").fetchall() == before
        assert db.execute("SELECT DISTINCT row_version FROM dosage_forms").fetchall() == [(1,)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        db.execute("UPDATE dosage_forms SET row_version=7 WHERE id=9999")
    result = migrate_test_database(path)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT row_version FROM dosage_forms WHERE id=9999").fetchone() == (7,)


@pytest.mark.parametrize("changed", ["product", "contract"])
def test_product_contract_links_require_both_original_versions(client, changed):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.models import Contract, Product, ContractProduct, AuditTrail

    _, contract_id, _, _, _, _ = target("contract")
    with SessionLocal() as db:
        product = crud.create_product(db, schemas.ProductCreate(product_name="Link candidate"))
        product_id, product_version = product.id, product.version
        contract = db.get(Contract, contract_id)
        contract_version = contract.version
        if changed == "product":
            product.comment = "Other edit"
        else:
            contract.contract_number = "Other edit"
        db.commit()
        count = db.query(AuditTrail).count()
    data = dict(contract_id=contract_id, expected_version=product_version, expected_contract_version=contract_version)
    response = client.post(f"/products/{product_id}/contract-links", data=data, follow_redirects=False)
    assert response.status_code == 409, response.text
    with SessionLocal() as db:
        assert db.query(ContractProduct).filter_by(contract_id=contract_id).count() == 0
        assert db.query(AuditTrail).count() == count
        data["expected_version"] = db.get(Product, product_id).version
        data["expected_contract_version"] = db.get(Contract, contract_id).version
    response = client.post(f"/products/{product_id}/contract-links", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    unlink = f"/products/{product_id}/contract-links/{contract_id}/delete"
    assert client.post(unlink, data=data, follow_redirects=False).status_code == 409
    with SessionLocal() as db:
        assert db.query(ContractProduct).filter_by(contract_id=contract_id, is_deleted=False).count() == 1
        data["expected_version"] = db.get(Product, product_id).version
        data["expected_contract_version"] = db.get(Contract, contract_id).version
    assert client.post(unlink, data=data, follow_redirects=False).status_code == 303


def test_tariff_form_rejects_stale_contract_after_child_change(client):
    from app import crud
    from app.database import SessionLocal
    from app.models import Contract, ContractAccountingTariff, AuditTrail

    _, row_id, _, _, _, _ = target("contract")
    with SessionLocal() as db:
        version = db.get(Contract, row_id).version
        service = crud.list_accounting_services(db)[0]
        data = dict(service_id=service.id, unit="test", price="10", is_active="true", expected_version=version)
    url = f"/contracts/{row_id}/accounting-tariffs"
    result = client.post(url, data=data, follow_redirects=False)
    assert result.status_code == 303, result.text
    with SessionLocal() as db:
        tariff = db.query(ContractAccountingTariff).filter_by(contract_id=row_id).one()
        assert db.get(Contract, row_id).version > version
        count = db.query(AuditTrail).count()
        tariff_id, tariff_version = tariff.id, tariff.version
    assert client.post(url, data={**data, "price": "999"}, follow_redirects=False).status_code == 409
    with SessionLocal() as db:
        assert db.get(ContractAccountingTariff, tariff_id).price == 10
        assert db.query(AuditTrail).count() == count
    deleted = client.post(f"/contracts/accounting-tariffs/{tariff_id}/delete",
                          data={"expected_version": tariff_version}, follow_redirects=False)
    assert deleted.status_code == 303, deleted.text


def test_sop_upload_and_registry_creation_share_one_transaction(client, monkeypatch):
    from app.database import SessionLocal
    from app.models import SOP, SOPVersion, Attachment, AuditTrail
    from app.routers import sops
    from app import crud
    from app.config import get_settings

    _, row_id, _, url, _, data = target("sop")
    with SessionLocal() as db:
        version = db.get(SOP, row_id).row_version
        counts = [db.query(m).count() for m in (SOPVersion, Attachment, AuditTrail)]
    storage = Path(get_settings().storage_root)
    files = {p for p in storage.rglob("*") if p.is_file()} if storage.exists() else set()
    original = crud.create_attachment

    def fail_registry(*args, **kwargs):
        raise ValueError("Simulated registry failure after SOP flush")

    monkeypatch.setattr(crud, "create_attachment", fail_registry)
    result = client.post(url, data={**data, "title": "Should rollback", "expected_version": version},
                         files={"sop_file": ("sop.txt", b"SOP upload", "text/plain")}, follow_redirects=False)
    assert result.status_code == 303 and "error=" in result.headers["location"], result.text
    assert {p for p in storage.rglob("*") if p.is_file()} == files
    with SessionLocal() as db:
        assert db.get(SOP, row_id).title == data["title"]
        assert db.get(SOP, row_id).row_version == version
        assert [db.query(m).count() for m in (SOPVersion, Attachment, AuditTrail)] == counts
    monkeypatch.setattr(crud, "create_attachment", original)
    result = client.post(url, data={**data, "title": "Fresh upload", "expected_version": version},
                         files={"sop_file": ("sop.txt", b"SOP upload", "text/plain")}, follow_redirects=False)
    assert result.status_code == 303 and "message=" in result.headers["location"], result.text
    with SessionLocal() as db:
        row = db.get(SOP, row_id)
        attachment = db.query(Attachment).filter_by(related_object_type="SOP", related_object_id=row_id).one()
        assert Path(row.file_path).read_bytes() == b"SOP upload"
        assert attachment.storage_path == row.file_path
        assert db.query(SOPVersion).count() == counts[0] + 1


def test_dosage_form_mapper_rejects_two_session_overwrite(client):
    from app.database import SessionLocal
    from app.models import DosageForm

    with SessionLocal() as first, SessionLocal() as second:
        winner = first.query(DosageForm).first()
        stale = second.get(DosageForm, winner.id)
        winner.name_ru = "Winner"
        first.commit()
        stale.name_ru = "Stale"
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()
        assert second.get(DosageForm, winner.id).name_ru == "Winner"


def test_bulk_session_revocation_rolls_back_if_audit_insert_fails(client):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from app import crud
    from app.database import SessionLocal
    from app.models import User, AuthSession, AuditTrail

    with SessionLocal() as db:
        user = db.query(User).filter_by(login="test-admin").one()
        password_hash, version = user.password_hash, user.version
        sessions = [(r.id, r.is_active, r.is_deleted, r.version) for r in db.query(AuthSession)]
        count = db.query(AuditTrail).count()
        db.execute(text("""CREATE TRIGGER reject_test_password_audit
            BEFORE INSERT ON tblAuditTrail WHEN NEW.action = 'password_reset'
            BEGIN SELECT RAISE(ABORT, 'Test audit failure'); END"""))
        db.commit()
        with pytest.raises(IntegrityError):
            crud.set_user_password(db, user, password="Must-rollback-2026!")
        db.rollback()
        assert user.password_hash == password_hash and user.version == version
        assert [(r.id, r.is_active, r.is_deleted, r.version) for r in db.query(AuthSession)] == sessions
        assert db.query(AuditTrail).count() == count
