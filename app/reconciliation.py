from __future__ import annotations

from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.models import (
    Case,
    CaseProduct,
    Contract,
    ContractContact,
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

SIGN_OFF_STATUSES = ["draft", "sent", "confirmed", "closed"]


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


def products_for_partner(db: Session, partner_id: str) -> list[Product]:
    direct = (
        db.query(Product)
        .options(joinedload(Product.substance_links).joinedload(ProductSubstance.substance))
        .filter(Product.is_deleted.is_(False), Product.mah_partner_id == partner_id)
        .all()
    )
    contract_product_ids = [
        row[0]
        for row in db.query(Contract.product_id)
        .filter(Contract.is_deleted.is_(False), Contract.partner_id == partner_id)
        .all()
    ]
    contracted = []
    if contract_product_ids:
        contracted = (
            db.query(Product)
            .options(joinedload(Product.substance_links).joinedload(ProductSubstance.substance))
            .filter(Product.is_deleted.is_(False), Product.id.in_(contract_product_ids))
            .all()
        )
    by_id = {product.id: product for product in direct + contracted}
    return sorted(by_id.values(), key=lambda product: product.product_name)


def build_reconciliation_preview(
    db: Session,
    *,
    partner_id: str,
    period_start: date,
    period_end: date,
    contact_id: str | None = None,
    language: str = "ru",
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
    products = products_for_partner(db, partner_id)

    our_items = build_our_company_items(db, partner, period_start, period_end)
    partner_items = build_partner_items(db, partner, period_start, period_end)
    apply_matching(our_items, partner_items)
    all_items = our_items + partner_items

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
        "discrepancy_items": [
            item
            for item in all_items
            if item["reconciliation_status"] not in {"matched", "confirmed"}
        ],
        "summary": summary_for_items(our_items, partner_items),
    }


def build_our_company_items(
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
            joinedload(Case.case_products).joinedload(CaseProduct.product).joinedload(Product.substance_links).joinedload(ProductSubstance.substance),
            joinedload(Case.reactions),
            joinedload(Case.followups),
            joinedload(Case.submissions),
        )
        .filter(Case.is_deleted.is_(False))
        .all()
    )
    items: list[dict[str, Any]] = []
    for case in cases:
        if not case_belongs_to_partner(case, partner.id):
            continue

        if date_in_period(case.initial_received_date, period_start, period_end) or date_in_period(
            case.latest_received_date,
            period_start,
            period_end,
        ):
            items.append(case_to_item(case, partner, "initial"))

        for follow_up in case.followups:
            if date_in_period(follow_up.received_date, period_start, period_end):
                item = case_to_item(case, partner, "follow_up", follow_up=follow_up)
                item["reconciliation_status"] = "follow_up"
                item["match_confidence"] = 0.9
                item["match_method"] = "follow_up_by_case_id"
                items.append(item)

    return items


def build_partner_items(
    db: Session,
    partner: Partner,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
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
    for report in reports:
        if not date_in_period(report.received_date, period_start, period_end):
            continue
        item = safety_report_to_partner_item(report, partner)
        if report.triage_status == "invalid" or (
            report.triage_status in {"invalid", "non_safety"} and not report.is_valid_icsr
        ):
            item["reconciliation_status"] = "not_valid_icsr"
            item["match_method"] = "triage_status"
            item["match_confidence"] = 1.0
        items.append(item)
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

    return {
        "internal_case_id": case.id,
        "partner_case_id": case.worldwide_case_id,
        "product_id": product.id if product else None,
        "source_side": "our_company",
        "case_type": case_type,
        "receipt_date_our_company": received_date,
        "receipt_date_partner": None,
        "transfer_date_our_company": transfer_date,
        "transfer_date_partner": None,
        "adverse_event": reaction_text(case),
        "seriousness": case.seriousness or "",
        "reconciliation_status": "requires_review",
        "match_confidence": 0.0,
        "match_method": "",
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


def safety_report_to_partner_item(report: SafetyReport, partner: Partner) -> dict[str, Any]:
    case = report.case
    case_product = first_case_product(case) if case else None
    product = case_product.product if case_product else None
    return {
        "internal_case_id": case.id if case else None,
        "partner_case_id": report.safety_report_number,
        "product_id": product.id if product else None,
        "source_side": "partner",
        "case_type": "initial",
        "receipt_date_our_company": None,
        "receipt_date_partner": report.received_date,
        "transfer_date_our_company": None,
        "transfer_date_partner": report.received_date,
        "adverse_event": reaction_text(case) if case else "",
        "seriousness": case.seriousness if case else "",
        "reconciliation_status": "requires_review",
        "match_confidence": 0.0,
        "match_method": "",
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
) -> dict[str, int]:
    all_items = our_items + partner_items
    matched_count = sum(1 for item in all_items if item["reconciliation_status"] in {"matched", "confirmed"})
    discrepancy_count = sum(1 for item in all_items if item["reconciliation_status"] not in {"matched", "confirmed"})
    return {
        "our_case_count": len(our_items),
        "partner_case_count": len(partner_items),
        "matched_count": matched_count,
        "discrepancy_count": discrepancy_count,
    }
