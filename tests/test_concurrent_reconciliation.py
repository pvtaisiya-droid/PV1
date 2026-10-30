import pytest
from sqlalchemy import text

from tests.test_concurrent_forms import Forms


def snapshot():
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, PartnerReconciliationItem, AuditTrail

    with SessionLocal() as db:
        return {model.__tablename__: db.execute(text(
            f'SELECT * FROM "{model.__tablename__}" ORDER BY id'
        )).all() for model in (PartnerReconciliation, PartnerReconciliationItem, AuditTrail)}


def target(client, action):
    from app import crud, schemas
    from app.database import SessionLocal

    with SessionLocal() as db:
        partner = crud.create_partner(db, schemas.PartnerCreate(
            partner_code="CONCURRENT-RECON", partner_name="Reconciliation parent",
            reconciliation_frequency="monthly"))
        row = crud.create_partner_reconciliation(db, schemas.PartnerReconciliationCreate(
            partner_id=partner.id, period_start="2026-09-01", period_end="2026-09-30"), [
                dict(source_side="partner", partner_case_id="one", reconciliation_status="matched"),
                dict(source_side="our_company", partner_case_id="two", reconciliation_status="matched"),
            ])
        row_id, item_id = row.id, row.items[0].id
    page = f"/partner-reconciliation?reconciliation_id={row_id}"
    url = (f"/partner-reconciliation/items/{item_id}" if action == "item"
           else f"/partner-reconciliation/{row_id}/{action}")
    response = client.get(page)
    assert response.status_code == 200, response.text
    form = Forms(response.text).forms[url]
    form.update(reconciliation_status="confirmed", reviewer_comment="Saved",
                comments="Saved", confirmed_by_user="Reviewer")
    return row_id, item_id, page, url, form


@pytest.mark.parametrize("action", ["confirm", "status", "item"])
def test_stale_parent_rejected_then_reload_succeeds(client, action):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation

    row_id, _, page, url, form = target(client, action)
    with SessionLocal() as db:
        db.get(PartnerReconciliation, row_id).comments = "Winner"
        db.commit()
    before = snapshot()
    response = client.post(url, data=form, follow_redirects=False)
    assert response.status_code == 409, response.text
    assert "Обновите страницу" in response.json()["detail"]
    assert snapshot() == before
    form.update(Forms(client.get(page).text).forms[url])
    response = client.post(url, data=form, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert client.post(url, data=form, follow_redirects=False).status_code == 409


@pytest.mark.parametrize("field,value", [
    ("expected_version", None), ("expected_version", "0"),
    ("expected_version", "invalid"), ("expected_item_version", None),
    ("expected_item_version", "0"), ("reconciliation_id", None),
])
def test_item_requires_valid_tokens(client, field, value):
    _, _, _, url, form = target(client, "item")
    if value is None:
        form.pop(field)
    else:
        form[field] = value
    before = snapshot()
    assert client.post(url, data=form, follow_redirects=False).status_code == 422
    assert snapshot() == before


@pytest.mark.parametrize("action", ["confirm", "status"])
@pytest.mark.parametrize("value", [None, "0", "invalid"])
def test_parent_action_requires_token(client, action, value):
    _, _, _, url, form = target(client, action)
    if value is None:
        form.pop("expected_version")
    else:
        form["expected_version"] = value
    before = snapshot()
    assert client.post(url, data=form, follow_redirects=False).status_code == 422
    assert snapshot() == before


@pytest.mark.parametrize("change", ["item", "deleted_item", "deleted_parent", "foreign_parent"])
def test_item_checks_version_and_ownership(client, change):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, PartnerReconciliationItem

    row_id, item_id, _, url, form = target(client, "item")
    with SessionLocal() as db:
        if change == "item":
            db.get(PartnerReconciliationItem, item_id).reviewer_comment = "Winner"
        elif change == "deleted_item":
            db.get(PartnerReconciliationItem, item_id).is_deleted = True
        elif change == "deleted_parent":
            db.get(PartnerReconciliation, row_id).is_deleted = True
        else:
            form["reconciliation_id"] = "another-parent"
        db.commit()
    before = snapshot()
    assert client.post(url, data=form, follow_redirects=False).status_code == (409 if change == "item" else 404)
    assert snapshot() == before


@pytest.mark.parametrize("action,changed", [
    ("confirm", "parent"), ("confirm", "item"),
    ("status", "parent"), ("item", "parent"), ("item", "item"),
])
def test_late_conflict_rolls_back_parent_items_counts_and_audit(client, monkeypatch, action, changed):
    from app import crud
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, PartnerReconciliationItem
    from app.routers import partner_reconciliation as routes

    row_id, item_id, _, url, form = target(client, action)
    before = []

    def winner():
        with SessionLocal() as db:
            if changed == "parent":
                db.get(PartnerReconciliation, row_id).comments = "Winner"
            else:
                db.get(PartnerReconciliationItem, item_id).reviewer_comment = "Winner"
            db.commit()
        before.append(snapshot())

    if action == "confirm":
        original = crud.refresh_partner_reconciliation_counts

        def racing_counts(row):
            original(row)
            winner()

        monkeypatch.setattr(crud, "refresh_partner_reconciliation_counts", racing_counts)
    else:
        original = routes.require_version

        def racing_check(row, version):
            original(row, version)
            if action == "status" or isinstance(row, PartnerReconciliationItem):
                winner()

        monkeypatch.setattr(routes, "require_version", racing_check)
    response = client.post(url, data=form, follow_redirects=False)
    assert response.status_code == 409, response.text
    assert snapshot() == before[0]


def test_comment_only_item_change_advances_parent_and_rejects_other_open_forms(client):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, PartnerReconciliationItem

    row_id, item_id, page, url, form = target(client, "item")
    all_forms = Forms(client.get(page).text).forms
    form["reconciliation_status"] = "matched"
    with SessionLocal() as db:
        previous = db.get(PartnerReconciliation, row_id).version
    assert client.post(url, data=form, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        row = db.get(PartnerReconciliation, row_id)
        assert row.version == previous + 1
        assert row.matched_count == 2 and row.discrepancy_count == 0
        assert db.get(PartnerReconciliationItem, item_id).reviewer_comment == "Saved"
    before = snapshot()
    for action in ("confirm", "status"):
        other_url = f"/partner-reconciliation/{row_id}/{action}"
        data = {**all_forms[other_url], "reconciliation_status": "confirmed"}
        assert client.post(other_url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before


def test_confirm_updates_active_items_only(client):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, PartnerReconciliationItem

    row_id, item_id, _, url, form = target(client, "confirm")
    with SessionLocal() as db:
        db.get(PartnerReconciliationItem, item_id).is_deleted = True
        db.commit()
    assert client.post(url, data=form, follow_redirects=False).status_code == 409
    form['expected_version'] = Forms(client.get('/partner-reconciliation?reconciliation_id='+row_id).text).forms[url]['expected_version']
    assert client.post(url, data=form, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        row = db.get(PartnerReconciliation, row_id)
        assert row.reconciliation_status == "confirmed"
        assert row.confirmed_at and row.confirmed_by_user == "Reviewer"
        assert row.matched_count == 1 and row.discrepancy_count == 0
        assert db.get(PartnerReconciliationItem, item_id).reconciliation_status == "matched"
        assert all(item.reconciliation_status == "confirmed" for item in row.items if not item.is_deleted)


def test_item_change_updates_counts_and_audit_in_same_transaction(client):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation, PartnerReconciliationItem, AuditTrail

    row_id, item_id, _, url, form = target(client, "item")
    form["reconciliation_status"] = "requires_review"
    assert client.post(url, data=form, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        row = db.get(PartnerReconciliation, row_id)
        assert row.matched_count == 1 and row.discrepancy_count == 1
        assert db.get(PartnerReconciliationItem, item_id).reviewer_comment == "Saved"
        event = db.query(AuditTrail).filter_by(entity_id=item_id,
                                              field_name="reconciliation_status").one()
        assert event.new_value == "requires_review"


def test_invalid_case_reference_rolls_back_item_parent_and_audit(client):
    _, _, _, url, form = target(client, "item")
    form["internal_case_id"] = "missing-case"
    before = snapshot()
    assert client.post(url, data=form, follow_redirects=False).status_code == 422
    assert snapshot() == before
