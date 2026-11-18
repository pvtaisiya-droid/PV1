"""Conservative duplicate candidates; identifying data are never logged here."""
import hashlib
import re
import unicodedata

from app.models import Attachment, ContractContact, SafetyReport


def normalized_text(value):
    if value is not None and not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def normalized_email(value):
    return normalized_text(value)


def normalized_phone(value):
    if value is not None and not isinstance(value, str):
        return ""
    main = re.split(r"(?:доб\.?|ext\.?|#)", (value or "").casefold(), maxsplit=1)[0]
    digits = "".join(char for char in main if char.isdecimal())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits if len(digits) >= 7 else ""


def message_fingerprint(text):
    value = normalized_text(text)
    return hashlib.sha256(value.encode()).hexdigest() if value else None


def contact_candidates(db, *, name=None, email=None, phones=(), partner_id=None, exclude_id=None):
    email_key = normalized_email(email)
    phone_keys = {normalized_phone(value) for value in phones} - {""}
    name_key = normalized_text(name).replace("ё", "е")
    if not (email_key or phone_keys or name_key):
        return []
    candidates = []
    for contact in db.query(ContractContact).filter(ContractContact.is_deleted.is_(False)).all():
        if contact.id == exclude_id:
            continue
        reasons = []
        old_email = normalized_email(contact.email)
        old_phones = {normalized_phone(contact.mobile_phone), normalized_phone(contact.work_phone)} - {""}
        if email_key and old_email == email_key:
            reasons.append("email")
        if phone_keys & old_phones:
            reasons.append("phone")
        conflicting = (email_key and old_email and email_key != old_email) or (phone_keys and old_phones and not phone_keys & old_phones)
        if name_key and not conflicting and contact.partner_id == partner_id and normalized_text(contact.full_name).replace("ё", "е") == name_key:
            reasons.append("name_partner")
        if reasons:
            candidates.append({"contact": contact, "reasons": reasons})
    return candidates


def blocked_contact_candidates(candidates, *, allowed=False, partner_id=None, email=None):
    if not allowed:
        return candidates
    # Existing installations enforce one email per partner. A review override
    # can distinguish people sharing a phone, but cannot violate that key.
    key = normalized_email(email)
    return [item for item in candidates if partner_id and key and item["contact"].partner_id == partner_id
            and normalized_email(item["contact"].email) == key]


def message_candidates(db, *, text=None, received_date=None, sender_email=None, sender_phone=None,
                       checksums=(), exclude_id=None):
    fingerprint = message_fingerprint(text)
    matched = {}
    if fingerprint:
        for report in db.query(SafetyReport).filter(SafetyReport.is_deleted.is_(False),
                                                   SafetyReport.content_fingerprint == fingerprint).all():
            if report.id != exclude_id:
                matched[report.id] = {"report": report, "reasons": ["content"]}
    hashes = {value for value in checksums if value}
    if hashes:
        for report in db.query(SafetyReport).filter(SafetyReport.is_deleted.is_(False), SafetyReport.source_checksum_sha256.in_(hashes)).all():
            if report.id != exclude_id:
                entry = matched.setdefault(report.id, {"report": report, "reasons": []})
                if "file" not in entry["reasons"]:
                    entry["reasons"].append("file")
        for attachment in db.query(Attachment).filter(Attachment.is_deleted.is_(False),
                                                       Attachment.checksum_sha256.in_(hashes),
                                                       Attachment.safety_report_id.is_not(None)).all():
            report = db.get(SafetyReport, attachment.safety_report_id)
            if report and not report.is_deleted and report.id != exclude_id:
                entry = matched.setdefault(report.id, {"report": report, "reasons": []})
                if "file" not in entry["reasons"]:
                    entry["reasons"].append("file")
    for entry in matched.values():
        report = entry["report"]
        if received_date and received_date == report.received_date:
            entry["reasons"].append("date")
        if ((normalized_email(sender_email) and normalized_email(sender_email) == normalized_email(report.sender_email)) or
            (normalized_phone(sender_phone) and normalized_phone(sender_phone) == normalized_phone(report.sender_phone))):
            entry["reasons"].append("sender")
    return list(matched.values())


class DuplicateContactError(ValueError):
    def __init__(self, candidates):
        self.candidates = candidates
        super().__init__("Найден похожий контакт. Выберите существующую запись; повторное сохранение в справочник не требуется.")


class DuplicateMessageError(ValueError):
    def __init__(self, candidates):
        self.candidates = candidates
        super().__init__("Найдены возможные дубли сообщения. Проверьте их и подтвердите создание отдельного входящего сообщения.")
