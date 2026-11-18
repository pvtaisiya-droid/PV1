import pytest


@pytest.mark.parametrize("product_status", ["registration_in_progress", "archived", "ma_change"])
def test_second_registration_in_card_preserves_product_lifecycle_status(client, product_status):
    from app import crud, schemas
    from app.database import SessionLocal

    with SessionLocal() as db:
        product = crud.create_product(
            db,
            schemas.ProductCreate(product_name="Two registration certificates", authorization_status=product_status),
        )
        product_id = product.id
        crud.create_product_registration_certificate(
            db,
            schemas.ProductRegistrationCertificateCreate(product_id=product_id, registration_number="RU-FIRST"),
        )

    # The first certificate has been reloaded from SQLite; the second receives
    # an aware timestamp in the current write transaction.
    with SessionLocal() as db:
        version = crud.get_product(db, product_id).version
    response = client.post(
        f"/products/{product_id}/registrations",
        data={"registration_number": "RU-SECOND", "expected_version": version, "detail_form": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/products/{product_id}?edit=1&message=")
    with SessionLocal() as db:
        product = crud.get_product(db, product_id)
        assert product.authorization_status == product_status
        assert {row.registration_number for row in crud.product_registration_certificates(product)} == {"RU-FIRST", "RU-SECOND"}
    card = client.get(f"/products/{product_id}")
    assert card.status_code == 200
    assert "RU-FIRST" in card.text and "RU-SECOND" in card.text
    with SessionLocal() as db:
        certificate = next(row for row in crud.product_registration_certificates(crud.get_product(db, product_id)) if row.registration_number == "RU-SECOND")
        certificate_id, certificate_version = certificate.id, certificate.version
    deleted = client.post(
        f"/products/registrations/{certificate_id}/delete",
        data={"expected_version": certificate_version, "detail_form": "true", "delete_reason": "Card regression check"},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert deleted.headers["location"].startswith(f"/products/{product_id}?edit=1&message=")
    from app.models import ProductRegistrationCertificate, AuditTrail
    with SessionLocal() as db:
        certificate = db.get(ProductRegistrationCertificate, certificate_id)
        assert certificate.is_deleted and not certificate.is_active
        assert certificate.version > certificate_version
        assert certificate.delete_reason == "Card regression check"
        assert db.query(AuditTrail).filter_by(entity_type="ProductRegistrationCertificate", entity_id=certificate_id, action="soft_delete").count() == 1
        product = crud.get_product(db, product_id)
        assert product.authorization_status == product_status
        assert [row.registration_number for row in crud.product_registration_certificates(product)] == ["RU-FIRST"]
