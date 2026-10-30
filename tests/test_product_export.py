from datetime import date
from html import unescape
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile


def xlsx_first_sheet_text(payload: bytes) -> str:
    with ZipFile(BytesIO(payload)) as workbook:
        return unescape(workbook.read("xl/worksheets/sheet1.xml").decode("utf-8"))


def test_products_can_be_exported_for_selected_dra_and_reused_by_psmf(client):
    from app import crud, psmf, schemas
    from app.database import SessionLocal

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"DRA-{suffix}",
                partner_name=f"ДРУ для выгрузки {suffix}",
            ),
        )
        other_partner = crud.create_partner(
            db,
            schemas.PartnerCreate(
                partner_code=f"OTHER-{suffix}",
                partner_name=f"Другой ДРУ {suffix}",
            ),
        )
        mah_product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"MAH-{suffix}",
                product_name=f"Препарат ДРУ {suffix}",
                active_substance=f"МНН {suffix}",
                dosage_form="таблетки",
                strength="10 мг",
                mah_partner_id=partner.id,
                comment="Экспортируемый препарат",
            ),
        )
        crud.create_product_registration_certificate(
            db,
            schemas.ProductRegistrationCertificateCreate(
                product_id=mah_product.id,
                registration_number=f"РУ-{suffix}",
                registration_date=date(2026, 1, 10),
                valid_until=date(2031, 1, 10),
                status="active",
                holder=partner.partner_name,
                country_code="RU",
            ),
        )
        contract_product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"CONTRACT-{suffix}",
                product_name=f"Препарат по договору {suffix}",
            ),
        )
        crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=partner.id,
                product_id=contract_product.id,
                product_ids=[contract_product.id],
                contract_type="pharmacovigilance_agreement",
                contract_number=f"PV-{suffix}",
                contract_date=date(2026, 1, 1),
                valid_until=date(2027, 1, 1),
            ),
        )
        unrelated_product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_code=f"OTHER-{suffix}",
                product_name=f"Чужой препарат {suffix}",
                mah_partner_id=other_partner.id,
            ),
        )
        partner_id = partner.id
        expected_psmf_ids = {mah_product.id, contract_product.id}
        unrelated_product_name = unrelated_product.product_name

        assert {
            product.id for product in psmf.partner_products(db, partner)
        } == expected_psmf_ids

    page = client.get(f"/products?partner_id={partner_id}")
    assert page.status_code == 200
    assert f"/api/products/export.xlsx?partner_id={partner_id}" in page.text

    response = client.get(f"/api/products/export.xlsx?partner_id={partner_id}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.headers["content-disposition"] == (
        "attachment; filename=dra_product_list.xlsx"
    )

    sheet = xlsx_first_sheet_text(response.content)
    assert "Код препарата" in sheet
    assert f"Препарат ДРУ {suffix}" in sheet
    assert f"МНН {suffix}" in sheet
    assert f"РУ-{suffix}" in sheet
    assert f"Препарат по договору {suffix}" in sheet
    assert f"PV-{suffix}" in sheet
    assert unrelated_product_name not in sheet


def test_product_export_requires_existing_dra(client):
    response = client.get("/api/products/export.xlsx?partner_id=missing")
    assert response.status_code == 404
