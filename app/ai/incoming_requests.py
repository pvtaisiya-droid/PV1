import json
import re
from typing import Any


INCOMING_REQUEST_ANALYSIS_PROMPT = """
Extract a human-review draft from an incoming pharmacovigilance request.
Return JSON only and do not save automatically. Human confirmation is required.
"""


def analyze_incoming_request_mock(
    source_text: str,
    *,
    partners: list[Any],
    products: list[Any],
) -> dict[str, Any]:
    text = source_text.strip()
    lowered = text.lower()
    partner = first_named_match(lowered, partners, "partner_name")
    product = first_named_match(lowered, products, "product_name")
    possible_icsr = has_any(
        lowered,
        ["patient", "пациент", "adverse", "event", "reaction", "нежел", "побоч"],
    )
    serious = has_any(
        lowered,
        ["death", "fatal", "hospital", "life-threatening", "смерт", "госпитал"],
    )

    product_substance = ""
    if product:
        for link in getattr(product, "substance_links", []) or []:
            if getattr(link, "substance", None):
                product_substance = link.substance.substance_name
                break

    draft = {
        "request_type": "possible ICSR" if possible_icsr else "medical information / other",
        "partner": partner.partner_name if partner else "",
        "partner_id": partner.id if partner else "",
        "product": product.product_name if product else "",
        "product_id": product.id if product else "",
        "active_substance": product_substance,
        "possible_icsr": "yes" if possible_icsr else "no",
        "patient_information": extract_patient_sentence(text),
        "adverse_event": extract_event_sentence(text),
        "seriousness": "serious" if serious else "not specified",
        "seriousness_criteria": "Potential seriousness keyword found." if serious else "",
        "missing_information": build_missing_information(lowered, possible_icsr),
        "recommended_next_action": (
            "Review minimum ICSR criteria and request missing information."
            if possible_icsr
            else "Classify request and route to the responsible owner."
        ),
        "validity_assessment": (
            "Human review required before ICSR creation."
            if possible_icsr
            else "No valid ICSR conclusion can be made from mock analysis alone."
        ),
    }
    draft["gpt_json_output"] = json.dumps(draft, ensure_ascii=False, indent=2)
    return draft


def first_named_match(text: str, records: list[Any], attr_name: str) -> Any | None:
    for record in records:
        name = str(getattr(record, attr_name, "") or "").strip()
        if name and name.lower() in text:
            return record
    return None


def has_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def extract_patient_sentence(text: str) -> str:
    return first_sentence_matching(text, ["patient", "пациент"]) or ""


def extract_event_sentence(text: str) -> str:
    return (
        first_sentence_matching(
            text,
            ["adverse", "event", "reaction", "rash", "pain", "нежел", "побоч"],
        )
        or ""
    )


def first_sentence_matching(text: str, keywords: list[str]) -> str | None:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            return sentence[:500]
    return sentences[0][:500] if sentences else None


def build_missing_information(text: str, possible_icsr: bool) -> str:
    missing = []
    if possible_icsr:
        checks = {
            "Identifiable patient": ["patient", "пациент"],
            "Reporter/contact details": ["reporter", "email", "phone", "сообщ", "почт"],
            "Suspect product": ["product", "препарат"],
            "Adverse event": ["adverse", "event", "reaction", "нежел", "побоч"],
        }
        for label, keywords in checks.items():
            if not has_any(text, keywords):
                missing.append(label)
    if not missing:
        missing.append("Confirm extracted fields and source document context.")
    return "; ".join(missing)
