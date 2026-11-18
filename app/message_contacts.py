"""Resolve message roles without modifying a selected directory contact."""
from app.duplicates import DuplicateContactError, blocked_contact_candidates, contact_candidates
from app.models import ContractContact, Partner


def prepare_message_contacts(db, data, *, save_sender=False, allow_duplicate=False, existing_report=None):
    if data.get("reporter_same_as_sender") and data.get("reporter_unknown"):
        raise ValueError("Выберите один вариант сведений о первичном репортёре.")
    partner_id = data.get("partner_id")
    if partner_id:
        partner = db.get(Partner, partner_id)
        if not partner or partner.is_deleted:
            raise ValueError("Выбранный партнёр не найден.")
    for role in ("sender", "reporter"):
        if role == "reporter" and (data.get("reporter_same_as_sender") or data.get("reporter_unknown")):
            continue
        contact_id = data.get(role + "_contact_id")
        if contact_id:
            contact = db.get(ContractContact, contact_id)
            unchanged_reference = existing_report is not None and getattr(existing_report, role + "_contact_id") == contact_id
            if not contact or (contact.is_deleted and not unchanged_reference):
                raise ValueError("Выбранный контакт не найден. Обновите список контактных лиц.")
            values = {"name": contact.known_name, "email": contact.email,
                      "phone": contact.mobile_phone or contact.work_phone}
            for key, value in values.items():
                if not unchanged_reference and not data.get(role + "_" + key):
                    data[role + "_" + key] = value
    if save_sender and not data.get("sender_contact_id"):
        if not any(data.get("sender_" + key) for key in ("name", "email", "phone")):
            raise ValueError("Для сохранения контакта укажите известное имя, email или телефон.")
        matches = contact_candidates(db, name=data.get("sender_name"), email=data.get("sender_email"),
                                     phones=[data.get("sender_phone")], partner_id=partner_id)
        blocked = blocked_contact_candidates(matches, allowed=allow_duplicate, partner_id=partner_id, email=data.get("sender_email"))
        if blocked:
            raise DuplicateContactError(blocked)
        contact = ContractContact(
            display_name=data.get("sender_name"), email=data.get("sender_email"),
            mobile_phone=data.get("sender_phone"), partner_id=partner_id,
            contact_type="other", is_pv_contact=False, is_reconciliation_recipient=False,
            cc_reconciliation=False, is_primary=False, is_billing_contact=False,
        )
        db.add(contact)
        db.flush()
        from app.crud import audit_user_id, log_create_event
        log_create_event(db, entity_type="ContractContact", entity_id=contact.id,
                         data={"display_name": contact.display_name, "email": contact.email,
                               "mobile_phone": contact.mobile_phone, "partner_id": partner_id})
        if matches:
            from app.audit import log_audit
            log_audit(db, entity_type="ContractContact", entity_id=contact.id, action="duplicate_review",
                      new_value=", ".join(item["contact"].id for item in matches),
                      comment="Пользователь подтвердил создание отдельного контакта после проверки совпадений.", user_id=audit_user_id(db))
        data["sender_contact_id"] = contact.id
    if data.get("reporter_same_as_sender"):
        data["reporter_contact_id"] = data.get("sender_contact_id")
        for key in ("name", "email", "phone"):
            data["reporter_" + key] = data.get("sender_" + key)
        data["reporter_unknown"] = False
    elif data.get("reporter_unknown"):
        data["reporter_contact_id"] = None
        data["reporter_country_code"] = None
        for key in ("name", "email", "phone"):
            data["reporter_" + key] = None
    return data
