import json

import pytest

from tests.test_concurrent_forms import Forms
from tests.test_e2b_import import e2b_xml, make_zip, upload_package


def seed_candidates():
    from app.database import SessionLocal
    from app.models import Product, ProductSubstance, Substance
    with SessionLocal() as db:
        substance = Substance(substance_name="Тестовое вещество", inn_name="Test substance")
        db.add(substance)
        db.flush()
        products = [Product(product_name="Brand A", product_code="MNN-A", strength="10 mg"),
                    Product(product_name="Brand B", product_code="MNN-B", strength="20 mg"),
                    Product(product_name="Other brand", product_code="MNN-C", is_company_product=False)]
        db.add_all(products)
        db.flush()
        db.add_all([ProductSubstance(product_id=row.id, substance_id=substance.id) for row in products])
        db.commit()
        return [row.id for row in products], substance.id


def uploaded(client, xml=None):
    response = upload_package(client, make_zip({"case.xml": xml or inn_xml()}))
    assert response.status_code == 303
    return response.headers["location"].split("?", 1)[0]


def inn_xml():
    return e2b_xml().replace(b"Test Product", b"TEST SUBSTANCE")


def matching_form(client, path, index=0):
    page = client.get(path)
    group = page.context["preview"]["case_groups"][index]
    target = f"{path}/resolve-products?case_group={group['token']}"
    return target, Forms(page.text).forms[target], group, page


def choose(client, path, product_id, index=0):
    target, data, group, _ = matching_form(client, path, index)
    data.update(product_sources=[group["products"][0]["match"]["source_token"]],
                product_choices=[product_id], reason="Verified synthetic product and source")
    response = client.post(target, data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response


def test_trade_name_then_inn_and_complete_active_composition(client):
    from app.database import SessionLocal
    from app.e2b_import import E2BMatchingService, E2BPackageService
    from app.models import Product, ProductSubstance, Substance
    ids, substance_id = seed_candidates()
    with SessionLocal() as db:
        match = E2BPackageService().match_product_scope
        direct = match(db, dict(reported_product_name="  BRAND A ", active_substances=["Test substance"]))
        assert direct["product_id"] == ids[0] and direct["matched_by"] == "trade_name"
        assert len(direct["candidates"]) == 1
        for source in [dict(reported_product_name="TEST SUBSTANCE"),
                       dict(reported_product_name="Тестовое вещество"),
                       dict(reported_product_name="Unknown brand", active_substances=["Test substance"]),
                       dict(active_substances=["Test substance"])]:
            result = match(db, source)
            assert result["status"] == "substance_candidates" and not result["is_company_product"]
            assert result["product_id"] is None
            assert {row["product_id"] for row in result["candidates"]} == set(ids)
            assert E2BMatchingService().find_product(db, source) == (None, "requires_choice")
        assert not match(db, dict(reported_product_name="Test substance sodium"))["candidates"]
        assert not match(db, dict(active_substances=["Test substance", "Unknown ingredient"]))["candidates"]
        second = Substance(substance_name="Второе вещество", inn_name="Second substance")
        db.add(second)
        db.flush()
        # Brand B is now a combination and cannot stand for the single INN.
        db.add(ProductSubstance(product_id=ids[1], substance_id=second.id))
        db.get(Product, ids[2]).is_active = False
        db.commit()
        single = match(db, dict(reported_product_name="Test substance"))
        assert [row["product_id"] for row in single["candidates"]] == [ids[0]]
        assert not single["product_id"] and not single["is_company_product"]
        combo = match(db, dict(active_substances=["Test substance", "Second substance"]))
        assert [row["product_id"] for row in combo["candidates"]] == [ids[1]]
        link = db.query(ProductSubstance).filter_by(product_id=ids[0], substance_id=substance_id).one()
        link.substance_role = "excipient"
        db.commit()
        assert not match(db, dict(reported_product_name="Test substance"))["candidates"]


def test_ambiguous_trade_name_never_selects_the_only_company_product(client):
    from app.database import SessionLocal
    from app.e2b_import import E2BMatchingService, E2BPackageService
    from app.models import Product
    with SessionLocal() as db:
        db.add_all([Product(product_code="DUP-A", product_name="Shared brand"),
                    Product(product_code="DUP-B", product_name="Shared brand", is_company_product=False)])
        db.commit()
        source = dict(reported_product_name="Shared brand")
        result = E2BPackageService().match_product_scope(db, source)
        assert result["status"] == "requires_choice" and result["matched_by"] == "trade_name"
        assert not result["product_id"] and not result["is_company_product"]
        assert E2BMatchingService().find_product(db, source) == (None, "requires_choice")


@pytest.mark.parametrize("source_kind", ["inn_in_name", "unknown_trade_name", "no_trade_name"])
def test_confirmed_inn_import_preserves_source_and_all_product_links(client, source_kind):
    from app.database import SessionLocal
    from app.models import AuditTrail, CaseProduct, E2BImportBatch, IncomingRequestProduct, SafetyReportProduct
    ids, _ = seed_candidates()
    xml = inn_xml()
    if source_kind == "unknown_trade_name":
        xml = xml.replace(b"TEST SUBSTANCE", b"Source trade name")
    elif source_kind == "no_trade_name":
        xml = xml.replace(b"<medicinalproduct>TEST SUBSTANCE</medicinalproduct>", b"")
    path = uploaded(client, xml)
    target, data, group, page = matching_form(client, path)
    assert not group["can_import"] and not group["has_company_product"]
    assert "сначала торговое название, затем МНН" in page.text
    original = group["products"][0]["reported_product_name"]
    source_key = group["products"][0]["match"]["source_token"]
    data.update(product_sources=[source_key], product_choices=[ids[1]], reason="Verified synthetic source")
    assert client.post(target, data={**data, "csrf": "bad"}).status_code == 403
    rejected = client.post(target, data={**data, "product_choices": ["unrelated-product"]})
    assert "Выбранный препарат отсутствует" in rejected.text
    choose(client, path, ids[1])
    assert client.post(target, data=data).status_code == 409
    page = client.get(path)
    group = page.context["preview"]["case_groups"][0]
    assert group["can_import"] and group["has_company_product"]
    assert page.context["preview"]["product_summary"]["has_company_product"]
    assert f'{path}/mark-not-pv-ars' not in Forms(page.text).forms
    assert group["products"][0]["match"]["status"] == "confirmed_company_product"
    _, current_data, _, _ = matching_form(client, path)
    rejected_mark = client.post(f"{path}/mark-not-pv-ars", data=current_data)
    assert "Сопоставлен препарат нашей компании" in rejected_mark.text
    # The same choice is projected into the existing detailed intake form.
    intake = client.get(f"{path}/intake?case_group={group['token']}&step=pv")
    assert intake.context["ae_state"]["medications"][0]["companyId"] == ids[1]
    confirm = f"{path}/confirm?case_group={group['token']}"
    assert client.post(confirm, data=Forms(page.text).forms[confirm], follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        row = db.query(CaseProduct).one()
        assert row.product_id == ids[1] and row.reported_product_name == original
        assert row.product_match_status == "manual" and row.dose_value == "10"
        assert db.query(SafetyReportProduct).one().product_id == ids[1]
        assert db.query(IncomingRequestProduct).one().product_id == ids[1]
        batch = db.query(E2BImportBatch).one()
        decisions = json.loads(batch.import_summary_json)["groups"][group["token"]]["product_resolutions"]
        assert decisions[source_key]["product_id"] == ids[1] and decisions[source_key]["user_id"]
        assert decisions[source_key]["reason"] and decisions[source_key]["confirmed_at"]
        assert db.query(AuditTrail).filter_by(action="resolve_products").count() == 1
        assert "_product_resolution" not in batch.items[0].normalized_json


def test_dictionary_changes_recheck_and_reconfirmation(client):
    from app.database import SessionLocal
    from app.models import Product
    ids, _ = seed_candidates()
    path = uploaded(client)
    choose(client, path, ids[0])
    page = client.get(path)
    assert client.post(f"{path}/recheck", data=Forms(page.text).forms[f"{path}/recheck"]).status_code == 200
    target, data, group, _ = matching_form(client, path)
    data.update(product_choices=[ids[0]], reason="Synthetic repeated confirmation")
    assert group["can_import"]  # Unchanged source and dictionaries preserve the decision.
    assert not group["stale_product_choices"]
    with SessionLocal() as db:
        db.get(Product, ids[0]).strength = "11 mg"
        db.commit()
    assert client.post(target, data=data).status_code == 409
    _, _, group, page = matching_form(client, path)
    assert not group["can_import"] and group["stale_product_choices"]
    assert "Прежнее сопоставление изменилось" in page.text
    choose(client, path, ids[0])
    assert matching_form(client, path)[2]["can_import"]
    with SessionLocal() as db:
        db.get(Product, ids[0]).is_deleted = True
        db.commit()
    _, _, group, _ = matching_form(client, path)
    assert not group["can_import"] and group["stale_product_choices"]
    assert ids[0] not in {row["product_id"] for row in group["products"][0]["match"]["candidates"]}


def test_inn_choice_is_scoped_to_one_case_and_non_company_choice_does_not_enable_import(client):
    from app.database import SessionLocal
    from app.models import Case, CaseProduct
    ids, _ = seed_candidates()
    xml = inn_xml()
    response = upload_package(client, make_zip({"a/case.xml": xml,
        "b/case.xml": xml.replace(b"WW-CASE-001", b"WW-CASE-002").replace(b"MSG-E2B-001", b"MSG-E2B-002")}))
    path = response.headers["location"].split("?", 1)[0]
    choose(client, path, ids[2], 0)
    assert not matching_form(client, path, 0)[2]["can_import"]
    choose(client, path, ids[0], 0)
    page = client.get(path)
    groups = page.context["preview"]["case_groups"]
    assert groups[0]["can_import"] and not groups[1]["can_import"]
    target = f"{path}/confirm-selection"
    data = Forms(page.text).forms[target]
    data["selected_groups"] = [groups[0]["token"]]
    assert client.post(target, data=data, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.query(Case).count() == 1 and db.query(CaseProduct).one().product_id == ids[0]
    assert not matching_form(client, path, 1)[2]["can_import"]
