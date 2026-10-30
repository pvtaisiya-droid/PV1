from datetime import date, timedelta
from typing import Any


def _present(value: Any) -> bool:
    return value not in (None, "", [], ())


def _visible(rows: Any) -> list[Any]:
    return [row for row in (rows or []) if not getattr(row, "is_deleted", False)]


def _quality(required: list[tuple[str, Any]]) -> dict[str, Any]:
    missing = [label for label, value in required if not _present(value)]
    return {
        "state": "Требуется заполнение" if missing else "Данные заполнены",
        "missing": missing,
    }


def partner_quality(partner: Any, contacts: list[Any] | None = None, contracts: list[Any] | None = None) -> dict[str, Any]:
    current_contacts = [contact for contact in _visible(contacts) if getattr(contact, "is_current", False)]
    return _quality(
        [
            ("не указан код партнера", getattr(partner, "partner_code", None)),
            ("не указано название партнера", getattr(partner, "partner_name", None)),
            ("не указан статус партнера", getattr(partner, "partner_type", None)),
            ("не указана периодичность сверки", getattr(partner, "reconciliation_frequency", None)),
            ("нет актуального контактного лица", current_contacts),
            ("нет договора или дополнительного соглашения", _visible(contracts)),
        ]
    )


def partner_attention(partner: Any, contacts: list[Any] | None = None, contracts: list[Any] | None = None) -> list[str]:
    warnings: list[str] = []
    contacts = _visible(contacts)
    contracts = _visible(contracts)
    current_contacts = [contact for contact in contacts if getattr(contact, "is_current", False)]
    pv_contacts = [contact for contact in current_contacts if getattr(contact, "is_pv_contact", False)]
    reconciliation_contacts = [
        contact
        for contact in current_contacts
        if getattr(contact, "is_reconciliation_recipient", False)
        or getattr(contact, "cc_reconciliation", False)
    ]
    inactive_reconciliation = [
        contact
        for contact in contacts
        if not getattr(contact, "is_current", False)
        and (
            getattr(contact, "is_reconciliation_recipient", False)
            or getattr(contact, "cc_reconciliation", False)
        )
    ]
    if not pv_contacts:
        warnings.append("У партнера нет актуального контакта по фармаконадзору.")
    if getattr(partner, "reconciliation_frequency", None) != "not_conducted" and not reconciliation_contacts:
        warnings.append("У партнера нет актуального контакта для сверки сообщений.")
    if inactive_reconciliation:
        warnings.append("Есть неактуальный контакт, который все еще указан для сверки.")
    if not contracts:
        warnings.append("У партнера пока нет договора или дополнительного соглашения.")
    elif not any(getattr(contract, "is_current", False) for contract in contracts):
        warnings.append("У партнера нет действующего договора.")
    return warnings


def partner_next_actions(partner: Any, contacts: list[Any] | None = None, contracts: list[Any] | None = None) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    warnings = partner_attention(partner, contacts, contracts)
    if any("договора" in warning for warning in warnings):
        actions.append({"label": "Добавить договор", "href": f"/contracts?partner_id={partner.id}"})
    if any("контакт" in warning.lower() for warning in warnings):
        actions.append({"label": "Добавить контакт", "href": f"/contract-contacts?partner_id={partner.id}"})
    if not _visible(getattr(partner, "products", [])):
        actions.append({"label": "Добавить препарат", "href": f"/products?partner_id={partner.id}"})
    if getattr(partner, "reconciliation_frequency", None) != "not_conducted":
        actions.append({"label": "Сформировать сверку", "href": f"/partner-reconciliation?partner_id={partner.id}"})
    return actions[:4]


def product_quality(product: Any) -> dict[str, Any]:
    substances = [
        link
        for link in _visible(getattr(product, "substance_links", []))
        if getattr(link, "substance", None) and not getattr(link.substance, "is_deleted", False)
    ]
    contracts = [
        link
        for link in _visible(getattr(product, "contract_links", []))
        if getattr(link, "contract", None) and not getattr(link.contract, "is_deleted", False)
    ]
    return _quality(
        [
            ("не указано торговое наименование", getattr(product, "product_name", None)),
            ("не выбран партнер", getattr(product, "mah_partner_id", None)),
            ("не указан договор", contracts),
            ("не указано действующее вещество", substances),
            ("не заполнена лекарственная форма", getattr(product, "dosage_form_id", None) or getattr(product, "dosage_form", None)),
            ("не указана дозировка", getattr(product, "strength", None)),
            ("не указан регистрационный номер", getattr(product, "authorization_number", None)),
        ]
    )


def product_attention(product: Any) -> list[str]:
    warnings: list[str] = []
    if not getattr(product, "mah_partner_id", None):
        warnings.append("У препарата не указан партнер.")
    if not [
        link
        for link in _visible(getattr(product, "contract_links", []))
        if getattr(link, "contract", None) and not getattr(link.contract, "is_deleted", False)
    ]:
        warnings.append("У препарата не указан договор или ДС.")
    if not (getattr(product, "dosage_form_id", None) or getattr(product, "dosage_form", None)):
        warnings.append("У препарата не указана лекарственная форма.")
    if not getattr(product, "strength", None):
        warnings.append("У препарата не указана дозировка.")
    return warnings


def product_next_actions(product: Any) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if not getattr(product, "mah_partner_id", None):
        actions.append({"label": "Выбрать партнера", "href": "/products"})
    if not getattr(product, "contract_links", None):
        actions.append({"label": "Привязать договор", "href": "/products"})
    actions.append({"label": "Создать сообщение", "href": f"/safety-reports?product_id={product.id}"})
    actions.append({"label": "Загрузить документ", "href": f"/documents?product_id={product.id}"})
    return actions[:4]


def contract_quality(contract: Any, tariffs: list[Any] | None = None) -> dict[str, Any]:
    return _quality(
        [
            ("не выбран партнер", getattr(contract, "partner_id", None)),
            ("не указан номер договора", getattr(contract, "contract_number", None)),
            ("не указана дата договора", getattr(contract, "contract_date", None)),
            ("не указан срок действия", getattr(contract, "valid_until", None)),
            ("не привязан препарат", getattr(contract, "linked_products", [])),
            ("нет структурированного перечня услуг", _visible(tariffs)),
        ]
    )


def contract_attention(contract: Any, tariffs: list[Any] | None = None) -> list[str]:
    warnings: list[str] = []
    today = date.today()
    valid_until = getattr(contract, "valid_until", None)
    if valid_until and today <= valid_until <= today + timedelta(days=60):
        warnings.append("Договор скоро истекает. Проверьте продление или замену.")
    if valid_until and valid_until < today and getattr(contract, "is_active", False):
        warnings.append("Договор завершен по сроку, но все еще отмечен активным.")
    if not getattr(contract, "linked_products", []):
        warnings.append("К договору пока не привязаны препараты.")
    tariffs = _visible(tariffs)
    if not tariffs:
        warnings.append("У договора нет структурированного перечня услуг.")
    elif any(not getattr(tariff, "price", None) for tariff in tariffs):
        warnings.append("У одной или нескольких услуг не указана цена.")
    if getattr(contract, "contract_type", None) == "additional_agreement" and not getattr(contract, "parent_contract_id", None):
        warnings.append("Дополнительное соглашение не привязано к базовому договору.")
    return warnings


def contract_next_actions(contract: Any, tariffs: list[Any] | None = None) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if not getattr(contract, "linked_products", []):
        actions.append({"label": "Привязать препарат", "href": f"/products?partner_id={getattr(contract, 'partner_id', '')}"})
    if not _visible(tariffs):
        actions.append({"label": "Добавить услугу", "href": f"/contracts?partner_id={getattr(contract, 'partner_id', '')}"})
    actions.append({"label": "Создать начисление", "href": f"/accounting-data?contract_id={contract.id}"})
    actions.append({"label": "Проверить срок договора", "href": f"/contracts?partner_id={getattr(contract, 'partner_id', '')}"})
    return actions[:4]


def status_class(value: Any) -> str:
    key = str(value or "").strip().lower().replace(" ", "-").replace("_", "-")
    positive = {"active", "fn", "authorized", "registration-active", "valid-icsr", "created-case", "closed", "completed", "approved", "paid"}
    warning = {"registration-in-progress", "in-triage", "new", "draft", "requires-review", "needs-review", "expiring", "due", "under-review"}
    danger = {"archive", "archived", "invalid", "not-valid-icsr", "overdue", "obsolete", "replaced", "cancelled"}
    if key in positive:
        return "pv-status-success"
    if key in warning:
        return "pv-status-warning"
    if key in danger:
        return "pv-status-danger"
    return "pv-status-neutral"


def related_count_label(count: int, label: str) -> dict[str, Any]:
    return {"count": count, "label": label}
