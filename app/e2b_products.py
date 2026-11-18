"""Sequential trade-name / INN matching; INN matches require confirmation."""

import hashlib
import json

from sqlalchemy.orm import selectinload

from app.models import Product, ProductSubstance


def key(value):
    return " ".join(str(value or "").split()).casefold()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def source_token(product):
    return digest({name: value for name, value in product.items()
                   if name not in {"match", "product_match_status", "is_company_product"}
                   and not name.startswith("_")})


def match_product(db, source):
    products = (db.query(Product).options(selectinload(Product.substance_links).joinedload(ProductSubstance.substance))
                .filter(Product.is_deleted.is_(False), Product.is_active.is_(True)).all())
    names = {key(source.get(name)) for name in ("reported_product_name", "normalized_product_name")} - {""}
    # Active substances must never masquerade as an exact trade-name match.
    exact = [product for product in products if names & {
        key(product.product_name), key(product.product_name_normalized), key(product.product_code)} - {""}]
    method = "trade_name"
    matches = exact
    if not exact:
        method = "inn"
        substance_names = {key(name) for name in source.get("active_substances") or []} - {""}
        queries = substance_names or names
        matches = []
        for product in products:
            links = active_links(product)
            aliases = [{key(link.substance.substance_name), key(link.substance.substance_name_normalized),
                        key(link.substance.inn_name)} - {""} for link in links]
            # Match the complete known composition. Do not equate a single
            # ingredient with a combination, salts, or unrecognized components.
            if queries and aliases and all(any(name in values for values in aliases) for name in queries) \
                    and all(values & queries for values in aliases):
                matches.append(product)
    candidates = [candidate(product) for product in sorted(matches, key=lambda p: (key(p.product_name), p.product_code, p.id))]
    token = source_token(source)
    input_token = digest({"source": token, "method": method, "candidates": candidates})
    result = dict(status="not_found", product_id=None, product_name=None, is_company_product=False,
                  matched_by=method, candidates=candidates, source_token=token, input_token=input_token,
                  stale_decision=False)
    decision = source.get("_product_resolution")
    selected = next((row for row in candidates if decision and row["product_id"] == decision.get("product_id")), None)
    if decision and selected and decision.get("input_token") == input_token:
        result.update(status="confirmed_company_product" if selected["is_company_product"] else "confirmed_non_company_product",
                      product_id=selected["product_id"], product_name=selected["product_name"],
                      is_company_product=selected["is_company_product"], decision=decision)
    else:
        result["stale_decision"] = bool(decision)
        if len(exact) == 1 and not decision:
            row = candidates[0]
            result.update(status="exact_company_product" if row["is_company_product"] else "exact_non_company_product",
                          product_id=row["product_id"], product_name=row["product_name"], is_company_product=row["is_company_product"])
        elif candidates:
            result["status"] = "substance_candidates" if method == "inn" else "requires_choice"
    return result


def active_links(product):
    return [link for link in product.substance_links if not link.is_deleted and link.is_active
            and link.substance_role == "active" and link.substance and not link.substance.is_deleted and link.substance.is_active]


def candidate(product):
    links = active_links(product)
    return dict(product_id=product.id, product_name=product.product_name, product_code=product.product_code,
                is_company_product=bool(product.is_company_product), strength=product.strength,
                dosage_form=product.dosage_form_name("ru"),
                substances=[dict(id=link.substance.id, name=link.substance.substance_name, inn=link.substance.inn_name)
                            for link in sorted(links, key=lambda row: row.substance_id)],
                dictionary_token=digest([product.version, product.product_name, product.product_name_normalized,
                    product.product_code, product.is_company_product, product.strength, product.dosage_form_id,
                    product.dosage_form, [(link.id, link.version, link.substance_id, link.substance.version,
                    link.substance.substance_name, link.substance.substance_name_normalized, link.substance.inn_name)
                    for link in sorted(links, key=lambda row: row.id)]]))
