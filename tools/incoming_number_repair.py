"""Prepare and explicitly apply a reviewed incoming-number repair, offline.

Never imports the application or silently runs on its default DB.
Version-2 plans preserve every original duplicate-group number and use suffixes.
Version-3 plans preserve those numbers and assign current MSG-year registrations.
Historical-column changes and all audit events are transactional.
Preparation is read-only. Application needs an explicit database, reviewed plan,
active actor, reason and --offline; all changes and new audit events are atomic.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


NUMBER = re.compile(r"(\d{4})-(\d{3,})\Z", flags=re.ASCII)
ACTION = "message_number_repair"


def connect(database: Path, *, writable: bool = False):
    # mode=rw also refuses to create a database at a misspelled path.
    connection = sqlite3.connect(database.resolve().as_uri() + ("?mode=rw" if writable else "?mode=ro"), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def revision(connection):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='alembic_version'").fetchone():
        return []
    return [r[0] for r in connection.execute("SELECT version_num FROM alembic_version ORDER BY version_num")]


def rows(connection):
    return connection.execute("SELECT * FROM tblIncomingRequests ORDER BY id").fetchall()


def state_hash(records):
    # Review becomes stale on any change, including a direct SQL edit which
    # neglected to increment version. Only the digest goes into the plan.
    state = [dict(row) for row in records]
    serialized = json.dumps(state, sort_keys=True, ensure_ascii=False,
                            default=lambda value: value.hex() if isinstance(value, bytes) else str(value))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def prepare_plan(database: Path, *, preserve_legacy: bool = False,
                 numbering_policy: str = "legacy", numbering_year: int | None = None):
    if numbering_policy not in {"legacy", "current"}:
        raise ValueError("Choose legacy or current registration numbering.")
    if numbering_policy == "current":
        if type(numbering_year) is not int or not 1900 <= numbering_year <= 9999:
            raise ValueError("Current registration numbering requires an explicit numbering year.")
        preserve_legacy = True
    elif numbering_year is not None:
        raise ValueError("A numbering year applies only to current registration numbering.")
    with closing(connect(database)) as connection:
        records = rows(connection)
        groups = defaultdict(list)
        used = set()
        maxima = defaultdict(int)
        for row in records:
            number = row["message_number"]
            if number is None:
                continue
            groups[number].append(row)
            used.add(number)
            match = NUMBER.fullmatch(number)
            if match:
                maxima[match[1]] = max(maxima[match[1]], int(match[2]))
        changes, retained, originals = [], [], []
        for number, group in sorted(groups.items()):
            if len(group) < 2:
                continue
            match = NUMBER.fullmatch(number)
            if not match:
                raise ValueError("A duplicate number has an unsupported format; prepare a manual review.")
            ordered = sorted(group, key=lambda r: (
                r["received_date"] or "9999-12-31", r["received_time"] or "",
                r["created_at"], r["id"]))
            retained.append({"id": ordered[0]["id"], "message_number": number,
                             "received_date": ordered[0]["received_date"]})
            year, width = match[1], len(match[2])
            originals.extend({"id": row["id"], "message_number": number} for row in ordered)
            suffix = 1
            for row in ordered[1:]:
                while True:
                    if preserve_legacy:
                        suffix += 1
                        candidate = f"{number}-{suffix:02d}"
                    else:
                        maxima[year] += 1
                        candidate = f"{year}-{maxima[year]:0{width}d}"
                    if candidate not in used:
                        break
                used.add(candidate)
                changes.append({"id": row["id"], "expected_message_number": number,
                                "expected_version": row["version"],
                                "received_date": row["received_date"],
                                "new_message_number": candidate})
        if numbering_policy == "current":
            by_id = {row["id"]: row for row in records}
            changes.sort(key=lambda change: (
                by_id[change["id"]]["received_date"] or "9999-12-31",
                by_id[change["id"]]["received_time"] or "",
                by_id[change["id"]]["created_at"], change["id"]))
            stem = f"MSG-{numbering_year}-"
            # Match create_incoming_request -> crud.generate_number, including
            # deleted rows, SQLite LIKE semantics, gaps and collision checks.
            matching_count = connection.execute(
                "SELECT count(*) FROM tblIncomingRequests WHERE message_number LIKE ?",
                (stem + "%",)).fetchone()[0]
            used = {row["message_number"] for row in records if row["message_number"] is not None}
            for change in changes:
                sequence = matching_count + 1
                while f"{stem}{sequence:04d}" in used:
                    sequence += 1
                change["new_message_number"] = f"{stem}{sequence:04d}"
                used.add(change["new_message_number"])
                matching_count += 1
        plan = {"format": 3 if numbering_policy == "current" else (2 if preserve_legacy else 1), "revision": revision(connection),
                "expected_state_sha256": state_hash(records),
                "selection_rule": "Keep earliest received_date/time, then created_at/id; propose numbers above the year's maximum.",
                "retained": retained, "changes": changes}
        if preserve_legacy:
            plan["legacy_originals"] = originals
            plan["selection_rule"] = "Keep earliest received_date/time, then created_at/id; use unused suffixes and preserve all original group numbers."
        if numbering_policy == "current":
            plan.update(numbering_policy="current", numbering_year=numbering_year)
            plan["selection_rule"] = "Keep earliest record per duplicate group; preserve original numbers; assign current MSG-year registrations in received_date/time, created_at/id order."
        return plan


def apply_plan(database: Path, plan: dict, *, actor_login: str, reason: str, offline: bool = False):
    if not offline:
        raise ValueError("Stop all application/import writers and specify --offline.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A reviewed correction reason is required.")
    if not isinstance(plan, dict) or plan.get("format") not in {1, 2, 3} or not isinstance(plan.get("changes"), list) or not plan["changes"]:
        raise ValueError("A nonempty reviewed version-1, version-2 or version-3 plan is required.")
    with closing(connect(database, writable=True)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            records = rows(connection)
            if revision(connection) != plan.get("revision") or state_hash(records) != plan.get("expected_state_sha256"):
                raise ValueError("Database revision or incoming records changed; prepare and review a new plan.")
            if plan["format"] == 3:
                expected = prepare_plan(database, numbering_policy="current", numbering_year=plan.get("numbering_year"))
                if plan != expected:
                    raise ValueError("The reviewed current-number plan does not match the registration rules.")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Resolve foreign key violations before number repair.")
            actor = connection.execute("""SELECT id FROM tblUsers WHERE login=?
                AND is_active=1 AND is_deleted=0""", (actor_login,)).fetchone()
            if actor is None:
                raise ValueError("The audit actor must be an existing active user.")
            by_id = {row["id"]: row for row in records}
            counts = defaultdict(int)
            for row in records:
                if row["message_number"] is not None:
                    counts[row["message_number"]] += 1
            proposed = {row["id"]: row["message_number"] for row in records}
            seen = set()
            for change in plan["changes"]:
                if not isinstance(change, dict) or not isinstance(change.get("id"), str):
                    raise ValueError("Every reviewed change must identify a record.")
                record_id = change.get("id")
                original = by_id.get(record_id)
                new = change.get("new_message_number")
                if original is None or record_id in seen or counts[original["message_number"]] < 2:
                    raise ValueError("Plan must change distinct records from duplicate groups only.")
                if change.get("expected_message_number") != original["message_number"] or change.get("expected_version") != original["version"]:
                    raise ValueError("Plan contains an incorrect original number or row version.")
                if not isinstance(new, str) or not new.strip() or len(new) > 50 or any(ord(c) < 32 for c in new) or new == original["message_number"]:
                    raise ValueError("Every proposed replacement must be a valid, different number.")
                seen.add(record_id)
                proposed[record_id] = new
            final_numbers = [n for n in proposed.values() if n is not None]
            if len(final_numbers) != len(set(final_numbers)):
                raise ValueError("The reviewed plan must resolve all duplicates without creating new ones.")
            if len(plan["changes"]) != sum(count - 1 for count in counts.values() if count > 1):
                raise ValueError("Keep one original number per group; do not renumber the entire group.")
            retained = plan.get("retained")
            expected_retained = {(r["id"], r["message_number"]) for r in records
                                 if counts[r["message_number"]] > 1 and r["id"] not in seen}
            if not isinstance(retained, list) or not all(
                isinstance(r, dict) and isinstance(r.get("id"), str) and isinstance(r.get("message_number"), str)
                for r in retained
            ) or {
                (r.get("id"), r.get("message_number")) for r in retained
            } != expected_retained:
                raise ValueError("The retained original numbers must agree with the reviewed changes.")
            timestamp = datetime.now(timezone.utc).isoformat()
            correlation = str(uuid4())
            preserved = 0
            if plan["format"] in {2, 3}:
                originals = plan.get("legacy_originals")
                expected_originals = {row["id"]: row["message_number"] for row in records if counts[row["message_number"]] > 1}
                if not isinstance(originals, list) or len(originals) != len(expected_originals) or not all(
                    isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("message_number"), str)
                    for item in originals
                ) or {item["id"]: item["message_number"] for item in originals} != expected_originals:
                    raise ValueError("Historical numbers must exactly match every original duplicate-group record.")
                columns = {row[1]: row for row in connection.execute("PRAGMA table_info(tblIncomingRequests)")}
                if "legacy_message_number" not in columns:
                    connection.execute('ALTER TABLE tblIncomingRequests ADD COLUMN legacy_message_number VARCHAR(50)')
                elif columns["legacy_message_number"][2].upper() != "VARCHAR(50)" or columns["legacy_message_number"][3]:
                    raise ValueError("Review the existing historical-number column before repair.")
                for item in originals:
                    original = by_id[item["id"]]
                    previous = original["legacy_message_number"] if "legacy_message_number" in original.keys() else None
                    if previous is not None and previous != item["message_number"]:
                        raise ValueError("An existing historical number must never be overwritten.")
                    if previous is not None:
                        continue
                    connection.execute("UPDATE tblIncomingRequests SET legacy_message_number=? WHERE id=?",
                                       (item["message_number"], item["id"]))
                    connection.execute("""INSERT INTO tblAuditTrail
                        (id, entity_type, entity_id, action, field_name, old_value, new_value,
                         change_reason, user_id, changed_by, timestamp, changed_at,
                         source_module, comment, correlation_id, created_at, updated_at,
                         is_active, is_deleted, version)
                        VALUES (?, 'IncomingRequest', ?, 'preserve_legacy_number', 'legacy_message_number',
                                NULL, ?, ?, ?, ?, ?, ?, 'Incoming Requests', ?, ?, ?, ?, 1, 0, 1)""", (
                        str(uuid4()), item["id"], item["message_number"], reason.strip(), actor["id"], actor["id"],
                        timestamp, timestamp, reason.strip(), correlation, timestamp, timestamp))
                    preserved += 1
            for change in plan["changes"]:
                result = connection.execute("""UPDATE tblIncomingRequests
                    SET message_number=?, version=version+1, updated_at=?, updated_by=?
                    WHERE id=? AND message_number=? AND version=?""", (
                    change["new_message_number"], timestamp, actor["id"], change["id"],
                    change["expected_message_number"], change["expected_version"]))
                if result.rowcount != 1:
                    raise ValueError("Concurrent record change; no correction was committed.")
                connection.execute("""INSERT INTO tblAuditTrail
                    (id, entity_type, entity_id, action, field_name, old_value, new_value,
                     change_reason, user_id, changed_by, timestamp, changed_at,
                     source_module, comment, correlation_id, created_at, updated_at,
                     is_active, is_deleted, version)
                    VALUES (?, 'IncomingRequest', ?, ?, 'message_number', ?, ?, ?, ?, ?,
                            ?, ?, 'Incoming Requests', ?, ?, ?, ?, 1, 0, 1)""", (
                    str(uuid4()), change["id"], ACTION, change["expected_message_number"],
                    change["new_message_number"], reason.strip(), actor["id"], actor["id"],
                    timestamp, timestamp, reason.strip(), correlation, timestamp, timestamp))
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Foreign key violation after repair; no correction was committed.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"changed_records": len(plan["changes"]), "preserved_original_numbers": preserved,
            "appended_audit_events": len(plan["changes"]) + preserved,
            "correlation_id": correlation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "apply"])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--actor-login")
    parser.add_argument("--reason")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--preserve-legacy", action="store_true", help="Prepare a version-2 plan with suffixes and historical numbers.")
    parser.add_argument("--numbering-policy", choices=["legacy", "current"], default="legacy",
                        help="Current uses MSG-year numbering and always preserves historical numbers.")
    parser.add_argument("--numbering-year", type=int, help="Explicit registration year for the current policy.")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            plan = prepare_plan(args.database, preserve_legacy=args.preserve_legacy,
                                numbering_policy=args.numbering_policy, numbering_year=args.numbering_year)
            # Refuse to overwrite a previously reviewed proposal.
            with args.plan.open("x", encoding="utf-8") as stream:
                json.dump(plan, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            result = {"proposed_changes": len(plan["changes"]), "retained_numbers": len(plan["retained"])}
        else:
            if not args.actor_login:
                raise ValueError("--actor-login is required for application.")
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            result = apply_plan(args.database, plan, actor_login=args.actor_login,
                                reason=args.reason, offline=args.offline)
    except (ValueError, OSError, sqlite3.Error) as exc:
        # SQLite can include business values in an error, so only ValueError is
        # safe to show. The transaction has already been rolled back.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        parser.exit(1, f"Number repair refused: {message}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
