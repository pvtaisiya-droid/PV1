"""Generate a read-only audit report before partner-scoped case numbering migration."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3


CASE_NUMBER_PATTERN = re.compile(r"^\d{4}-\d{3,}$")
LEADING_NUMBER_PATTERN = re.compile(r"^(?:UNASSIGNED-)?(\d{4})-(\d{3,})")
PARTNER_CODE_PATTERN = re.compile(r"^[A-Z0-9-]+$")


def build_report(database_path: Path) -> dict:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    cases = connection.execute(
        '''
        SELECT case_row.id, case_row.case_number, case_row.partner_id,
               case_row.created_at, partner.partner_code, partner.partner_name
        FROM "tblCases" AS case_row
        LEFT JOIN "tblPartners" AS partner ON partner.id = case_row.partner_id
        WHERE COALESCE(case_row.is_deleted, 0) = 0
        ORDER BY partner.partner_code, case_row.case_number, case_row.id
        '''
    ).fetchall()
    partners = connection.execute(
        '''
        SELECT id, partner_code, partner_name
        FROM "tblPartners"
        WHERE COALESCE(is_deleted, 0) = 0
        ORDER BY partner_name, id
        '''
    ).fetchall()

    grouped_cases: dict[str, list[sqlite3.Row]] = defaultdict(list)
    number_partners: dict[str, set[str]] = defaultdict(set)
    for row in cases:
        group_key = row["partner_id"] or "UNASSIGNED"
        grouped_cases[group_key].append(row)
        if row["case_number"]:
            number_partners[row["case_number"]].add(group_key)

    partner_rows = []
    duplicate_count = 0
    invalid_count = 0
    blank_count = 0
    for group_key, rows in grouped_cases.items():
        numbers: dict[str, list[str]] = defaultdict(list)
        invalid_records = []
        blank_records = []
        maxima: dict[str, int] = defaultdict(int)
        for row in rows:
            number = (row["case_number"] or "").strip()
            if not number:
                blank_records.append(row["id"])
                continue
            numbers[number].append(row["id"])
            expected_format = (
                bool(CASE_NUMBER_PATTERN.fullmatch(number))
                if row["partner_id"]
                else bool(re.fullmatch(r"UNASSIGNED-\d{4}-\d{3,}", number))
            )
            if not expected_format:
                invalid_records.append(
                    {"case_id": row["id"], "case_number": number}
                )
            leading = LEADING_NUMBER_PATTERN.match(number)
            if leading:
                maxima[leading.group(1)] = max(
                    maxima[leading.group(1)],
                    int(leading.group(2)),
                )
        duplicates = [
            {"case_number": number, "case_ids": case_ids}
            for number, case_ids in numbers.items()
            if len(case_ids) > 1
        ]
        duplicate_count += len(duplicates)
        invalid_count += len(invalid_records)
        blank_count += len(blank_records)
        sample = rows[0]
        partner_rows.append(
            {
                "partner_id": None if group_key == "UNASSIGNED" else group_key,
                "partner_code": sample["partner_code"],
                "partner_name": sample["partner_name"],
                "case_count": len(rows),
                "maximum_sequence_by_year": dict(sorted(maxima.items())),
                "duplicates_within_partner": duplicates,
                "blank_number_case_ids": blank_records,
                "nonconforming_numbers": invalid_records,
            }
        )

    missing_codes = [
        {"partner_id": row["id"], "partner_name": row["partner_name"]}
        for row in partners
        if not (row["partner_code"] or "").strip()
    ]
    invalid_codes = [
        {
            "partner_id": row["id"],
            "partner_code": row["partner_code"],
            "partner_name": row["partner_name"],
        }
        for row in partners
        if (row["partner_code"] or "").strip()
        and not PARTNER_CODE_PATTERN.fullmatch((row["partner_code"] or "").strip())
    ]
    repeated_across_partners = [
        {"case_number": number, "partner_scopes": sorted(scopes)}
        for number, scopes in number_partners.items()
        if len(scopes) > 1
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_file": database_path.name,
        "summary": {
            "active_case_count": len(cases),
            "partner_scope_count": len(grouped_cases),
            "duplicate_numbers_within_partner_count": duplicate_count,
            "same_numbers_across_partners_count": len(repeated_across_partners),
            "unassigned_case_count": len(grouped_cases.get("UNASSIGNED", [])),
            "blank_case_number_count": blank_count,
            "nonconforming_case_number_count": invalid_count,
            "partners_without_code_count": len(missing_codes),
            "partners_with_nonconforming_code_count": len(invalid_codes),
        },
        "partners_without_code": missing_codes,
        "partners_with_nonconforming_code": invalid_codes,
        "same_numbers_across_partners": repeated_across_partners,
        "partner_registers": partner_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("pv_system.db"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(args.database.resolve())
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)


if __name__ == "__main__":
    main()
