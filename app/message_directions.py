from __future__ import annotations

from typing import Literal


PARTNER_TO_OUR_COMPANY = "partner_to_our_company"
OUR_COMPANY_TO_PARTNER = "our_company_to_partner"
MESSAGE_DIRECTION_NEEDS_REVIEW = "needs_review"

MessageDirection = Literal[
    "partner_to_our_company",
    "our_company_to_partner",
    "needs_review",
]
ResolvedMessageDirection = Literal[
    "partner_to_our_company",
    "our_company_to_partner",
]

MESSAGE_DIRECTIONS = {
    PARTNER_TO_OUR_COMPANY,
    OUR_COMPANY_TO_PARTNER,
    MESSAGE_DIRECTION_NEEDS_REVIEW,
}
RESOLVED_MESSAGE_DIRECTIONS = {
    PARTNER_TO_OUR_COMPANY,
    OUR_COMPANY_TO_PARTNER,
}


def message_direction_from_legacy_source_type(source_type: str | None) -> MessageDirection:
    normalized = (source_type or "").strip().lower()
    if normalized == "partner":
        return PARTNER_TO_OUR_COMPANY
    if normalized == "our_company":
        return OUR_COMPANY_TO_PARTNER
    return MESSAGE_DIRECTION_NEEDS_REVIEW


def reconciliation_source_side(message_direction: str | None) -> str:
    if message_direction == PARTNER_TO_OUR_COMPANY:
        return "partner"
    if message_direction == OUR_COMPANY_TO_PARTNER:
        return "our_company"
    return MESSAGE_DIRECTION_NEEDS_REVIEW
