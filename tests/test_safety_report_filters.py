import pytest


def test_single_product_filter_keeps_all_report_and_case_products(client):
    from app import crud, schemas
    from app.database import SessionLocal

    with SessionLocal() as db:
        products = [crud.create_product(db, schemas.ProductCreate(product_name=f"Filter product {index}")) for index in range(4)]
        product_ids = [product.id for product in products]
        report = crud.create_safety_report(db, schemas.SafetyReportCreate(
            raw_subject="Matching report", message_direction="partner_to_our_company", product_ids=product_ids[:2],
        ))
        report_id = report.id
        case = crud.create_case_from_report(db, report)
        crud.add_case_product(db, case, schemas.CaseProductCreate(product_id=product_ids[2], reported_product_name="Case-only product"))
        crud.create_safety_report(db, schemas.SafetyReportCreate(
            raw_subject="Unrelated report", message_direction="our_company_to_partner", product_ids=[product_ids[3]],
        ))

    for selected in (product_ids[0], product_ids[2]):
        response = client.get("/safety-reports", params={"product_id": selected})
        assert response.status_code == 200
        assert [row.id for row in response.context["reports"]] == [report_id]
        assert response.context["pagination"].total == 1
        assert response.context["filters"]["product_id"] == selected
        assert set(response.context["report_product_names"][report_id]) == {"Filter product 0", "Filter product 1", "Case-only product"}
        with SessionLocal() as db:
            assert set(crud.get_safety_report(db, report_id).product_ids) == set(product_ids[:2])


@pytest.mark.parametrize("deleted_part", ["report_link", "case_product", "case"])
def test_product_filter_ignores_deleted_report_or_case_links(client, deleted_part):
    from app import crud, schemas
    from app.database import SessionLocal

    with SessionLocal() as db:
        product = crud.create_product(db, schemas.ProductCreate(product_name="Inactive product association"))
        product_id = product.id
        report = crud.create_safety_report(db, schemas.SafetyReportCreate(
            message_direction="partner_to_our_company", product_ids=[product_id] if deleted_part == "report_link" else [],
        ))
        if deleted_part == "report_link":
            report.product_links[0].is_deleted = True
        else:
            case = crud.create_case_from_report(db, report)
            case_product = crud.add_case_product(db, case, schemas.CaseProductCreate(product_id=product_id))
            if deleted_part == "case_product":
                case_product.is_deleted = True
            else:
                case.is_deleted = True
        db.commit()

    response = client.get("/safety-reports", params={"product_id": product_id})
    assert response.status_code == 200
    assert response.context["reports"] == []
    assert response.context["pagination"].total == 0
