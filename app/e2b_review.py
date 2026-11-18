"""Presentation and unsaved intake values for the package review wizard."""
import json
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from app import ae_card
from app.config import get_settings
from app.e2b_import import E2BMatchingService, E2BPackageService, date_field, parsed_case_flags


def review_flags(parsed):
    # An unresolved date conflict is None in the assembled draft, while each
    # individual source always has a date object.
    patient = dict(parsed.get("patient") or {})
    unresolved_death = "death_date" in patient and patient["death_date"] is None
    patient["death_date"] = patient.get("death_date") or {}
    flags = parsed_case_flags({**parsed, "patient": patient})
    if unresolved_death:
        flags["has_death"] = None
        if not flags["is_serious"]:
            flags["is_serious"] = None
    return flags


def document_review(batch, group, groups, selected_ids=None):
    """Keep the full attachment list and explain the suggested case allocation."""
    selected = set(group["attachment_ids"] if selected_ids is None else selected_ids)
    documents = []
    for item in batch.items:
        if item.file_role == "xml":
            continue
        owners = [row for row in groups if item.id in row["attachment_ids"]]
        assignment = "current" if item.id in group["attachment_ids"] else "other" if owners else "unassigned"
        documents.append({"id": item.id, "path": item.original_path,
            "selected": item.id in selected, "assignment": assignment,
            "case_labels": [row.get("worldwide_case_id") or row.get("local_sender_case_id") or row["case_key"] for row in owners]})
    documents.sort(key=lambda row: ({"current": 0, "unassigned": 1, "other": 2}[row["assignment"]], row["path"].casefold()))
    return documents


def enrich_groups(db, batch, preview):
    for group in preview["case_groups"]:
        parsed = group.get("merged") or {}
        group["flags"] = review_flags(parsed)
        group["products"] = [dict(product, match=match) for product, match in
                             zip(parsed.get("products", []), group["product_matches"])]
        group["products"].sort(key=lambda p: (p.get("drug_role") == "concomitant", not p["match"].get("is_company_product")))
        group["files"] = [file for file in preview["files"] if file["id"] in group["attachment_ids"]]
        group["review_documents"] = document_review(batch, group, preview["case_groups"])
        group["issues"] = [issue for issue in batch.issues if (issue.item_id in group["item_ids"] or issue.item_id is None)
                           and not (group["has_company_product"] and issue.issue_type == "not_pv_ars_candidate")]
        message = parsed.get("message") or {}
        version = str(group.get("clinical_version") or "")
        group["followup"] = (message.get("followup_type") or "").casefold() in {"follow_up", "follow-up", "followup"} or (version.isdecimal() and int(version) > 1)
    current_summary = E2BPackageService().product_summary([{"normalized": {"products": group["products"]}}
                                                        for group in preview["case_groups"]])
    preview["product_summary"].update(current_summary)
    preview["has_company_product"] = current_summary["has_company_product"]
    preview["pv_scope_status"] = current_summary["status"]


def draft_values(db, batch, group):
    """Never create a report or case just to display a form."""
    parsed = group.get("merged") or {}
    message = parsed.get("message") or {}
    patient = parsed.get("patient") or {}
    reporter = parsed.get("reporter") or {}
    narrative = (parsed.get("narrative") or {}).get("medical_narrative") or batch.source_filename
    received = date_field(message.get("company_received_date")) or date_field(message.get("first_received_date"))
    received = received or datetime.now(ZoneInfo(get_settings().business_timezone)).date()
    state = ae_card.empty_state()
    age = patient.get("age_value")
    units = {"years": "лет", "months": "мес.", "weeks": "нед.", "days": "дн."}
    state["patient"].update(initials=patient.get("initials") or patient.get("identifier") or "",
        age=f"{age:g} {units.get(patient.get('age_unit'), patient.get('age_unit') or '')}".strip() if isinstance(age, (int, float)) else "",
        sex=patient.get("sex") if patient.get("sex") in {"male", "female"} else "")
    state["ae"] = bool(parsed.get("reactions"))
    state["description"] = (parsed.get("narrative") or {}).get("medical_narrative") or ""
    state["serious"] = "yes" if review_flags(parsed)["is_serious"] else "no"
    state["criteria"] = [label for label, field in zip(ae_card.CRITERIA, ae_card.REACTION_FIELDS)
                         if any(row.get(field) for row in parsed.get("reactions", []))]
    matches = [(product, E2BPackageService().match_product_scope(db, product)) for product in parsed.get("products", [])]
    state["medications"] = [dict(id=str(uuid4()), companyId=match.get("product_id") or "",
        reported=product.get("reported_product_name") or "", caseProductId="") for product, match in matches] or state["medications"]
    if any(reporter.get(field) for field in ("name", "email", "phone")):
        identifier = str(uuid4())
        state["people"].append(dict(id=identifier, name=reporter.get("name") or "", email=reporter.get("email") or "",
            phone=reporter.get("phone") or "", country=reporter.get("country") or "", saved=False))
        state["contacts"]["primary"].update(mode="new", personId=identifier)
    partner_id, _ = E2BMatchingService().find_partner(db, parsed)
    return dict(received_date=received.isoformat(), message_direction="needs_review", source_type="e2b_zip",
        raw_subject=f"E2B import {message.get('message_identifier') or batch.source_filename}", raw_text=narrative,
        partner_id=partner_id or "", routes=["pv"], product_ids=list(dict.fromkeys(match["product_id"] for _, match in matches if match.get("product_id"))),
        reporter_name=reporter.get("name") or "", reporter_email=reporter.get("email") or "", reporter_phone=reporter.get("phone") or "",
        reporter_country_code=reporter.get("country") or message.get("sender_country") or "", pv_card_json=json.dumps(state, ensure_ascii=False))
