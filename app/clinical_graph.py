"""Message-local graph. Keys are NOT identities across follow-ups."""
import json
from sqlalchemy import inspect
from app import models as m
from app.clinical_values import dump
from app.drug_data import model_for

META = {"id", "case_id", "case_product_id", "reaction_id", "created_at", "updated_at", "version",
        "is_deleted", "deleted_at", "deleted_by", "delete_reason", "source_facts_json", "reviewed_fields_json", "current_sources_json"}


def graph(parsed):
    result, refs = [], {"reaction": {}, "product": {}}
    source = parsed["message"].get("import_source")
    def add(key, model, kind, values, links=None, facts=None):
        facts = facts or {}
        explicit = set()
        if kind == "reaction":
            explicit.update(k for k,v in facts.get("criteria", {}).items() if v.get("state") != "missing")
            explicit.update(k for k,src in (("outcome","outcome"),("reported_seriousness","reported_seriousness"),("meddra_llt_code","meddra")) if facts.get(src, {}).get("state", "missing") != "missing")
        if kind in ("indication", "rechallenge") and facts.get("value"):
            explicit.update(("meddra_llt_code", "null_flavor") if kind == "indication" else ("rechallenge_code", "null_flavor"))
        if kind == "product" and facts.get("role", {}).get("state", "missing") != "missing":
            explicit.add("drug_role")
        if kind == "dosage":
            for source_key, fields in (("dose",("dose_value","dose_unit")),("numerator",("dose_numerator_value","dose_numerator_unit")),("denominator",("dose_denominator_value","dose_denominator_unit")),("period",("interval_value","interval_unit")),("width",("duration_value","duration_unit"))):
                if facts.get(source_key, {}).get("nullFlavor") is not None:
                    explicit.update(fields)
            for side in ("start","end"):
                if facts.get(side, {}).get("state", "missing") != "missing":
                    explicit.update((side+"_raw", side+"_null_flavor"))
            if values.get("route_code") is not None or values.get("route_null_flavor") is not None:
                explicit.update(("route_code", "route_null_flavor"))
        columns = set(inspect(model).columns.keys()) - META
        values = {k: v for k, v in values.items() if k in columns and (v is not None or k in explicit)}
        result.append(dict(key=key, model=model.__name__, kind=kind, values=values, links=links or {}, source={**(facts or {}), "import_source": source}))
    def register(kind, facts, key):
        for i in facts.get("xml_ids", []):
            ident = (i.get("root"), i.get("extension"))
            if ident in refs[kind]:
                raise ValueError("Неоднозначный ID повтора внутри сообщения.")
            refs[kind][ident] = key
    def resolve(kind, ids):
        matches = {refs[kind].get((i.get("root"), i.get("extension"))) for i in ids}
        matches.discard(None)
        if len(matches) != 1:
            raise ValueError("Связь внутри сообщения не установлена однозначно: " + kind)
        return matches.pop()
    for n, r in enumerate(parsed.get("reactions", [])):
        key = f"reaction:{n}"
        values = dict(r)
        values.pop("is_serious", None)
        if not r.get("source_facts", {}).get("reported_text"):
            values.pop("reported_term", None)
        # These facts have one editable owner in the clinical reaction block.
        for field in ("term_language", "term_country_code", "verbatim_term", "medically_confirmed", "duration_text"):
            values.pop(field, None)
        for side in ("onset", "end"):
            values.pop(side + "_date", None)
        add(key, m.Reaction, "reaction", values, facts=r.get("source_facts"))
        register("reaction", r.get("source_facts", {}), key)
    for n, p in enumerate(parsed.get("products", [])):
        key = f"product:{n}"
        values = dict(p)
        if p.get("coded_identifiers"):
            values["coded_identifiers_json"] = dump(p["coded_identifiers"])
        if p.get("source_facts", {}).get("role", {}).get("state") == "missing":
            values.pop("drug_role", None)
        values["interpretation_profile"] = p.get("drug_profile")
        add(key, m.CaseProduct, "product", values, facts=p.get("source_facts"))
        register("product", p.get("source_facts", {}), key)
        for kind, label in (("dosage", "dosages"), ("substance", "substances"), ("indication", "indications"),
                            ("assessment", "assessments"), ("rechallenge", "rechallenges")):
            for j, child in enumerate(p.get(label, [])):
                links = {"case_product_id": key}
                if child.get("reaction_ref"):
                    root, ext = json.loads(child["reaction_ref"])
                    links["reaction_id"] = resolve("reaction", [{"root": root, "extension": ext}])
                values = {**child, "interpretation_profile": p.get("drug_profile")}
                if kind == "assessment":
                    values["causality_method"] = "imported_text"
                add(f"{key}:{kind}:{j}", model_for(kind), kind, values, links, child.get("source_facts"))
    for record in parsed.get("clinical_records", []):
        links = {}
        if record.get("parent_key"):
            links["parent_id"] = record["parent_key"]
        if "drug_refs" in record:
            links["case_product_id"] = resolve("product", record["drug_refs"])
        if "reaction_refs" in record:
            links["reaction_id"] = resolve("reaction", record["reaction_refs"])
        add(record["key"], m.CaseClinicalRecord, record["kind"],
            {"kind": record["kind"], "fields_json": dump(record["fields"]), "interpretation_profile": "ich-clinical-v1"}, links, record["source_facts"])
    # Topological sort: patient before history/parent, drug and reaction before pairs.
    ordered, remaining = [], list(result)
    while remaining:
        ready = [r for r in remaining if set(r["links"].values()) <= {n["key"] for n in ordered}]
        if not ready:
            raise ValueError("В графе сообщения есть неразрешённая связь.")
        ordered.extend(ready)
        remaining = [r for r in remaining if r not in ready]
    return ordered


def fields(node):
    return json.loads(node["values"]["fields_json"]) if node["model"] == "CaseClinicalRecord" else node["values"]


def current_fields(row):
    return json.loads(row.fields_json) if isinstance(row, m.CaseClinicalRecord) else {
        c.key: getattr(row, c.key) for c in inspect(type(row)).columns if c.key not in META}


def owner(db, row):
    return row.case_id if hasattr(row, "case_id") else db.get(m.CaseProduct, row.case_product_id).case_id
