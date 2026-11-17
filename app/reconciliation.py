from __future__ import annotations

from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.message_directions import (
    MESSAGE_DIRECTION_NEEDS_REVIEW,
    reconciliation_source_side,
)
from app.models import (
    Case,
    CaseProduct,
    Contract,
    ContractContact,
    ContractProduct,
    FollowUp,
    Partner,
    Product,
    ProductSubstance,
    Reaction,
    SafetyReport,
    Submission,
)


RECONCILIATION_STATUSES = [
    "matched",
    "missing_in_our_database",
    "missing_in_partner_data",
    "duplicate",
    "follow_up",
    "not_valid_icsr",
    "requires_review",
    "confirmed",
]

ACTIVE_REGISTRATION_STATUSES = {"active", "registration_active", "authorized"}

SIGN_OFF_STATUSES = ["draft", "sent", "confirmed", "closed"]
DEFAULT_COMPANY_NAME = "ООО «АРС»"
DEFAULT_EMAIL_SUBJECT_TEMPLATES = {
    "ru": "Форма сверки сообщений по безопасности — [period_start_ru]–[period_end_ru]",
    "en": "Safety Messages Reconciliation Form — [period_start_en]–[period_end_en]",
}
DEFAULT_EMAIL_BODY_TEMPLATES = {
    "ru": """[greeting_ru]

Направляю форму сверки сообщений по безопасности за период с [period_start_ru] по [period_end_ru].

Просим проверить представленную информацию и подтвердить отсутствие расхождений либо направить комментарии по выявленным несоответствиям.

С уважением,
Таисия Воркунова
УЛФ
ООО «АРС»

E-mail: tvorkunova@pharmrussia.com""",
    "en": """[greeting_en]

Please find attached the Safety Messages Reconciliation Form for the period from [period_start_en] to [period_end_en].

Please review the information provided and confirm that there are no discrepancies or send us your comments regarding any discrepancies identified.

Kind regards,
Taisiya Vorkunova
QPPV
ARS, LLC

E-mail: tvorkunova@pharmrussia.com""",
}


class AmbiguousCurrentRegistrationError(ValueError):
    def __init__(self, product_name: str, registration_numbers: list[str]):
        self.product_name = product_name
        self.registration_numbers = sorted(set(registration_numbers))
        numbers = ", ".join(f"№ {number}" for number in self.registration_numbers)
        super().__init__(
            f"Для препарата «{product_name}» найдено несколько равнозначных действующих РУ: "
            f"{numbers}. Укажите одно актуальное РУ в разделе «Препараты», "
            "а остальным установите статус «Заменено»."
        )


def date_in_period(value: date | datetime | None, period_start: date, period_end: date) -> bool:
    if value is None:
        return False
    if isinstance(value, datetime):
        value = value.date()
    return period_start <= value <= period_end


def as_date(value: date | datetime | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value


def normalize(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def short_text(value: str | None, limit: int = 240) -> str:
    value = " ".join((value or "").split())
    return value[:limit]


def contact_full_name(contact: ContractContact | None) -> str | None:
    if not contact:
        return None
    parts = [contact.last_name, contact.first_name, contact.patronymic]
    return " ".join(part for part in parts if part)


def preferred_partner_contact(contacts: list[ContractContact]) -> ContractContact | None:
    active_contacts = [
        contact
        for contact in contacts
        if contact.is_current and getattr(contact, "is_active", True)
    ]
    if not active_contacts:
        return None
    return sorted(
        active_contacts,
        key=lambda contact: (
            not contact.is_reconciliation_recipient,
            not contact.is_primary,
            contact.last_name or "",
            contact.first_name or "",
        ),
    )[0]


def products_for_partner(db: Session, partner_id: str) -> list[Product]:
    today = date.today()
    direct = (
        db.query(Product)
        .options(
            joinedload(Product.registration_certificates),
            joinedload(Product.substance_links).joinedload(ProductSubstance.substance),
        )
        .filter(
            Product.is_deleted.is_(False),
            Product.is_active.is_(True),
            Product.mah_partner_id == partner_id,
        )
        .all()
    )
    contract_product_ids = {
        row[0]
        for row in db.query(Contract.product_id)
        .filter(
            Contract.is_deleted.is_(False),
            Contract.partner_id == partner_id,
            Contract.contract_date <= today,
            Contract.valid_until >= today,
        )
        .all()
        if row[0]
    }
    contract_product_ids.update(
        row[0]
        for row in db.query(ContractProduct.product_id)
        .join(Contract, Contract.id == ContractProduct.contract_id)
        .filter(
            Contract.is_deleted.is_(False),
            Contract.partner_id == partner_id,
            Contract.contract_date <= today,
            Contract.valid_until >= today,
            ContractProduct.is_deleted.is_(False),
        )
        .all()
        if row[0]
    )
    contracted = []
    if contract_product_ids:
        contracted = (
            db.query(Product)
            .options(
                joinedload(Product.registration_certificates),
                joinedload(Product.substance_links).joinedload(ProductSubstance.substance),
            )
            .filter(
                Product.is_deleted.is_(False),
                Product.is_active.is_(True),
                Product.id.in_(contract_product_ids),
            )
            .all()
        )
    by_id = {product.id: product for product in direct + contracted}
    current_products: list[Product] = []
    for product in by_id.values():
        registration = current_product_registration(product, on_date=today)
        if not registration:
            continue
        product.reconciliation_registration = registration
        current_products.append(product)
    grouped: dict[tuple[str, str, str], list[Product]] = {}
    for product in current_products:
        key = reconciliation_product_key(product)
        grouped.setdefault(key, []).append(product)

    deduplicated: dict[tuple[str, str, str], Product] = {}
    for key, candidates in grouped.items():
        best_priority = max(reconciliation_registration_priority(product) for product in candidates)
        best_candidates = [
            product
            for product in candidates
            if reconciliation_registration_priority(product) == best_priority
        ]
        registration_numbers = {
            product.reconciliation_registration.registration_number
            for product in best_candidates
        }
        if len(registration_numbers) > 1:
            raise AmbiguousCurrentRegistrationError(
                best_candidates[0].product_name,
                list(registration_numbers),
            )
        deduplicated[key] = max(best_candidates, key=reconciliation_product_rank)
    return sorted(deduplicated.values(), key=lambda product: product.product_name)


def current_product_registration(product: Product, *, on_date: date | None = None):
    reference_date = on_date or date.today()
    registrations = [
        registration
        for registration in (product.registration_certificates or [])
        if not registration.is_deleted
        and (registration.status or "").strip().lower() in ACTIVE_REGISTRATION_STATUSES
        and (not registration.registration_date or registration.registration_date <= reference_date)
        and (not registration.valid_until or registration.valid_until >= reference_date)
    ]
    if not registrations:
        return None
    best_priority = max(registration_priority(registration) for registration in registrations)
    best_registrations = [
        registration
        for registration in registrations
        if registration_priority(registration) == best_priority
    ]
    registration_numbers = {
        registration.registration_number
        for registration in best_registrations
    }
    if len(registration_numbers) > 1:
        raise AmbiguousCurrentRegistrationError(
            product.product_name,
            list(registration_numbers),
        )
    return max(
        best_registrations,
        key=lambda registration: getattr(registration, "created_at", None) or datetime.min,
    )


def registration_priority(registration) -> tuple[date, int]:
    registration_number = normalize(registration.registration_number).upper().replace(" ", "")
    modern_registration = int(
        registration_number.startswith("ЛП-")
        or "РГ-RU" in registration_number
    )
    return (
        registration.registration_date or date.min,
        modern_registration,
    )


def reconciliation_product_key(product: Product) -> tuple[str, str, str]:
    strength = normalize(getattr(product, "strength", None)).replace(",", ".").replace(" ", "")
    return (
        normalize(product.product_name),
        normalize(getattr(product, "dosage_form", None)),
        strength,
    )


def reconciliation_product_rank(product: Product) -> tuple[date, int, datetime]:
    registration = product.reconciliation_registration
    registration_date, modern_registration = registration_priority(registration)
    return (
        registration_date,
        modern_registration,
        getattr(product, "created_at", None) or datetime.min,
    )


def reconciliation_registration_priority(product: Product) -> tuple[date, int]:
    return registration_priority(product.reconciliation_registration)


def reconciliation_recipients(db: Session, partner_id: str) -> dict[str, list[ContractContact]]:
    contacts = (
        db.query(ContractContact)
        .filter(
            ContractContact.partner_id == partner_id,
            ContractContact.is_deleted.is_(False),
            ContractContact.is_active.is_(True),
            ContractContact.is_current.is_(True),
            ContractContact.email.is_not(None),
            ContractContact.email != "",
        )
        .order_by(ContractContact.is_primary.desc(), ContractContact.last_name, ContractContact.first_name)
        .all()
    )
    return {
        "to": [
            contact
            for contact in contacts
            if contact.is_reconciliation_recipient and contact.email
        ],
        "cc": [
            contact
            for contact in contacts
            if contact.cc_reconciliation and contact.email
        ],
    }


def contact_email_list(contacts: list[ContractContact]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for contact in contacts:
        email = (contact.email or "").strip()
        key = email.lower()
        if not email or key in seen:
            continue
        seen.add(key)
        result.append(email)
    return result


def email_list_text(emails: list[str]) -> str:
    return "; ".join(emails)


def render_reconciliation_email(
    reconciliation,
    *,
    current_user=None,
    company_name: str = DEFAULT_COMPANY_NAME,
) -> dict[str, str]:
    language = "en" if getattr(reconciliation, "language", "ru") == "en" else "ru"
    contact = getattr(reconciliation, "contact", None)
    contact_first_name = (getattr(contact, "first_name", None) or "").strip()
    if not contact_first_name:
        name_parts = (getattr(reconciliation, "contact_name", None) or "").split()
        contact_first_name = name_parts[1] if len(name_parts) > 1 else (name_parts[0] if name_parts else "")
    greeting_ru = f"Уважаемая {contact_first_name}," if contact_first_name else "Уважаемые коллеги,"
    greeting_en = f"Dear {contact_first_name}," if contact_first_name else "Dear Colleagues,"
    replacements = {
        "[period_start_ru]": format_email_date(reconciliation.period_start, "ru"),
        "[period_end_ru]": format_email_date(reconciliation.period_end, "ru"),
        "[period_start_en]": format_email_date(reconciliation.period_start, "en"),
        "[period_end_en]": format_email_date(reconciliation.period_end, "en"),
        "[greeting_ru]": greeting_ru,
        "[greeting_en]": greeting_en,
        "[document_filename]": reconciliation.document_filename or "",
        "[company_name]": company_name,
    }
    subject = reconciliation.email_subject or DEFAULT_EMAIL_SUBJECT_TEMPLATES[language]
    body = reconciliation.email_body or DEFAULT_EMAIL_BODY_TEMPLATES[language]
    for token, value in replacements.items():
        subject = subject.replace(token, value)
        body = body.replace(token, value)
    return {"subject": subject, "body": body}


def format_email_date(value: date | datetime | str, language: str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    elif isinstance(value, str):
        value = date.fromisoformat(value[:10])
    if language == "en":
        months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        return f"{value.day} {months[value.month - 1]} {value.year}"
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    return f"{value.day} {months[value.month - 1]} {value.year} года"


def reconciliation_preview_version(preview, period_start, period_end):
    """Fingerprint the unfiltered inputs displayed in the preview/document."""
    import hashlib
    import json
    from sqlalchemy import inspect
    from app.reconciliation_documents import product_row

    def columns(record):
        if record is None:
            return None
        return {column.key: getattr(record, column.key)
                for column in inspect(type(record)).columns}

    products = []
    for product in preview["products"]:
        products.append({
            "product": columns(product),
            "registration": columns(product.reconciliation_registration),
            "document_rows": [product_row(product, "ru"), product_row(product, "en")],
            "substances": sorted(
                [{"link": columns(link), "substance": columns(link.substance)}
                 for link in product.substance_links],
                key=lambda row: str(row["link"]["id"])),
        })
    data = {
        "period": [period_start, period_end],
        "partner": columns(preview["partner"]),
        "contact": columns(preview["contact"]),
        "contacts": sorted([columns(row) for row in preview["contacts"]],
                           key=lambda row: str(row["id"])),
        "products": sorted(products, key=lambda row: str(row["product"]["id"])),
        "items": sorted(preview["items"],
                        key=lambda row: json.dumps(row, sort_keys=True, default=str)),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str,
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def build_reconciliation_preview(
    db: Session,
    *,
    partner_id: str,
    period_start: date,
    period_end: date,
    contact_id: str | None = None,
    language: str = "ru",
    product_ids: list[str] | None = None,
) -> dict[str, Any]:
    partner = db.query(Partner).filter(Partner.id == partner_id, Partner.is_deleted.is_(False)).first()
    if not partner:
        raise ValueError("Partner not found")

    contact = None
    if contact_id:
        contact = (
            db.query(ContractContact)
            .filter(
                ContractContact.id == contact_id,
                ContractContact.partner_id == partner_id,
                ContractContact.is_deleted.is_(False),
                ContractContact.is_active.is_(True),
                ContractContact.is_current.is_(True),
            )
            .first()
        )

    contacts = (
        db.query(ContractContact)
        .filter(
            ContractContact.partner_id == partner_id,
            ContractContact.is_deleted.is_(False),
        )
        .order_by(ContractContact.is_current.desc(), ContractContact.last_name)
        .all()
    )
    if not contact:
        contact = preferred_partner_contact(contacts)
    products = products_for_partner(db, partner_id)

    all_items = build_reconciliation_message_items(db, partner, period_start, period_end)
    product_id_set = {product_id for product_id in (product_ids or []) if product_id}
    if product_id_set:
        all_items = filter_items_by_products(all_items, product_id_set)
    our_items = [item for item in all_items if item["source_side"] == "our_company"]
    partner_items = [item for item in all_items if item["source_side"] == "partner"]
    direction_review_items = [
        item for item in all_items if item["source_side"] == MESSAGE_DIRECTION_NEEDS_REVIEW
    ]

    return {
        "partner": partner,
        "contact": contact,
        "contacts": contacts,
        "products": products,
        "period_start": period_start,
        "period_end": period_end,
        "language": language,
        "items": all_items,
        "our_items": our_items,
        "partner_items": partner_items,
        "direction_review_items": direction_review_items,
        "discrepancy_items": [
            item
            for item in all_items
            if item["reconciliation_status"] not in {"matched", "confirmed"}
        ],
        "summary": summary_for_items(our_items, partner_items, direction_review_items),
    }


def filter_items_by_products(
    items: list[dict[str, Any]],
    product_ids: set[str],
) -> list[dict[str, Any]]:
    return [item for item in items if item.get("product_id") in product_ids]


def build_our_company_items(
    db: Session,
    partner: Partner,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    return [
        item
        for item in build_reconciliation_message_items(db, partner, period_start, period_end)
        if item["source_side"] == "our_company"
    ]


def build_partner_items(
    db: Session,
    partner: Partner,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    return [
        item
        for item in build_reconciliation_message_items(db, partner, period_start, period_end)
        if item["source_side"] == "partner"
    ]


def build_reconciliation_message_items(
    db: Session,
    partner: Partner,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    cases = (
        db.query(Case)
        .options(
            joinedload(Case.partner),
            joinedload(Case.safety_report),
            joinedload(Case.patients),
            joinedload(Case.case_products)
            .joinedload(CaseProduct.product)
            .joinedload(Product.substance_links)
            .joinedload(ProductSubstance.substance),
            joinedload(Case.reactions),
            joinedload(Case.followups),
            joinedload(Case.submissions),
        )
        .filter(Case.is_deleted.is_(False))
        .all()
    )
    reports = (
        db.query(SafetyReport)
        .options(
            joinedload(SafetyReport.case).joinedload(Case.case_products).joinedload(CaseProduct.product).joinedload(Product.substance_links).joinedload(ProductSubstance.substance),
            joinedload(SafetyReport.case).joinedload(Case.patients),
            joinedload(SafetyReport.case).joinedload(Case.reactions),
            joinedload(SafetyReport.case).joinedload(Case.followups),
        )
        .filter(
            SafetyReport.is_deleted.is_(False),
            SafetyReport.partner_id == partner.id,
        )
        .all()
    )
    items: list[dict[str, Any]] = []
    represented_report_ids: set[str] = set()

    for case in cases:
        if not case_belongs_to_partner(case, partner.id):
            continue

        report = case.safety_report
        if report and not report.is_deleted:
            represented_report_ids.add(report.id)
            if date_in_period(report.received_date, period_start, period_end):
                items.append(safety_report_to_item(report, partner))
        elif date_in_period(case.initial_received_date, period_start, period_end):
            items.append(
                case_to_item(
                    case,
                    partner,
                    "initial",
                    message_direction=MESSAGE_DIRECTION_NEEDS_REVIEW,
                )
            )

        for follow_up in case.followups:
            if follow_up.is_deleted or not date_in_period(
                follow_up.received_date,
                period_start,
                period_end,
            ):
                continue
            item = case_to_item(
                case,
                partner,
                "follow_up",
                follow_up=follow_up,
                message_direction=follow_up.message_direction,
            )
            if follow_up.message_direction == MESSAGE_DIRECTION_NEEDS_REVIEW:
                item["reconciliation_status"] = "requires_review"
                item["match_confidence"] = 0.0
                item["match_method"] = "message_direction_needs_review"
            else:
                item["reconciliation_status"] = "follow_up"
                item["match_confidence"] = 1.0
                item["match_method"] = "follow_up_message_direction"
            items.append(item)

    for report in reports:
        if report.id in represented_report_ids or not date_in_period(
            report.received_date,
            period_start,
            period_end,
        ):
            continue
        items.append(safety_report_to_item(report, partner))
    return items


def case_belongs_to_partner(case: Case, partner_id: str) -> bool:
    if case.partner_id == partner_id:
        return True
    if case.safety_report and case.safety_report.partner_id == partner_id:
        return True
    if any(
        case_product.product and case_product.product.mah_partner_id == partner_id
        for case_product in case.case_products
    ):
        return True
    return any(submission.recipient_partner_id == partner_id for submission in case.submissions)


def case_to_item(
    case: Case,
    partner: Partner,
    case_type: str,
    *,
    follow_up: FollowUp | None = None,
    message_direction: str = MESSAGE_DIRECTION_NEEDS_REVIEW,
) -> dict[str, Any]:
    case_product = first_case_product(case)
    product = case_product.product if case_product else None
    received_date = follow_up.received_date if follow_up else (
        case.initial_received_date or case.latest_received_date
    )
    transfer_date = first_submission_date(case, partner.id)
    case_number = case.case_number
    if follow_up:
        case_number = f"{case.case_number}-FU{follow_up.follow_up_number}"
    source_side = reconciliation_source_side(message_direction)
    resolved_direction = source_side != MESSAGE_DIRECTION_NEEDS_REVIEW

    return {
        "internal_case_id": case.id,
        "partner_case_id": case.worldwide_case_id,
        "product_id": product.id if product else None,
        "source_side": source_side,
        "case_type": case_type,
        "receipt_date_our_company": received_date if source_side != "partner" else None,
        "receipt_date_partner": received_date if source_side != "our_company" else None,
        "transfer_date_our_company": transfer_date if source_side == "our_company" else None,
        "transfer_date_partner": received_date if source_side == "partner" else None,
        "adverse_event": reaction_text(case),
        "seriousness": case.seriousness or "",
        "reconciliation_status": "matched" if resolved_direction else "requires_review",
        "match_confidence": 1.0 if resolved_direction else 0.0,
        "match_method": "message_direction" if resolved_direction else "message_direction_needs_review",
        "reviewer_comment": "",
        "confirmed_by_user": "",
        "internal_case_number": case_number,
        "partner_case_number": case.worldwide_case_id or "",
        "partner_name": partner.partner_name,
        "product_name": product_name(case_product, product),
        "active_substance": active_substance_text(case_product, product),
        "patient": patient_text(case),
        "country": case.country_of_occurrence or "",
        "source_type": case.report_type or case.case_type or "",
        "short_description": short_text(follow_up.description if follow_up else case.narrative),
        "linked_item_id": None,
    }


def safety_report_to_item(report: SafetyReport, partner: Partner) -> dict[str, Any]:
    case = report.case
    case_product = first_case_product(case) if case else None
    product = case_product.product if case_product else None
    source_side = reconciliation_source_side(report.message_direction)
    resolved_direction = source_side != MESSAGE_DIRECTION_NEEDS_REVIEW
    item = {
        "internal_case_id": case.id if case else None,
        "partner_case_id": report.safety_report_number,
        "product_id": product.id if product else None,
        "source_side": source_side,
        "case_type": "initial",
        "receipt_date_our_company": report.received_date if source_side != "partner" else None,
        "receipt_date_partner": report.received_date if source_side != "our_company" else None,
        "transfer_date_our_company": (
            first_submission_date(case, partner.id)
            if case and source_side == "our_company"
            else None
        ),
        "transfer_date_partner": report.received_date if source_side == "partner" else None,
        "adverse_event": reaction_text(case) if case else "",
        "seriousness": case.seriousness if case else "",
        "reconciliation_status": "matched" if resolved_direction else "requires_review",
        "match_confidence": 1.0 if resolved_direction else 0.0,
        "match_method": "message_direction" if resolved_direction else "message_direction_needs_review",
        "reviewer_comment": "",
        "confirmed_by_user": "",
        "internal_case_number": case.case_number if case else "",
        "partner_case_number": report.safety_report_number,
        "partner_name": partner.partner_name,
        "product_name": product_name(case_product, product),
        "active_substance": active_substance_text(case_product, product),
        "patient": patient_text(case) if case else report.reporter_name or "",
        "country": (case.country_of_occurrence if case else report.reporter_country_code) or "",
        "source_type": report.source_type or "partner",
        "short_description": short_text(report.raw_text or report.raw_subject),
        "linked_item_id": None,
    }
    if report.triage_status in {"invalid", "non_safety"} and not report.is_valid_icsr:
        item["reconciliation_status"] = "not_valid_icsr"
        item["match_method"] = "triage_status"
        item["match_confidence"] = 1.0
    return item


def safety_report_to_partner_item(report: SafetyReport, partner: Partner) -> dict[str, Any]:
    """Backward-compatible alias for callers that used the old directional name."""
    return safety_report_to_item(report, partner)


def first_case_product(case: Case | None) -> CaseProduct | None:
    if not case or not case.case_products:
        return None
    return case.case_products[0]


def first_submission_date(case: Case, partner_id: str) -> date | None:
    submissions = [
        submission
        for submission in case.submissions
        if submission.recipient_partner_id == partner_id
    ]
    if not submissions:
        return None
    submission = sorted(submissions, key=lambda row: row.due_date or date.max)[0]
    return as_date(submission.submitted_at) or submission.due_date


def product_name(case_product: CaseProduct | None, product: Product | None) -> str:
    return (
        (case_product.reported_product_name if case_product else None)
        or (product.product_name if product else None)
        or ""
    )


def active_substance_text(case_product: CaseProduct | None, product: Product | None) -> str:
    if case_product and case_product.active_substance_text:
        return case_product.active_substance_text
    if product and product.substance_links:
        return ", ".join(
            link.substance.substance_name
            for link in product.substance_links
            if link.substance
        )
    return ""


def patient_text(case: Case | None) -> str:
    if not case or not case.patients:
        return ""
    patient = case.patients[0]
    if patient.patient_initials:
        return patient.patient_initials
    parts = []
    if patient.sex:
        parts.append(patient.sex)
    if patient.age_value:
        parts.append(f"{patient.age_value:g} {patient.age_unit or ''}".strip())
    return ", ".join(parts)


def reaction_text(case: Case | None) -> str:
    if not case:
        return ""
    return "; ".join(reaction.reported_term for reaction in case.reactions if reaction.reported_term)


def apply_matching(our_items: list[dict[str, Any]], partner_items: list[dict[str, Any]]) -> None:
    matched_partner_ids: set[int] = set()

    for our_item in our_items:
        match_index, method, confidence = find_best_match(our_item, partner_items, matched_partner_ids)
        if match_index is None:
            if our_item["reconciliation_status"] != "follow_up":
                our_item["reconciliation_status"] = "missing_in_partner_data"
                our_item["match_method"] = "no_partner_match"
                our_item["match_confidence"] = 0.0
            continue

        partner_item = partner_items[match_index]
        matched_partner_ids.add(match_index)
        set_matched(our_item, partner_item, method, confidence)

    for index, partner_item in enumerate(partner_items):
        if index in matched_partner_ids:
            continue
        if partner_item["reconciliation_status"] in {"not_valid_icsr"}:
            continue
        partner_item["reconciliation_status"] = "missing_in_our_database"
        partner_item["match_method"] = "no_internal_match"
        partner_item["match_confidence"] = 0.0

    mark_possible_duplicates(our_items + partner_items)


def find_best_match(
    our_item: dict[str, Any],
    partner_items: list[dict[str, Any]],
    matched_partner_ids: set[int],
) -> tuple[int | None, str, float]:
    for index, partner_item in enumerate(partner_items):
        if index in matched_partner_ids:
            continue
        if partner_item.get("internal_case_id") and partner_item.get("internal_case_id") == our_item.get("internal_case_id"):
            return index, "exact_case_id", 1.0
        if normalize(partner_item.get("partner_case_number")) and normalize(
            partner_item.get("partner_case_number")
        ) in {
            normalize(our_item.get("internal_case_number")),
            normalize(our_item.get("partner_case_number")),
        }:
            return index, "partner_case_number", 0.95

    for index, partner_item in enumerate(partner_items):
        if index in matched_partner_ids:
            continue
        if composite_match(our_item, partner_item):
            return index, "product_date_patient_event", 0.82

    return None, "", 0.0


def composite_match(our_item: dict[str, Any], partner_item: dict[str, Any]) -> bool:
    same_product = normalize(our_item.get("product_name")) and normalize(
        our_item.get("product_name")
    ) == normalize(partner_item.get("product_name"))
    same_patient = normalize(our_item.get("patient")) and normalize(
        our_item.get("patient")
    ) == normalize(partner_item.get("patient"))
    same_event = normalize(our_item.get("adverse_event")) and normalize(
        our_item.get("adverse_event")
    ) == normalize(partner_item.get("adverse_event"))
    our_date = our_item.get("receipt_date_our_company")
    partner_date = partner_item.get("receipt_date_partner")
    return bool(same_product and same_patient and same_event and our_date == partner_date)


def set_matched(
    our_item: dict[str, Any],
    partner_item: dict[str, Any],
    method: str,
    confidence: float,
) -> None:
    for item in (our_item, partner_item):
        item["reconciliation_status"] = "matched"
        item["match_method"] = method
        item["match_confidence"] = confidence


def mark_possible_duplicates(items: list[dict[str, Any]]) -> None:
    for index, item in enumerate(items):
        if item["reconciliation_status"] in {"matched", "confirmed", "follow_up"}:
            continue
        for other in items[index + 1 :]:
            if other["reconciliation_status"] in {"matched", "confirmed", "follow_up"}:
                continue
            if not item.get("short_description") or not other.get("short_description"):
                continue
            similarity = SequenceMatcher(
                None,
                normalize(item["short_description"]),
                normalize(other["short_description"]),
            ).ratio()
            if similarity >= 0.84:
                item["reconciliation_status"] = "duplicate"
                item["match_method"] = "similar_description"
                item["match_confidence"] = round(similarity, 2)
                item["linked_item_id"] = other.get("id")
                break


def summary_for_items(
    our_items: list[dict[str, Any]],
    partner_items: list[dict[str, Any]],
    direction_review_items: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    direction_review_items = direction_review_items or []
    all_items = our_items + partner_items + direction_review_items
    matched_count = sum(1 for item in all_items if item["reconciliation_status"] in {"matched", "confirmed"})
    discrepancy_count = sum(1 for item in all_items if item["reconciliation_status"] not in {"matched", "confirmed"})
    return {
        "our_case_count": len(our_items),
        "partner_case_count": len(partner_items),
        "matched_count": matched_count,
        "discrepancy_count": discrepancy_count,
        "direction_review_count": len(direction_review_items),
    }
