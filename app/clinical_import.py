"""ICH clinical repeats. Selectors are versioned in clinical_contract.json.

Names, IDs and ordering do not identify records across incoming messages.
"""
from lxml import etree as E
import base64
import hashlib
from app.clinical_values import CONTRACT, normalize, dump

NS = {"h": "urn:hl7-org:v3", "xsi": "http://www.w3.org/2001/XMLSchema-instance"}
H = "{urn:hl7-org:v3}"
PARENT_KINDS = {
    "parent": "patient", "medical_history": "patient", "drug_history": "patient",
    "death_cause": "patient", "autopsy_cause": "patient", "test": "patient",
    "parent_medical_history": "parent", "parent_drug_history": "parent",
    "study_registration": "study", "study_site": "study",
}


def identifier(node):
    return [{"root": n.get("root"), "extension": n.get("extension")}
            for n in node.findall(H + "id") if n.get("root") and not n.get("nullFlavor")]


def quantity(node):
    return {"attributes": dict(node.attrib), "text": node.text,
            "parts": [{"type": E.QName(n).localname, "attributes": dict(n.attrib), "text": n.text}
                      for n in node if isinstance(n.tag, str)]}


def extract(xml_bytes, source_ref=None):
    parser = E.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = E.fromstring(xml_bytes, parser)
    if root.tag == H + "PORR_IN049016UV":
        wrapper = E.Element(H + "MCCI_IN200100UV01")
        wrapper.append(root)
        root = wrapper
    records, warnings, source_nodes = [], [], {}
    for kind, definition in CONTRACT.items():
        if not definition["xpath"]:
            continue
        for index, node in enumerate(root.xpath(definition["xpath"], namespaces=NS), 1):
            values = {}
            for code, field in definition["fields"].items():
                if not field.get("xpath"):
                    continue
                nodes = node.xpath(field["xpath"], namespaces=NS)
                if not nodes:
                    continue  # absent field is never an instruction to clear
                if len(nodes) != 1:
                    values[code] = dict(raw=None, value=None, state="ambiguous", origin="source_xml",
                        candidates=[E.tostring(n, encoding="unicode") for n in nodes], null_flavor=None)
                    warnings.append({"type": "clinical_value", "message": f"{kind}/{index}/{code}: неоднозначное значение сохранено для разбора.", "blocking": False})
                    continue
                element = nodes[0]
                if field.get("attribute") and field["attribute"] not in element.attrib and "nullFlavor" not in element.attrib:
                    continue
                raw = element.get(field["attribute"]) if field.get("attribute") else "".join(element.itertext()).strip()
                if field["type"] in ("quantity", "quantity_unit"):
                    q = quantity(element)
                    q['attributes'].pop('unit', None)
                    for part in q['parts']:
                        part['attributes'].pop('unit', None)
                    raw = dump(q)
                if code == 'F.r.3.3':
                    raw = dump({'value': element.get('unit'), 'parts': [dict(type=E.QName(n).localname, unit=n.get('unit')) for n in element if n.get('unit')]})
                if field["type"] == "document":
                    # Bytes are stored by the existing embedded-document handler.
                    document = {"media_type": element.get("mediaType"), "representation": element.get("representation"),
                                "reference": [dict(n.attrib) for n in element.findall(H + "reference")]}
                    encoded = ''.join((element.text or '').split())
                    if element.get('representation') == 'B64' and len(encoded) <= 12 * 1024 * 1024:
                        try:
                            document['sha256'] = hashlib.sha256(base64.b64decode(encoded, validate=True)).hexdigest()
                        except ValueError:
                            document['decode_state'] = 'invalid'
                    raw = dump(document)
                nf = element.get("nullFlavor")
                if nf is not None and not (element.get("value") or element.get("code") or "".join(element.itertext()).strip()):
                    raw = None
                value = normalize(field, raw, nf, system=element.get("codeSystem"),
                    system_version=element.get("codeSystemVersion"), origin="source_xml",
                    context={"xpath": field["xpath"], "attributes": dict(element.attrib), "import_source": source_ref})
                if field.get("system") and value["system"] and value["system"] != field["system"]:
                    value["state"] = "unsupported"
                if (value.get('system') or '').startswith('TBD-'):
                    value['state'] = 'unsupported'
                values[code] = value
                if value["state"] in ("invalid", "unsupported"):
                    warnings.append({"type": "clinical_value", "message": f"{kind}/{index}/{code}: {value['state']}; исходное значение сохранено.", "blocking": False})
            if kind in ('reporter', 'sender'):
                prefix = 'h:relatedInvestigation/h:subjectOf2/h:controlActEvent/h:author/h:assignedEntity/' if kind == 'reporter' else ''
                contacts = [dict(n.attrib) for n in node.xpath(prefix + 'h:telecom', namespaces=NS)]
                if contacts:
                    values['local.contacts'] = normalize(CONTRACT[kind]['fields']['local.contacts'], dump(contacts), origin='source_xml', context={'import_source': source_ref})
            if kind in ('drug_history', 'parent_drug_history'):
                identifiers = [dict(n.attrib) for n in node.xpath('h:consumable/h:instanceOfKind/h:kindOfProduct/h:code', namespaces=NS)]
                if identifiers:
                    values['local.product_identifiers'] = normalize(CONTRACT[kind]['fields']['local.product_identifiers'], dump(identifiers), origin='source_xml', context={'import_source': source_ref, 'idmp_interpretation':'unestablished'})
            if not values and kind not in ("patient", "parent", "study"):
                continue
            key = f"clinical:{kind}:{index}"
            record = dict(key=key, kind=kind, fields=values, parent_key=None,
                source_facts=dict(profile="ich-clinical-v1", xpath=definition["xpath"], repeat_index=index,
                    xml_sha256=hashlib.sha256(E.tostring(node, encoding='utf-8')).hexdigest(), xml_ids=identifier(node), import_source=source_ref))
            if kind in ("drug_extra", "drug_additional", "pair_interval", "pair_last_interval"):
                drug = node if kind == "drug_extra" else next((n for n in node.iterancestors() if n.tag == H + "substanceAdministration"), None)
                record["drug_refs"] = identifier(drug) if drug is not None else []
            if kind == "reaction_extra":
                record["reaction_refs"] = identifier(node)
            if kind in ("pair_interval", "pair_last_interval"):
                record["reaction_refs"] = [dict(n.attrib) for n in node.findall(H + "actReference/" + H + "id")]
            records.append(record)
            source_nodes[key] = node
    # Establish ancestry inside this message. It is not follow-up matching.
    for record in records:
        parent_kind = PARENT_KINDS.get(record["kind"])
        if not parent_kind:
            continue
        node = source_nodes[record["key"]]
        parents = [p for p in records if p["kind"] == parent_kind and source_nodes[p["key"]] in node.iterancestors()]
        if len(parents) == 1:
            record["parent_key"] = parents[0]["key"]
        else:
            warnings.append({"type": "clinical_parent", "message": f"{record['key']}: связь с {parent_kind} не установлена однозначно.", "blocking": True})
    return records, warnings
