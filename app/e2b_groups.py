"""Conservative, deterministic assembly of case variants from an E2B package.

Only clinical variants of the same version complement each other. Source records
are immutable; every selected value has provenance and every conflict is explicit.
"""

from collections import Counter, defaultdict
from copy import deepcopy
import json
import re


TRANSPORT_FIELDS = {"message_identifier", "message_number", "receiver", "message_date", "related_messages"}
DERIVED_FIELDS = {"match", "product_match_status", "is_company_product", "import_source"}


def normalized_text(value):
    return " ".join(str(value or "").casefold().split())


def case_key(message, file_id=""):
    if message.get("worldwide_case_id"):
        return str(message["worldwide_case_id"]).strip()
    sender = normalized_text(message.get("sender"))
    local = message.get("local_sender_case_id")
    if local and sender:
        return "local:" + json.dumps([sender, str(local).strip()], ensure_ascii=False)
    # A missing case identity must never collapse unrelated messages into unknown.
    return "unidentified:" + str(file_id)


def empty(value):
    return (value is None or value == "" or value == [] or value == {}
            or isinstance(value, dict) and all(empty(child) for child in value.values()))


class CaseAssembler:
    def __init__(self, resolutions=None):
        self.resolutions = resolutions or {}
        self.conflicts = []
        self.provenance = {}

    def conflict(self, variants, path, reason="different_values"):
        choices = [{"file_id": source["id"], "file": source["path"], "value": deepcopy(value)}
                   for source, value in variants]
        selected = self.resolutions.get(path)
        selected_choices = [choice for choice in choices if choice["file_id"] == selected]
        if selected is not None and not selected_choices:
            raise ValueError("Conflict resolution refers to an unavailable source.")
        self.conflicts.append({"path": path, "reason": reason, "choices": choices,
                               "resolved": bool(selected_choices), "selected": selected})
        if selected_choices:
            choice = selected_choices[0]
            self.provenance[path] = [choice]
            return deepcopy(choice["value"])
        # An unresolved draft cannot be imported. Do not silently pick one source.
        return None

    def merge(self, variants, path):
        present = [(source, value) for source, value in variants if not empty(value)]
        if not present:
            return deepcopy(variants[0][1]) if variants else None
        first = present[0][1]
        if not isinstance(first, dict) and all(value == first for _, value in present):
            self.provenance[path] = [{"file_id": source["id"], "file": source["path"]} for source, _ in present]
            return deepcopy(first)
        if all(isinstance(value, dict) for _, value in present):
            if all("raw" in value and "precision" in value for _, value in present):
                if all(value == first for _, value in present):
                    self.provenance[path] = [{"file_id": source["id"], "file": source["path"]} for source, _ in present]
                    return deepcopy(first)
                # A partial date can be completed; explicit nullFlavor values
                # remain distinct from a missing date and from a known date.
                raw = [str(value.get("raw") or "") for _, value in present]
                if all(not value.get("null_flavor") for _, value in present) and all(re.fullmatch(r"\d{4}(?:\d{2}){0,2}", text) for text in raw):
                    longest = max(raw, key=len)
                    if all(longest.startswith(text) for text in raw):
                        return self.merge([(source, value) for source, value in present if str(value.get("raw")) == longest], path)
                return self.conflict(present, path)
            keys = sorted({key for _, value in present for key in value})
            return {key: self.merge([(source, value.get(key)) for source, value in present], path + "." + key)
                    for key in keys if key not in DERIVED_FIELDS}
        return self.conflict(present, path)

    def rows(self, variants, kind):
        variants = [(source, [{key: value for key, value in row.items() if key not in DERIVED_FIELDS} for row in rows])
                    for source, rows in variants]
        name_key = "reported_product_name" if kind == "products" else "reported_term"
        codes_by_name = defaultdict(set)
        if kind == "reactions":
            for _, rows in variants:
                for row in rows:
                    code = row.get("meddra_llt_code") or row.get("meddra_pt_code")
                    if code:
                        codes_by_name[normalized_text(row.get(name_key))].add(str(code))

        def identity(row):
            name = normalized_text(row.get(name_key))
            if not name and kind == "products":
                substances = sorted({normalized_text(value) for value in row.get("active_substances") or []
                                     if normalized_text(value)})
                if substances:
                    return ("", json.dumps(substances, ensure_ascii=False))
            if not name:
                return None
            if kind == "reactions" and len(codes_by_name[name]) > 1:
                code = row.get("meddra_llt_code") or row.get("meddra_pt_code")
                return (name, str(code)) if code else None
            return (name, "")

        grouped = defaultdict(list)
        for source, rows in variants:
            counts = Counter(identity(row) for row in rows)
            if None in counts or any(count > 1 for count in counts.values()):
                result = self.conflict(variants, kind, "ambiguous_rows")
                return result or []
            for row in rows:
                grouped[identity(row)].append((source, row))
        result = []
        for key in sorted(grouped):
            result.append(self.merge(grouped[key], f"{kind}.{len(result)}"))
        return result

    def assemble(self, records):
        # Sorting is independent of archive member order. Original records are
        # never modified, including the transport data used by transmissions.
        records = sorted(records, key=lambda record: (record["path"], record.get("checksum_sha256", "")))
        versions = {str(record["normalized"]["message"].get("message_version") or "") for record in records}
        numeric = bool(versions) and all(version.isdecimal() for version in versions)
        if len(versions) == 1:
            selected_version = next(iter(versions))
        elif numeric:
            selected_version = str(max(int(version) for version in versions))
        else:
            selected_version = self.conflict([(record, str(record["normalized"]["message"].get("message_version") or "")) for record in records], "message.message_version", "unorderable_versions")
        clinical = [record for record in records if
                    (int(str(record["normalized"]["message"].get("message_version"))) == int(selected_version) if numeric
                     else str(record["normalized"]["message"].get("message_version") or "") == selected_version)]
        if not clinical:
            return None, selected_version
        primary = deepcopy(clinical[0]["normalized"])
        for section in ("patient", "reporter", "literature", "narrative"):
            primary[section] = self.merge([(record, record["normalized"].get(section, {})) for record in clinical], section)
        messages = [(record, {key: value for key, value in record["normalized"]["message"].items() if key not in TRANSPORT_FIELDS | {"message_version"}}) for record in clinical]
        primary["message"].update(self.merge(messages, "message"))
        primary["message"]["message_version"] = selected_version
        for section in ("products", "reactions"):
            primary[section] = self.rows([(record, record["normalized"].get(section, [])) for record in clinical], section)
        primary["embedded_documents"] = []  # Extract each original XML separately.
        primary["warnings"] = []
        return primary, selected_version


def build_case_groups(records, resolutions=None):
    grouped = defaultdict(list)
    for record in records:
        if record.get("normalized"):
            grouped[case_key(record["normalized"]["message"], record["id"])].append(record)
    groups = []
    for key in sorted(grouped):
        sources = grouped[key]
        assembler = CaseAssembler((resolutions or {}).get(key))
        merged, version = assembler.assemble(sources)
        first = sorted(sources, key=lambda record: record["path"])[0]["normalized"]["message"]
        groups.append({
            "case_key": key, "worldwide_case_id": first.get("worldwide_case_id"),
            "local_sender_case_id": first.get("local_sender_case_id"),
            "item_ids": sorted(record["id"] for record in sources),
            "merged": merged, "clinical_version": version,
            "conflicts": assembler.conflicts, "provenance": assembler.provenance,
            "unresolved_count": sum(not conflict["resolved"] for conflict in assembler.conflicts),
            "messages": [{"file_name": record["file_name"], "path": record["path"], "file_id": record["id"],
                          **{field: record["normalized"]["message"].get(field) for field in ("message_identifier", "message_version", "receiver", "followup_type")}}
                         for record in sorted(sources, key=lambda record: record["path"])],
        })
    assign_documents(groups, records)
    return groups


def assign_documents(groups, records):
    """Use the most specific shared directory; ambiguous documents stay in batch."""
    by_id = {record["id"]: record for record in records}
    for group in groups:
        group["attachment_ids"] = list(group["item_ids"])
    for record in records:
        if record.get("normalized"):
            continue
        if record.get("duplicate_of_item_id"):
            matching = [group for group in groups if record["duplicate_of_item_id"] in group["item_ids"]]
            if len(matching) == 1:
                if record.get("role") == "xml":
                    matching[0]["item_ids"].append(record["id"])
                matching[0]["attachment_ids"].append(record["id"])
                continue
        scores = []
        directory = record["path"].rsplit("/", 1)[0] if "/" in record["path"] else ""
        for group in groups:
            score = 1 if len(groups) == 1 else 0
            for item_id in group["item_ids"]:
                xml_path = by_id[item_id]["path"]
                xml_dir = xml_path.rsplit("/", 1)[0] if "/" in xml_path else ""
                if directory and xml_dir and (directory == xml_dir or directory.startswith(xml_dir + "/") or xml_dir.startswith(directory + "/")):
                    score = max(score, len(xml_dir) + 1 if directory.startswith(xml_dir) else len(directory) + 1)
            scores.append((score, group))
        if scores:
            best = max(score for score, _ in scores)
            winners = [group for score, group in scores if score == best]
            if best and len(winners) == 1:
                if record.get("role") == "xml":
                    winners[0]["item_ids"].append(record["id"])
                winners[0]["attachment_ids"].append(record["id"])
