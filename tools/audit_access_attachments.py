"""Explain unresolved Access placeholders in a verified snapshot, without writes to data."""

import argparse
from collections import defaultdict
from contextlib import closing
import csv
import json
from pathlib import Path

from tools import backup_snapshot as backup


FILE_METADATA = (
    "file_name", "mime_type", "file_size_bytes", "storage_path", "file_url",
)


def analyze(snapshot: Path, source_csv: Path) -> dict:
    snapshot, source_csv = snapshot.resolve(), source_csv.resolve()
    recovery = backup.verify_snapshot(snapshot)
    csv_hash = backup.digest(source_csv)
    source_rows = defaultdict(list)
    with source_csv.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"legacy_access_table", "legacy_access_id", *FILE_METADATA}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Attachment CSV lacks required identity/file columns")
        for number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed attachment CSV row")
            key = (row["legacy_access_table"].strip(), row["legacy_access_id"].strip())
            source_rows[key].append((number, row))
    if backup.digest(source_csv) != csv_hash:
        raise ValueError("Attachment CSV changed while reading")

    mappings = defaultdict(list)
    with closing(backup.readonly(snapshot / "database.sqlite3")) as db:
        for table, source_id, target_id in db.execute(
            "SELECT source_table, source_id, target_id FROM access_import_id_map "
            "WHERE target_table = 'tblAttachments'"
        ):
            mappings[str(target_id)].append((table, source_id))
        checksums = {str(key): value for key, value in db.execute(
            "SELECT id, checksum_sha256 FROM tblAttachments"
        )}

    entries = []
    for ref in recovery["references"]:
        if ref["status"] == "present":
            continue
        entry = {**ref, "classification": "unexplained"}
        keys = mappings[ref["id"]] if ref["table"] == "tblAttachments" else []
        if ref["column"] == "storage_path" and len(keys) == 1:
            key = keys[0]
            candidates = source_rows[key]
            if len(candidates) == 1:
                number, row = candidates[0]
                entry.update(source_table=key[0], source_id=key[1], csv_row=number)
                if (key[0] and key[1]
                        and ref["path"] == f"access-migration/{key[1]}"
                        and not checksums.get(ref["id"])
                        and all(not row[field].strip() for field in FILE_METADATA)):
                    entry["classification"] = "import_placeholder_without_file_metadata"
        entries.append(entry)
    explained = sum(e["classification"] != "unexplained" for e in entries)
    return {
        "format": 1,
        "snapshot_manifest_sha256": backup.digest(snapshot / "manifest.json"),
        "source_csv_sha256": csv_hash,
        "source_csv_rows": sum(len(rows) for rows in source_rows.values()),
        "unresolved_references": len(entries),
        "explained_import_placeholders": explained,
        "unexplained_references": len(entries) - explained,
        # Explaining provenance never turns a missing file into a recovered file.
        "recovery_complete": recovery["recovery_complete"],
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("source_csv", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.snapshot.resolve()):
        parser.error("Output must be outside the verified snapshot")
    report = analyze(args.snapshot, args.source_csv)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "entries"}))
    return 2 if report["unexplained_references"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
