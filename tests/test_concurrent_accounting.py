import pytest
from sqlalchemy import text
from sqlalchemy.orm.exc import StaleDataError

from tests.test_concurrent_forms import Forms, target


def snapshot():
    from app.database import SessionLocal
    from app.models import Partner, PartnerAccountingInfo, ContractContact, AccountingDataPeriod, AccountingDataLine, AuditTrail

    with SessionLocal() as db:
        return {
            model.__tablename__: db.execute(text(
                f'SELECT * FROM "{model.__tablename__}" ORDER BY id'
            )).all()
            for model in (Partner, PartnerAccountingInfo, ContractContact, AccountingDataPeriod, AccountingDataLine, AuditTrail)
        }


def accounting_target(client, *, info=True):
    from app import crud, schemas
    from app.database import SessionLocal

    _, partner_id, _, _, _, _ = target("partner")
    with SessionLocal() as db:
        contact = crud.create_contract_contact(db, schemas.ContractContactCreate(
            partner_id=partner_id, last_name="Billing", first_name="Person",
            email="billing@example.test", position="Accounting",
        ))
        contact_id = contact.id
        if info:
            crud.upsert_partner_accounting_info(db, partner_id, {
                "legal_entity_name": "Original", "billing_email": "billing@example.test",
            })
    url = f"/partners/{partner_id}/accounting-info"
    page = f"/partners/{partner_id}"
    form = Forms(client.get(page).text).forms[url]
    return partner_id, contact_id, page, url, form


@pytest.mark.parametrize("changed", ["partner", "info", "contact", "contact_deleted", "contact_added", "info_created"])
def test_accounting_info_rejects_stale_form_and_accepts_reload(client, changed):
    from app import crud
    from app.database import SessionLocal
    from app.models import Partner, ContractContact

    partner_id, contact_id, page, url, form = accounting_target(client, info=changed != "info_created")
    with SessionLocal() as db:
        if changed == "partner":
            db.get(Partner, partner_id).address = "Winner"
        elif changed == "info":
            crud.get_partner_accounting_info(db, partner_id).legal_entity_name = "Winner"
        elif changed in ("contact", "contact_deleted"):
            contact = db.get(ContractContact, contact_id)
            contact.comments = "Winner"
            contact.is_deleted = changed == "contact_deleted"
        elif changed == "contact_added":
            db.add(ContractContact(partner_id=partner_id, last_name="New", first_name="Contact", position="PV"))
        else:
            crud.upsert_partner_accounting_info(db, partner_id, {"legal_entity_name": "Winner"})
        db.commit()
    before = snapshot()
    result = client.post(url, data={**form, "legal_entity_name": "Stale"}, follow_redirects=False)
    assert result.status_code == 409, result.text
    assert "Обновите страницу" in result.json()["detail"]
    assert snapshot() == before
    refreshed = Forms(client.get(page).text).forms[url]
    # Deleted contacts retain the unique partner/email key; use a new address
    # after reload rather than implicitly resurrecting the deleted contact.
    email = "replacement@example.test" if changed == "contact_deleted" else "billing@example.test"
    result = client.post(url, data={**refreshed, "legal_entity_name": "Saved",
                                   "billing_email": email}, follow_redirects=False)
    assert result.status_code == 303, result.text
    with SessionLocal() as db:
        assert crud.get_partner_accounting_info(db, partner_id).legal_entity_name == "Saved"
        contact = db.query(ContractContact).filter_by(partner_id=partner_id, is_deleted=False,
                                                     email=email).one()
        assert contact.is_billing_contact


@pytest.mark.parametrize("field,value", [
    ("expected_version", None), ("expected_version", "0"),
    ("expected_info_version", None), ("expected_info_version", "-1"),
    ("expected_contact_versions", None), ("expected_contact_versions", "invalid"),
    ("expected_contact_versions", '{"unknown": 0}'),
])
def test_accounting_info_requires_valid_tokens(client, field, value):
    _, _, _, url, form = accounting_target(client)
    if value is None:
        form.pop(field)
    else:
        form[field] = value
    before = snapshot()
    assert client.post(url, data=form, follow_redirects=False).status_code == 422
    assert snapshot() == before


@pytest.mark.parametrize("changed", ["partner", "info", "contact"])
def test_accounting_info_late_conflict_rolls_back_whole_transaction(client, monkeypatch, changed):
    from app import crud
    from app.database import SessionLocal
    from app.models import Partner, ContractContact
    from app.routers import accounting_data, contract_imports

    partner_id, contact_id, _, url, form = accounting_target(client)
    original = accounting_data.require_version
    before = []

    def winner():
        with SessionLocal() as other:
            if changed == "partner":
                other.get(Partner, partner_id).address = "Winner"
            elif changed == "info":
                crud.get_partner_accounting_info(other, partner_id).comment = "Winner"
            else:
                other.get(ContractContact, contact_id).comments = "Winner"
            other.commit()
        before.append(snapshot())

    if changed == "contact":
        original_find = contract_imports.find_partner_contact

        def racing_find(*args, **kwargs):
            contact = original_find(*args, **kwargs)
            if not before:
                winner()
            return contact

        monkeypatch.setattr(contract_imports, "find_partner_contact", racing_find)
    else:
        def racing_check(row, version):
            original(row, version)
            if type(row).__name__ == "PartnerAccountingInfo":
                winner()

        monkeypatch.setattr(accounting_data, "require_version", racing_check)
    result = client.post(url, data={**form, "legal_entity_name": "Loser"}, follow_redirects=False)
    assert result.status_code == 409, result.text
    assert snapshot() == before[0]


@pytest.mark.parametrize("info", [False, True])
def test_billing_sync_failure_rolls_back_info_parent_contacts_and_audit(client, monkeypatch, info):
    from app.routers import contract_imports

    _, _, _, url, form = accounting_target(client, info=info)
    original = contract_imports.sync_billing_contact
    before = snapshot()

    def failing_sync(*args, **kwargs):
        original(*args, **kwargs)
        raise StaleDataError("Injected billing sync failure")

    monkeypatch.setattr(contract_imports, "sync_billing_contact", failing_sync)
    result = client.post(url, data={**form, "billing_email": "new@example.test",
                                   "legal_entity_name": "Must roll back"}, follow_redirects=False)
    assert result.status_code == 409, result.text
    assert snapshot() == before


def period_target(client):
    from app import crud
    from app.database import SessionLocal

    _, contract_id, _, _, _, data = target("contract")
    with SessionLocal() as db:
        period = crud.create_accounting_period(db, partner_id=data["partner_id"],
                                               contract_id=contract_id, year=2026, quarter=1)
        service_id = crud.list_accounting_services(db)[0].id
        line = crud.add_accounting_line(db, period, service_id=service_id, quantity=2, contract_price=10)
        period_id, line_id = period.id, line.id
    page = f"/accounting-data/{period_id}"
    forms = Forms(client.get(page).text).forms
    return period_id, line_id, service_id, page, forms


@pytest.mark.parametrize("action", ["lines", "delete", "status", "recalculate"])
def test_period_forms_reject_stale_version_accept_reload_and_keep_totals(client, action):
    from app.database import SessionLocal
    from app.models import AccountingDataPeriod, AccountingDataLine

    period_id, line_id, service_id, page, forms = period_target(client)
    url = f"{page}/lines/{line_id}/delete" if action == "delete" else f"{page}/{action}"
    fields = {"service_id": service_id, "quantity": "3", "contract_price": "10"} if action == "lines" else {}
    if action == "status":
        fields = {"status": "sent_to_accounting"}
    stale = {**forms[url], **fields}
    with SessionLocal() as db:
        db.get(AccountingDataPeriod, period_id).comment = "Winner"
        db.commit()
    before = snapshot()
    assert client.post(url, data=stale, follow_redirects=False).status_code == 409
    assert snapshot() == before
    for invalid in (None, "0", "-1"):
        bad = dict(stale)
        if invalid is None:
            bad.pop("expected_version")
        else:
            bad["expected_version"] = invalid
        assert client.post(url, data=bad, follow_redirects=False).status_code == 422
    assert snapshot() == before
    fresh = Forms(client.get(page).text).forms[url]
    result = client.post(url, data={**fresh, **fields}, follow_redirects=False)
    assert result.status_code == 303, result.text
    with SessionLocal() as db:
        period = db.get(AccountingDataPeriod, period_id)
        assert period.total_contract_amount == {"lines": 50, "delete": 0}.get(action, 20)
        assert db.get(AccountingDataLine, line_id).is_deleted == (action == "delete")
        if action == "status":
            assert period.status == "sent_to_accounting"
            assert period.sent_to_accounting_at is not None


def test_line_delete_checks_membership_and_child_version(client):
    from app import crud
    from app.database import SessionLocal
    from app.models import AccountingDataPeriod, AccountingDataLine

    period_id, line_id, _, page, forms = period_target(client)
    url = f"{page}/lines/{line_id}/delete"
    stale = forms[url]
    with SessionLocal() as db:
        period = db.get(AccountingDataPeriod, period_id)
        another = crud.create_accounting_period(db, partner_id=period.partner_id,
                                                contract_id=period.contract_id, year=2026, quarter=2)
        wrong_url = f"/accounting-data/{another.id}/lines/{line_id}/delete"
        db.get(AccountingDataLine, line_id).comment = "Winner"
        db.commit()
    before = snapshot()
    assert client.post(wrong_url, data=stale, follow_redirects=False).status_code == 404
    assert client.post(url, data=stale, follow_redirects=False).status_code == 409
    missing = dict(stale)
    missing.pop("expected_line_version")
    assert client.post(url, data=missing, follow_redirects=False).status_code == 422
    assert snapshot() == before
    fresh = Forms(client.get(page).text).forms[url]
    assert client.post(url, data=fresh, follow_redirects=False).status_code == 303


@pytest.mark.parametrize("action", ["lines", "delete", "status", "recalculate"])
def test_period_late_conflict_rolls_back_lines_totals_status_and_audit(client, monkeypatch, action):
    from app import crud
    from app.database import SessionLocal
    from app.models import AccountingDataPeriod

    period_id, line_id, service_id, page, forms = period_target(client)
    name = {"lines": "add_accounting_line", "delete": "delete_accounting_line",
            "status": "update_accounting_period_status", "recalculate": "recalculate_accounting_period"}[action]
    url = f"{page}/lines/{line_id}/delete" if action == "delete" else f"{page}/{action}"
    fields = {"service_id": service_id, "quantity": "3", "contract_price": "10"} if action == "lines" else {}
    if action == "status":
        fields = {"status": "sent_to_accounting"}
    original = getattr(crud, name)
    before = []

    def racing_change(*args, **kwargs):
        with SessionLocal() as other:
            other.get(AccountingDataPeriod, period_id).comment = "Winner"
            other.commit()
        before.append(snapshot())
        return original(*args, **kwargs)

    monkeypatch.setattr(crud, name, racing_change)
    result = client.post(url, data={**forms[url], **fields}, follow_redirects=False)
    assert result.status_code == 409, result.text
    assert snapshot() == before[0]


def test_upsert_commit_false_leaves_transaction_to_caller(client):
    from app import crud
    from app.database import SessionLocal

    partner_id, _, _, _, _ = accounting_target(client)
    before = snapshot()
    with SessionLocal() as db:
        crud.upsert_partner_accounting_info(db, partner_id, {"legal_entity_name": "Uncommitted"}, commit=False)
        db.flush()
        db.rollback()
    assert snapshot() == before


def test_first_accounting_save_without_contacts_and_duplicate_submission(client):
    from app import crud
    from app.database import SessionLocal
    from app.models import ContractContact

    _, partner_id, _, _, _, _ = target("partner")
    page = f"/partners/{partner_id}"
    url = page + "/accounting-info"
    form = Forms(client.get(page).text).forms[url]
    assert form["expected_info_version"] == "0"
    assert form["expected_contact_versions"] == "{}"
    data = {**form, "billing_contact_name": "First Contact", "billing_email": "first@example.test"}
    response = client.post(url, data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    with SessionLocal() as db:
        assert crud.get_partner_accounting_info(db, partner_id).billing_email == "first@example.test"
        assert db.query(ContractContact).filter_by(partner_id=partner_id, is_billing_contact=True).count() == 1
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before
    refreshed = Forms(client.get(page).text).forms[url]
    assert refreshed["legal_entity_name"] == ""
    assert refreshed["inn"] == ""
    assert refreshed["comment"] == ""


def test_billing_unique_constraint_failure_rolls_back_the_whole_save(client):
    from app.database import SessionLocal
    from app.models import ContractContact

    _, contact_id, page, url, _ = accounting_target(client)
    with SessionLocal() as db:
        db.get(ContractContact, contact_id).is_deleted = True
        db.commit()
    form = Forms(client.get(page).text).forms[url]
    before = snapshot()
    response = client.post(url, data={**form, "legal_entity_name": "Must roll back"}, follow_redirects=False)
    assert response.status_code == 422, response.text
    assert snapshot() == before
