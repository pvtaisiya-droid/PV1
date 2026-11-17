from tests.form_helpers import post_current
import json
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from app.routers.contract_imports import normalize_payload, validate_payload


def source_value(value):
    return {"value": value}


def test_normalize_payload_preserves_canonical_partner_accounting():
    payload = {
        "schema_name": "pv_contract_import",
        "schema_version": "1.0",
        "partner": {
            "partner_code": source_value("GRAND"),
            "partner_name": source_value("ООО «ФК ГРАНД КАПИТАЛ»"),
            "accounting": {
                "legal_entity_name": source_value(
                    "Общество с ограниченной ответственностью «ФК ГРАНД КАПИТАЛ»"
                ),
                "inn": source_value("7729418511"),
                "kpp": source_value("771400001"),
                "billing_contact_name": "Сухорукова Надежда Ивановна",
                "billing_email": "NSukhorukova@grand-capital.pro",
                "comment": "Комментарий",
            },
        },
        "contract": {
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": source_value("967/26"),
            "contract_date": source_value("2026-07-01"),
            "valid_until": source_value("2027-12-31"),
            "parent_contract": {"contract_number": source_value(None)},
        },
    }

    normalized, warnings = normalize_payload(payload)

    assert warnings == []
    assert normalized["partner"]["partner_code"]["value"] == "GRAND"
    assert normalized["partner"]["accounting"] == payload["partner"]["accounting"]
    assert validate_payload(normalized) == []


def test_confirm_import_creates_partner_and_contract(client):
    payload = {
        "schema_name": "pv_contract_import",
        "schema_version": "1.0",
        "partner": {
            "partner_name": source_value("Тестовый партнер импорта"),
            "partner_type": "fn",
            "accounting": {
                "legal_entity_name": source_value("ООО Тестовый партнер импорта"),
                "inn": source_value("7729418511"),
                "kpp": source_value("771400001"),
                "billing_contact_name": "Исходный бухгалтер",
                "billing_email": "original-invoices@example.test",
                "comment": "Исходный порядок направления счетов.",
            },
        },
        "contract": {
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": source_value("967/26-TEST"),
            "contract_date": source_value("2026-07-01"),
            "valid_until": source_value("2027-12-31"),
            "parent_contract": {"contract_number": source_value(None)},
        },
        "contacts": [
            {
                "contact_type": "administrative",
                "last_name": source_value("Исходная"),
                "first_name": source_value("Анна"),
                "position": source_value("Менеджер"),
                "email": source_value("original@example.test"),
            }
        ],
        "products": [
            {
                "import_key": "test-product",
                "product_name": source_value("Исходный препарат"),
                "authorization_status": "registration_in_progress",
            }
        ],
    }

    response = post_current(client,
        "/contract-imports/confirm",
        data={
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "partner_name": "Тестовый партнер импорта",
            "partner_code": "FN-IMPORT-TEST",
            "accounting_legal_entity_name": "ООО Юрлицо для счета",
            "accounting_inn": "7729418511",
            "accounting_kpp": "771400001",
            "accounting_billing_contact_name": "Контакт для счетов",
            "accounting_billing_email": "invoices@example.test",
            "accounting_comment": "Направлять счета на специальный адрес.",
            "contract_number": "967/26-TEST",
            "contract_date": "2026-07-01",
            "valid_until": "2027-12-31",
            "include_contact_0": "yes",
            "contact_last_name_0": "Исправленная",
            "contact_first_name_0": "Анна",
            "contact_position_0": "Руководитель проекта",
            "contact_email_0": "edited@example.test",
            "contact_phone_0": "+79990000000",
            "include_product_0": "yes",
            "product_name_0": "Исправленный препарат",
            "product_strength_0": "100 мг",
            "product_route_0": "внутривенно",
            "confirm_review": "yes",
        },
    )

    assert response.status_code == 200
    assert "Импорт завершен" in response.text
    assert "FN-IMPORT-TEST" not in response.text  # Result is presented by name, not raw form data.

    from app.database import SessionLocal
    from app.models import Contract, ContractContact, Partner, PartnerAccountingInfo, Product

    with SessionLocal() as db:
        partner = db.query(Partner).filter(Partner.partner_code == "FN-IMPORT-TEST").one()
        accounting = db.query(PartnerAccountingInfo).filter_by(partner_id=partner.id).one()
        contract = db.query(Contract).filter_by(partner_id=partner.id).one()
        contact = db.query(ContractContact).filter_by(
            partner_id=partner.id,
            email="edited@example.test",
        ).one()
        billing_contact = db.query(ContractContact).filter_by(
            partner_id=partner.id,
            email="invoices@example.test",
        ).one()
        product = db.query(Product).filter_by(product_name="Исправленный препарат").one()
        assert accounting.inn == "7729418511"
        assert accounting.legal_entity_name == "ООО Юрлицо для счета"
        assert accounting.kpp == "771400001"
        assert accounting.billing_contact_name == "Контакт для счетов"
        assert accounting.billing_email == "invoices@example.test"
        assert accounting.comment == "Направлять счета на специальный адрес."
        assert contract.contract_number == "967/26-TEST"
        assert contact.last_name == "Исправленная"
        assert contact.email == "edited@example.test"
        assert billing_contact.is_billing_contact is True
        assert billing_contact.contact_type == "accounting"
        assert product.strength == "100 мг"
        assert product.id in contract.product_ids


def test_existing_partner_requires_confirmation_updates_accounting_and_reports_duplicates(client):
    from app.database import SessionLocal
    from app.models import ContractContact, Partner, PartnerAccountingInfo

    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        partner = Partner(
            partner_code=f"EXISTING-{suffix}",
            partner_name=f"Существующий партнер {suffix}",
            partner_type="fn",
            reconciliation_frequency="not_conducted",
        )
        db.add(partner)
        db.flush()
        db.add(PartnerAccountingInfo(
            partner_id=partner.id,
            legal_entity_name=f"ООО Существующий партнер {suffix}",
            inn="7701234567",
            kpp="770101001",
        ))
        db.add(ContractContact(
            partner_id=partner.id,
            last_name="Бухгалтер",
            first_name="Тестовый",
            email="invoices-existing@example.test",
            position="Менеджер проекта",
            is_current=True,
            is_pv_contact=True,
            is_reconciliation_recipient=False,
            cc_reconciliation=False,
            is_primary=False,
            is_billing_contact=False,
            contact_type="pv",
        ))
        db.commit()
        partner_id = partner.id

    payload = {
        "schema_name": "pv_contract_import",
        "schema_version": "1.0",
        "partner": {
            "partner_name": source_value(f"Существующий партнер {suffix}"),
            "partner_type": "fn",
            "accounting": {
                "legal_entity_name": source_value(f"ООО Существующий партнер {suffix}"),
                "inn": source_value("7701234567"),
                "kpp": source_value("770101001"),
                "billing_contact_name": "Бухгалтер Тестовый",
                "billing_email": "invoices-existing@example.test",
                "comment": "Направлять счета на специальный e-mail.",
            },
        },
        "contract": {
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": source_value(f"EXISTING-CONTRACT-{suffix}"),
            "contract_date": source_value("2026-07-01"),
            "valid_until": source_value("2027-12-31"),
        },
    }

    preview_response = post_current(client,
        "/contract-imports/preview",
        files={
            "import_file": (
                "existing-partner.json",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json",
            )
        },
    )
    assert preview_response.status_code == 200
    assert "Совпадения и возможные дубли" in preview_response.text
    assert "Партнер уже есть в PV1" in preview_response.text
    assert 'name="confirm_existing_partner"' in preview_response.text
    assert "Контакт уже есть" in preview_response.text
    assert "Новое назначение" in preview_response.text
    assert "Будет добавлена «Бухгалтерия»" in preview_response.text
    assert "bg-secondary-subtle" in preview_response.text
    assert "bg-success-subtle" in preview_response.text

    form_data = {
        "payload_json": json.dumps(payload, ensure_ascii=False),
        "partner_id": partner_id,
        "partner_name": f"Существующий партнер {suffix}",
        "accounting_legal_entity_name": f"ООО Существующий партнер {suffix}",
        "accounting_inn": "7701234567",
        "accounting_kpp": "770101001",
        "accounting_billing_contact_name": "Бухгалтер Тестовый",
        "accounting_billing_email": "invoices-existing@example.test",
        "accounting_comment": "Направлять счета на специальный e-mail.",
        "contract_type": "pharmacovigilance_agreement",
        "contract_number": f"EXISTING-CONTRACT-{suffix}",
        "contract_date": "2026-07-01",
        "valid_until": "2027-12-31",
        "confirm_review": "yes",
    }
    rejected_response = post_current(client, "/contract-imports/confirm", data=form_data)
    assert "Подтвердите, что выбранный существующий партнер" in rejected_response.text

    form_data["confirm_existing_partner"] = "yes"
    confirmed_response = post_current(client, "/contract-imports/confirm", data=form_data)
    assert confirmed_response.status_code == 200
    assert "Импорт завершен" in confirmed_response.text
    assert "Обнаружены дубли" in confirmed_response.text
    assert "ИНН уже содержится в карточке партнера" in confirmed_response.text
    assert "Добавлено или обновлено" in confirmed_response.text
    assert "добавлено второе назначение «Бухгалтерия»" in confirmed_response.text

    with SessionLocal() as db:
        accounting = db.query(PartnerAccountingInfo).filter_by(partner_id=partner_id).one()
        billing_contact = db.query(ContractContact).filter_by(
            partner_id=partner_id,
            email="invoices-existing@example.test",
        ).one()
        assert accounting.billing_contact_name == "Бухгалтер Тестовый"
        assert accounting.billing_email == "invoices-existing@example.test"
        assert accounting.comment == "Направлять счета на специальный e-mail."
        assert billing_contact.is_pv_contact is True
        assert billing_contact.is_billing_contact is True
        assert db.query(ContractContact).filter_by(partner_id=partner_id).count() == 1

    form_data["accounting_comment"] = "Отдельно сохраненный порядок направления счетов."
    accounting_only_response = post_current(client,
        "/contract-imports/confirm-accounting",
        data=form_data,
    )
    assert accounting_only_response.status_code == 200
    assert "Информация для бухгалтерии сохранена отдельно" in accounting_only_response.text
    assert "Обнаружены дубли" in accounting_only_response.text
    assert "Комментарий о порядке направления счетов: значение добавлено или обновлено" in accounting_only_response.text

    with SessionLocal() as db:
        accounting = db.query(PartnerAccountingInfo).filter_by(partner_id=partner_id).one()
        assert accounting.comment == "Отдельно сохраненный порядок направления счетов."


def test_preview_matches_existing_cyrillic_partner_without_accounting(client):
    from app.database import SessionLocal
    from app.models import Partner

    suffix = uuid4().hex[:8]
    partner_name = f"ООО «КИРИЛЛИЧЕСКИЙ ПАРТНЕР {suffix}»"
    with SessionLocal() as db:
        partner = Partner(
            partner_code=f"CYR-{suffix}",
            partner_name=partner_name,
            partner_type="fn",
            reconciliation_frequency="not_conducted",
        )
        db.add(partner)
        db.commit()

    payload = {
        "schema_name": "pv_contract_import",
        "schema_version": "1.0",
        "partner": {
            "partner_name": source_value(partner_name),
            "accounting": {
                "legal_entity_name": source_value(partner_name),
                "inn": source_value("7701234567"),
                "kpp": source_value("770101001"),
            },
        },
        "contract": {
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": source_value(f"CYR-{suffix}"),
            "contract_date": source_value("2026-07-01"),
            "valid_until": source_value("2027-12-31"),
        },
    }
    response = post_current(client,
        "/contract-imports/preview",
        files={
            "import_file": (
                "cyrillic-partner.json",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json",
            )
        },
    )

    assert response.status_code == 200
    assert "Найдено одно совпадение" in response.text
    assert f"CYR-{suffix} / {partner_name}" in response.text
    assert "Партнер уже есть в PV1" in response.text


def test_gpt_package_contains_current_prompt_schema_references_and_example(client):
    response = client.get("/contract-imports/gpt-package")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(response.content)) as package:
        assert set(package.namelist()) == {
            "GPT_PROMPT.txt",
            "README.txt",
            "contract_import.schema.json",
            "contract_import_reference_values.json",
            "contract_import_example.json",
        }
        prompt = package.read("GPT_PROMPT.txt").decode("utf-8")
        references = json.loads(package.read("contract_import_reference_values.json"))
        example = json.loads(package.read("contract_import_example.json"))

    assert "Извлекай только следующие данные" in prompt
    assert "одной нумерованной позиции относятся к одному препарату" in prompt
    assert "не создавай дополнительный препарат" in prompt
    assert "не подменяй специальный бухгалтерский e-mail общим адресом организации" in prompt
    assert "используй его как billing_contact_name" in prompt
    assert "контакты ООО «АРС»" in prompt
    assert "substances" in references
    assert "dosage_forms" in references
    assert "obligation_types" not in references
    assert "message_types" not in references
    assert validate_payload(example) == []


def test_contract_import_example_matches_schema():
    payload = json.loads(
        (Path(__file__).resolve().parents[1] / "examples" / "contract_import_example.json").read_text(encoding="utf-8")
    )

    assert validate_payload(payload) == []


def test_normalize_preserves_missing_product_name_for_manual_correction():
    payload = {
        "schema_name": "pv_contract_import",
        "schema_version": "1.0",
        "partner": {"partner_name": source_value("Партнер")},
        "contract": {
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": source_value("1"),
            "contract_date": source_value("2026-01-01"),
            "valid_until": source_value("2027-01-01"),
        },
        "products": [
            {
                "import_key": "missing-trade-name",
                "product_name": {
                    "value": None,
                    "source": {"requires_manual_review": True},
                },
            }
        ],
        "manual_review": [
            {
                "category": "missing_required_data",
                "description": "Торговое наименование не найдено.",
                "recommended_action": "Заполнить вручную.",
            }
        ],
    }

    normalized, warnings = normalize_payload(payload)

    assert warnings == []
    assert normalized["products"][0]["product_name"]["value"] is None
    assert validate_payload(normalized) == []


def test_normalize_preserves_canonical_nested_contact_phones():
    payload = {
        "schema_name": "pv_contract_import",
        "schema_version": "1.0",
        "partner": {"partner_name": source_value("Партнер")},
        "contract": {
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": source_value("1"),
            "contract_date": source_value("2026-01-01"),
            "valid_until": source_value("2027-01-01"),
        },
        "contacts": [
            {
                "contact_type": "pv",
                "last_name": source_value("Иванова"),
                "first_name": source_value("Анна"),
                "position": source_value("Менеджер"),
                "phones": {
                    "mobile": source_value("+7 900 000-00-00"),
                    "work": source_value("+7 495 000-00-00"),
                    "extension": source_value("123"),
                },
            }
        ],
    }

    normalized, warnings = normalize_payload(payload)

    assert warnings == []
    assert normalized["contacts"][0]["phones"] == payload["contacts"][0]["phones"]
    assert validate_payload(normalized) == []


def test_normalize_excludes_ars_contacts_from_partner_import():
    payload = {
        "schema_name": "pv_contract_import",
        "schema_version": "1.0",
        "partner": {"partner_name": source_value("Партнер")},
        "contract": {
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": source_value("1"),
            "contract_date": source_value("2026-01-01"),
            "valid_until": source_value("2027-01-01"),
        },
        "contacts": [
            {
                "contact_type": "pv",
                "last_name": source_value("Партнерова"),
                "first_name": source_value("Анна"),
                "position": source_value("Менеджер"),
                "email": source_value("partner@example.test"),
            },
            {
                "contact_type": "pv",
                "last_name": source_value("Воркунова"),
                "first_name": source_value("Таисия"),
                "position": source_value("УЛФ ООО «АРС»"),
                "email": source_value("pv@pharmrussia.com"),
                "comments": "Контакт со стороны Исполнителя ООО «АРС».",
            },
        ],
    }

    normalized, warnings = normalize_payload(payload)

    assert len(normalized["contacts"]) == 1
    assert normalized["contacts"][0]["email"]["value"] == "partner@example.test"
    assert any("ООО «АРС»" in warning and "исключён" in warning for warning in warnings)
    assert validate_payload(normalized) == []


def test_contract_import_preview_shows_only_requested_review_sections(client):
    example_path = Path(__file__).resolve().parents[1] / "examples" / "contract_import_example.json"
    response = post_current(client,
        "/contract-imports/preview",
        files={"import_file": (example_path.name, example_path.read_bytes(), "application/json")},
    )

    assert response.status_code == 200
    for label in (
        "Статус",
        "Частота сверки",
        "Информация для бухгалтерии",
        "Юрлицо для счета",
        "ИНН",
        "КПП",
        "Контакт для счетов",
        "E-mail для счетов",
        "Комментарий",
        "Основной договор",
        "Добавочный номер",
        "Наименование вещества RU",
        "ЛФ на русском",
        "Комментарий к услуге",
        "Мобильный:",
    ):
        assert label in response.text
    assert "+7 900 000-00-00" in response.text
    assert "invoices@example-pharma.ru" in response.text
    assert "Новое" in response.text
    assert "bg-success-subtle" in response.text
    assert "Обязанности и сроки передачи сообщений" not in response.text
    assert "Реквизиты сторон" not in response.text


def test_confirm_new_format_creates_product_reference_values(client):
    suffix = uuid4().hex[:8]
    payload = json.loads(
        (Path(__file__).resolve().parents[1] / "examples" / "contract_import_example.json").read_text(encoding="utf-8")
    )
    payload["partner"]["partner_name"]["value"] = f"Новый формат {suffix}"
    payload["contract"]["contract_number"]["value"] = f"PV-NEW-{suffix}"
    payload["contract"]["valid_until"]["value"] = "2027-12-31"
    payload["products"][0]["product_name"]["value"] = f"Препарат {suffix}"
    payload["products"][0]["active_substances"][0]["name_ru"]["value"] = f"Вещество {suffix}"
    payload["products"][0]["dosage_form"]["reference_code"] = None
    payload["products"][0]["dosage_form"]["name_ru"]["value"] = f"Форма {suffix}"
    payload["products"][0]["dosage_form"]["add_new"] = True

    response = post_current(client,
        "/contract-imports/confirm",
        data={
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "partner_name": f"Новый формат {suffix}",
            "partner_code": f"NEW-{suffix}",
            "partner_type": "fn",
            "reconciliation_frequency": "monthly",
            "contract_type": "pharmacovigilance_agreement",
            "contract_number": f"PV-NEW-{suffix}",
            "contract_date": "2026-02-15",
            "valid_until": "2027-12-31",
            "include_product_0": "yes",
            "product_name_0": f"Препарат {suffix}",
            "product_substance_name_ru_0": f"Вещество {suffix}",
            "product_substance_name_en_0": "Substance",
            "product_substance_atc_0": "A00AA00",
            "product_dosage_form_name_ru_0": f"Форма {suffix}",
            "product_dosage_form_name_en_0": "Form",
            "product_dosage_form_group_ru_0": "Группа",
            "product_dosage_form_group_en_0": "Group",
            "product_strength_0": "100 мг",
            "product_comment_0": "Проверено",
            "confirm_review": "yes",
        },
    )

    assert response.status_code == 200
    assert "Импорт завершен" in response.text
    from app.database import SessionLocal
    from app.models import Product

    with SessionLocal() as db:
        product = db.query(Product).filter(Product.product_name == f"Препарат {suffix}").one()
        assert product.dosage_form_ref.name_ru == f"Форма {suffix}"
        assert product.substance_links[0].substance.substance_name == f"Вещество {suffix}"
        assert product.comment == "Проверено"
