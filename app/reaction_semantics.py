"""Narrow incoming reaction interpretation; see fixtures/e2b_r3_reactions/README.md.

Recognising an XML level/code is not MedDRA dictionary validation.
"""
import json
import re

H = "{urn:hl7-org:v3}"
OBS = "2.16.840.1.113883.3.989.2.1.1.19"
MEDDRA = "2.16.840.1.113883.6.163"
OUTCOME = "2.16.840.1.113883.3.989.2.1.1.11"
HIGHLIGHT = "2.16.840.1.113883.3.989.2.1.1.10"
PROFILE = "ich-r3-reaction-v1"
CRITERIA = dict(seriousness_death="34", seriousness_life_threatening="21",
    seriousness_hospitalization="33", seriousness_disability="35",
    seriousness_congenital_anomaly="12", seriousness_other_medically_important="26")
OUTCOMES = {"0":"unknown", "1":"recovered/resolved", "2":"recovering/resolving",
    "3":"not recovered/not resolved/ongoing", "4":"recovered/resolved with sequelae", "5":"fatal"}
REACTION_PATH = ("controlActProcess/subject/investigationEvent/component/adverseEventAssessment/"
                 "subject1/primaryRole/subjectOf2/observation")


def path(value):
    return "/".join(H + p for p in value.split("/"))


def aggregate(criteria, reported=None):
    if any(v is True for v in criteria) or reported is True:
        return True
    if reported is False or (criteria and all(v is False for v in criteria)):
        return False
    return None


def fact(nodes, kind, system=None, versions=None):
    candidates = [dict(n.attrib) for n in nodes]
    result = dict(state="missing", value=None, candidates=candidates)
    if not nodes:
        return result
    if len(nodes) != 1:
        return dict(result, state="ambiguous")
    attrs = candidates[0]
    result.update(raw=attrs.get("value" if kind == "boolean" else "code"),
                  null_flavor=attrs.get("nullFlavor"), display_name=attrs.get("displayName"))
    if "nullFlavor" in attrs:
        return dict(result, state="invalid" if result["raw"] is not None else "null_flavor")
    raw = result["raw"]
    if system and (attrs.get("codeSystem") != system or
                   (versions and attrs.get("codeSystemVersion") not in versions)):
        return dict(result, state="unsupported")
    if kind == "boolean":
        # Preserve explicit XML false, but do not claim ICH outbound conformance.
        values = {"true":True, "1":True, "false":False, "0":False}
        return dict(result, value=values.get(raw), state="parsed" if raw in values else "invalid")
    if kind == "outcome":
        return dict(result, value=OUTCOMES.get(raw), state="parsed" if raw in OUTCOMES else "unsupported")
    if kind == "highlight":
        return dict(result, value={"1":False,"2":False,"3":True,"4":True}.get(raw),
                    state="parsed" if raw in ("1","2","3","4") else "unsupported")
    return dict(result, value=raw, state="syntax_only" if raw and re.fullmatch(r"[0-9]{8}",raw) else "invalid")


def extract_reactions(root, parse_date):
    messages = [root] if root.tag == H+"PORR_IN049016UV" else root.findall(H+"PORR_IN049016UV")
    results = []
    for message in messages:
        for index, observation in enumerate(message.findall(path(REACTION_PATH)), 1):
            code = observation.find(H+"code")
            if code is None or code.get("code") != "29":
                continue
            recognised = code.get("codeSystem") == OBS and code.get("codeSystemVersion") in (None,"1.1")
            values = observation.findall(H+"value")
            meddra = fact(values, "meddra", MEDDRA) if recognised else dict(state="unsupported",value=None,
                candidates=[dict(v.attrib) for v in values])
            original = [v.find(H+"originalText") for v in values]
            original = next((v for v in original if v is not None), None)
            raw_text = "".join(original.itertext()) if original is not None else None
            children = observation.findall(path("outboundRelationship2/observation"))
            raw_children = [dict(code=dict(c.find(H+"code").attrib) if c.find(H+"code") is not None else {},
                                 values=[dict(v.attrib) for v in c.findall(H+"value")]) for c in children]

            def sub(code_value, kind, system=None):
                selected = [c for c in children if c.find(H+"code") is not None and c.find(H+"code").get("code") == code_value]
                nodes = [v for c in selected for v in c.findall(H+"value")]
                bad = not recognised or any(c.find(H+"code").get("codeSystem") != OBS or
                    c.find(H+"code").get("codeSystemVersion") not in (None,"1.1") for c in selected)
                result = fact(nodes,kind,system,(None,"1.0") if system else None)
                if len(selected)>1:
                    result.update(state="ambiguous",value=None)
                elif bad:
                    result.update(state="unsupported",value=None)
                elif selected and not nodes:
                    result.update(state="invalid")
                return result

            criteria = {field:sub(c,"boolean") for field,c in CRITERIA.items()}
            outcome = sub("27","outcome",OUTCOME)
            reported = sub("37","highlight",HIGHLIGHT)
            flags = {k:v["value"] for k,v in criteria.items()}
            warnings = []
            if any(v["value"] is False for v in criteria.values()):
                warnings.append("Явное false сохранено; ICH E.i.3.2 задаёт true/NI, допустимость отправки не подтверждена.")
            if any(v["state"] in ("invalid","unsupported","ambiguous") for v in [meddra,outcome,reported,*criteria.values()]):
                warnings.append("Есть неподдержанное, ошибочное или неоднозначное исходное значение; требуется проверка.")
            if reported["value"] is False and any(v is True for v in flags.values()):
                warnings.append("Исходная общая оценка противоречит критериям серьёзности.")
            sources = dict(profile=PROFILE, xml_path=REACTION_PATH, namespace=H[1:-1], reaction_index=index,
                observation_code=dict(code.attrib), xml_ids=[dict(i.attrib) for i in observation.findall(H+"id")], meddra=meddra, criteria=criteria, outcome=outcome,
                reported_seriousness=reported, subobservations=raw_children, reported_text=raw_text,
                warnings=warnings, dictionary_validation="not_performed")
            first_value = values[0] if len(values)==1 else None
            low = observation.find(path("effectiveTime/low")); high = observation.find(path("effectiveTime/high"))
            def date_fact(node):
                parsed = parse_date(node.get("value") if node is not None else None)
                if node is not None and node.get("nullFlavor"):
                    parsed["null_flavor"] = node.get("nullFlavor")
                return parsed
            country = observation.find(path("location/locatedEntity/locatedPlace/code"))
            results.append(dict(reported_term=raw_text or "[Исходный текст реакции отсутствует]",
                verbatim_term=raw_text, meddra_llt_code=meddra["value"],
                meddra_llt_name=first_value.get("displayName") if first_value is not None else None,
                meddra_pt_code=None, meddra_pt_name=None,
                meddra_version=first_value.get("codeSystemVersion") if first_value is not None else None,
                term_language=original.get("language") if original is not None else None,
                term_country_code=country.get("code") if country is not None else None,
                onset_date=date_fact(low), end_date=date_fact(high),
                outcome=outcome["value"], reported_seriousness=reported["value"],
                is_serious=aggregate(list(flags.values()), reported["value"]), **flags,
                interpretation_profile=PROFILE, source_facts=sources))
    return results


def prepare_manual(data):
    data = dict(data)
    # Legacy API is_serious is explicitly a user's general assessment on write.
    reported = data.get("is_serious")
    data["reported_seriousness"] = reported
    data["is_serious"] = aggregate([data.get(k) for k in CRITERIA], reported)
    data["interpretation_profile"] = "manual-reaction-v1"
    data["reviewed_fields_json"] = json.dumps([*data.keys(), "__created_manually__"])
    return data


def provenance(reaction):
    source = json.loads(reaction.source_facts_json or "null")
    reviewed = json.loads(reaction.reviewed_fields_json or "[]")
    warnings = list((source or {}).get("warnings", []))
    if reaction.reported_seriousness is False and any(getattr(reaction,k) is True for k in CRITERIA):
        warnings.append("Текущая общая оценка противоречит критериям; требуется проверка.")
    states = {}
    for field in [*CRITERIA, "is_serious", "reported_seriousness", "meddra_llt_code", "meddra_pt_code", "meddra_version", "outcome"]:
        if field in reviewed:
            states[field] = "ручной ввод / оценка; словарь не проверен"
        elif source:
            fact_value = source.get("criteria", {}).get(field) or source.get(field, {})
            if field.startswith("meddra_"):
                states[field] = "из XML; словарь не проверен" if field != "meddra_pt_code" else "соответствие LLT → PT не установлено"
            elif field == "is_serious":
                states[field] = "расчёт по критериям и отдельной общей оценке"
            else:
                states[field] = "источник: " + fact_value.get("state", "missing")
        elif "__created_manually__" in reviewed:
            states[field] = "не указано при создании"
        else:
            states[field] = "историческое значение; происхождение не проверено"
    return dict(profile=reaction.interpretation_profile or "legacy_unverified", source=source, field_statuses=states,
        manually_reviewed_fields=reviewed, current_field_sources=json.loads(reaction.current_sources_json or '{}'), dictionary_validation="not_performed", warnings=warnings)
