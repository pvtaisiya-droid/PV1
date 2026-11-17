"""Narrow incoming R3 identity/date interpretation, not a regional validator.

ICH IG 5.03 and Appendix I(G) 1.03 (18 July 2025), package 1.11.
I(G) PDF pp. 7–9, 11: N.1.2, N.2.r.1–3, C.1.1, C.1.4/5, C.1.8.1.
Source URLs and synthetic examples: tests/fixtures/e2b_r3_identity/README.md.
"""
import json
import re
from datetime import date, datetime
from xml.etree import ElementTree as ET

PROFILE = "ich-r3-identity-dates-v1"
NS = "urn:hl7-org:v3"
H = {"h": NS}
OID = "2.16.840.1.113883.3.989.2.1.3."
INV = "h:controlActProcess/h:subject/h:investigationEvent"


def source_date(nodes, path, element):
    """Preserve a received TS, including unsupported values, without inventing parts."""
    result = dict(element=element, path=path, namespace=NS, origin="source_xml",
                  raw=None, value=None, parsed=None, precision=None, timezone=None,
                  null_flavor=None, state="missing",
                  candidates=[dict(n.attrib) for n in nodes])
    if not nodes:
        return result
    if len(nodes) != 1:
        result["state"] = "ambiguous"
        return result
    attrs = nodes[0].attrib
    raw, nf = attrs.get("value"), attrs.get("nullFlavor")
    result.update(raw=raw, null_flavor=nf)
    if nf is not None:
        result["state"] = "invalid" if raw is not None or not nf else "null_flavor"
        return result  # storage does not certify the nullFlavor as permitted
    result["state"] = "invalid"
    if raw is None:
        return result
    match = re.fullmatch(r"(\d{4}(?:\d{2}){0,5})(\.\d+)?([+-]\d{4})?", raw)
    if not match:
        return result
    digits, fraction, zone = match.groups()
    length = len(digits)
    result["timezone"] = zone
    if (fraction and length != 14) or (zone and length < 10):
        return result
    try:
        year = int(digits[:4])
        if not 1 <= year <= 9999:
            return result
        if length >= 6 and not 1 <= int(digits[4:6]) <= 12:
            return result
        if length >= 8:
            day = date(year, int(digits[4:6]), int(digits[6:8]))
        if length >= 10 and int(digits[8:10]) > 23:
            return result
        if length >= 12 and int(digits[10:12]) > 59:
            return result
        if length >= 14 and int(digits[12:14]) > 59:
            return result
        if zone and (int(zone[1:3]) > 23 or int(zone[3:]) > 59):
            return result
    except ValueError:
        return result
    precision = {4:"year", 6:"month", 8:"day", 10:"hour", 12:"minute", 14:"second"}[length]
    parts = [digits[:4]] + [digits[i:i+2] for i in range(4, length, 2)]
    parsed = "-".join(parts[:3])
    if length >= 10:
        parsed += "T" + ":".join(parts[3:])
    parsed += fraction or ""
    if zone:
        parsed += zone[:3] + ":" + zone[3:]
    result.update(precision="fraction" if fraction else precision, parsed=parsed,
                  state="partial" if length < 8 else "parsed",
                  value=day.isoformat() if length >= 8 else None)
    return result


def identifier_fact(parents, relative, code, suffix, context, warn=None):
    nodes = [n for p in parents for n in p.findall(relative, H)]
    candidates = [dict(n.attrib) for n in nodes]
    selected = [n for n in nodes if n.get("root") == OID + suffix]
    fact = dict(element=code, path=context, namespace=NS, expected_root=OID+suffix,
                candidates=candidates, value=None, state="missing", origin="source_xml")
    if len(selected) == 1 and selected[0].get("extension") and "nullFlavor" not in selected[0].attrib:
        fact.update(value=selected[0].get("extension"), state="interpreted",
                    root=selected[0].get("root"),
                    assigning_authority_name=selected[0].get("assigningAuthorityName"))
    elif nodes:
        fact["state"] = "ambiguous" if len(selected)>1 else "uninterpreted"
    if warn and fact["state"] in ("ambiguous", "uninterpreted"):
        warn("r3_identifier_context", f"R3 {code}: назначение идентификатора не установлено однозначно; сопоставление остановлено.")
    return fact

def extract_identity(root):
    warnings = []
    def warn(code, text, blocking=True):
        warnings.append(dict(type=code, message=text, blocking=blocking))

    is_batch = root.tag == f"{{{NS}}}MCCI_IN200100UV01"
    messages = root.findall("h:PORR_IN049016UV", H) if is_batch else (
        [root] if root.tag == f"{{{NS}}}PORR_IN049016UV" else [])
    if len(messages) != 1:
        warn("r3_message_context", "R3: требуется один ICSR в XML с namespace urn:hl7-org:v3. Исходник сохранён; автоматическое объединение запрещено.")
    investigations = [n for m in messages for n in m.findall(INV, H)]
    if len(investigations) != 1:
        warn("r3_case_context", "R3: контекст случая отсутствует или неоднозначен. Требуется разбор исходника.")

    def select_id(*args):
        return identifier_fact(*args, warn=warn)

    identifiers = {
        "batch": select_id([root] if is_batch else [], "h:id", "N.1.2", "22", "MCCI_IN200100UV01/id"),
        "message": select_id(messages, "h:id", "N.2.r.1", "1", "PORR_IN049016UV/id"),
        "sender": select_id(messages, "h:sender/h:device/h:id", "N.2.r.2", "11", "PORR_IN049016UV/sender/device/id"),
        "receiver": select_id(messages, "h:receiver/h:device/h:id", "N.2.r.3", "12", "PORR_IN049016UV/receiver/device/id"),
        "local": select_id(investigations, "h:id", "C.1.1", "1", "PORR_IN049016UV/controlActProcess/subject/investigationEvent/id"),
        "worldwide": select_id(investigations, "h:id", "C.1.8.1", "2", "PORR_IN049016UV/controlActProcess/subject/investigationEvent/id"),
    }
    # The other known case-ID root is not an unknown namespace. Unknown roots
    # remain in candidates and must never be assigned meaning by position.
    if any(n.get("root") not in {OID+"1", OID+"2"} for p in investigations for n in p.findall("h:id", H)):
        warn("r3_unknown_case_identifier", "R3: неизвестное пространство ID случая; требуется проверка исходника.")
    sender = identifiers["sender"]
    scope = json.dumps([sender.get("root"), sender["value"]], ensure_ascii=False) if sender["state"] == "interpreted" else None
    if not identifiers["worldwide"]["value"] and not (identifiers["local"]["value"] and scope):
        warn("r3_missing_case_identity", "R3: нет однозначного worldwide ID или ID случая с установленным отправителем.")
    dates = {}
    for key, relative, code in [("first_received_date", "h:effectiveTime/h:low", "C.1.4"),
                                ("latest_received_date", "h:availabilityTime", "C.1.5")]:
        dates[key] = source_date([n for p in investigations for n in p.findall(relative,H)],
                                "PORR_IN049016UV/controlActProcess/subject/investigationEvent/"+relative.replace("h:", ""), code)
        if dates[key]["state"] != "parsed":
            warn("r3_source_date", f"R3 {code}: состояние исходной даты — {dates[key]['state']}; дата регистрации PV1 её не заменяет.", False)
    return dict(identity_profile=PROFILE, identifiers=identifiers, sender_scope=scope,
                batch_identifier=identifiers["batch"]["value"],
                message_identifier=identifiers["message"]["value"],
                message_version=None,
                local_sender_case_id=identifiers["local"]["value"],
                worldwide_case_id=identifiers["worldwide"]["value"],
                sender=sender["value"], receiver=identifiers["receiver"]["value"],
                identity_warnings=warnings, **dates)


def transmission_facts(transmissions):
    """Read facts from immutable source snapshots, never reinterpret old JSON."""
    facts = []
    for tx in sorted(transmissions, key=lambda t: (t.created_at or datetime.min, t.id or "")):
        if tx.is_deleted:
            continue
        message = json.loads(tx.normalized_json or "{}").get("message", {})
        facts.append(dict(
            interpretation=message.get("identity_profile") or "legacy_unverified",
            transmission_id=tx.id, import_batch_id=tx.batch_id, import_item_id=tx.import_item_id,
            checksum_sha256=tx.xml_checksum_sha256, registered_at=tx.created_at,
            identifiers=message.get("identifiers", {}),
            dates={k:message.get(k) for k in ("first_received_date", "latest_received_date")},
            warnings=message.get("identity_warnings", []),
        ))
    return facts
