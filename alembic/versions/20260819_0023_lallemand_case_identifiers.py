"""move LALLEMAND identifiers out of internal case numbers

Revision ID: 20260819_0023
Revises: 20260818_0022
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import uuid

from alembic import op
import sqlalchemy as sa


revision = "20260819_0023"
down_revision = "20260818_0022"
branch_labels = None
depends_on = None


COMPOSITE_NUMBER = re.compile(
    r"^(?P<case_number>\d{4}-\d{3,})\s*;(?P<identifiers>[\s\S]+)$"
)
PARTNER_IDENTIFIER = re.compile(r"^PV[A-Z0-9]+$", re.IGNORECASE)
MIGRATION_REASON = (
    "LALLEMAND identifiers were moved from the internal case number "
    "to additional case numbers."
)


def _identifier_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for raw_token in re.split(r"[;\r\n]+", value):
        token = raw_token.strip()
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _insert_audit(
    bind,
    *,
    entity_type: str,
    entity_id: str,
    case_id: str,
    action: str,
    field_name: str,
    old_value: str | None,
    new_value: str | None,
    comment: str,
    now: datetime,
) -> None:
    bind.execute(
        sa.text(
            'INSERT INTO "tblAuditTrail" '
            '(id, created_at, updated_at, is_active, is_deleted, version, '
            'entity_type, entity_id, case_id, action, field_name, old_value, '
            'new_value, change_reason, timestamp, changed_at, source_module, comment) '
            'VALUES (:id, :now, :now, 1, 0, 1, :entity_type, :entity_id, '
            ':case_id, :action, :field_name, :old_value, :new_value, '
            ':change_reason, :now, :now, :source_module, :comment)'
        ),
        {
            "id": str(uuid.uuid4()),
            "now": now,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "case_id": case_id,
            "action": action,
            "field_name": field_name,
            "old_value": old_value,
            "new_value": new_value,
            "change_reason": MIGRATION_REASON,
            "source_module": "ICSRs",
            "comment": comment,
        },
    )


def _reserve_counter(bind, *, partner_id: str, year: int, sequence: int, now: datetime) -> None:
    scope = f"PARTNER:{partner_id}"
    counter = bind.execute(
        sa.text(
            'SELECT id, last_value FROM "tblCaseNumberCounters" '
            'WHERE scope_key = :scope AND numbering_year = :year'
        ),
        {"scope": scope, "year": year},
    ).mappings().first()
    if counter is None:
        bind.execute(
            sa.text(
                'INSERT INTO "tblCaseNumberCounters" '
                '(id, created_at, updated_at, is_active, is_deleted, version, '
                'scope_key, numbering_year, last_value) '
                'VALUES (:id, :now, :now, 1, 0, 1, :scope, :year, :sequence)'
            ),
            {
                "id": str(uuid.uuid4()),
                "now": now,
                "scope": scope,
                "year": year,
                "sequence": sequence,
            },
        )
    elif int(counter["last_value"]) < sequence:
        bind.execute(
            sa.text(
                'UPDATE "tblCaseNumberCounters" SET last_value = :sequence, '
                'updated_at = :now, version = version + 1 WHERE id = :id'
            ),
            {"id": counter["id"], "sequence": sequence, "now": now},
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    required_tables = {
        "tblPartners",
        "tblCases",
        "tblCaseIdentifiers",
        "tblCaseNumberCounters",
        "tblAuditTrail",
    }
    if not required_tables.issubset(set(inspector.get_table_names())):
        return

    partners = bind.execute(
        sa.text(
            'SELECT id, partner_name FROM "tblPartners" '
            'WHERE COALESCE(is_deleted, 0) = 0'
        )
    ).mappings().all()
    lallemand_partners = [
        row for row in partners if "LALLEMAND" in (row["partner_name"] or "").upper()
    ]
    now = datetime.now(timezone.utc)

    for partner in lallemand_partners:
        partner_id = partner["id"]
        cases = bind.execute(
            sa.text(
                'SELECT id, case_number FROM "tblCases" '
                'WHERE COALESCE(is_deleted, 0) = 0 '
                'AND (partner_id = :partner_id OR numbering_partner_id = :partner_id)'
            ),
            {"partner_id": partner_id},
        ).mappings().all()

        for case_row in cases:
            original_number = (case_row["case_number"] or "").strip()
            match = COMPOSITE_NUMBER.fullmatch(original_number)
            if not match:
                continue
            case_number = match.group("case_number")
            tokens = _identifier_tokens(match.group("identifiers"))
            if not tokens:
                continue
            year_text, sequence_text = case_number.split("-", 1)
            year = int(year_text)
            sequence = int(sequence_text)
            scope = f"PARTNER:{partner_id}"

            number_conflict = bind.execute(
                sa.text(
                    'SELECT id FROM "tblCases" WHERE id <> :case_id '
                    'AND COALESCE(is_deleted, 0) = 0 AND case_number = :case_number '
                    'AND (numbering_partner_id = :partner_id OR '
                    '(numbering_partner_id IS NULL AND partner_id = :partner_id)) '
                    'LIMIT 1'
                ),
                {
                    "case_id": case_row["id"],
                    "case_number": case_number,
                    "partner_id": partner_id,
                },
            ).first()
            structured_conflict = bind.execute(
                sa.text(
                    'SELECT id FROM "tblCases" WHERE id <> :case_id '
                    'AND numbering_scope = :scope AND numbering_year = :year '
                    'AND numbering_sequence = :sequence LIMIT 1'
                ),
                {
                    "case_id": case_row["id"],
                    "scope": scope,
                    "year": year,
                    "sequence": sequence,
                },
            ).first()
            if number_conflict or structured_conflict:
                raise RuntimeError(
                    f"Cannot normalize {original_number!r}: {case_number!r} is already used."
                )

            existing_identifiers = bind.execute(
                sa.text(
                    'SELECT identifier_value, is_primary FROM "tblCaseIdentifiers" '
                    'WHERE case_id = :case_id AND COALESCE(is_deleted, 0) = 0'
                ),
                {"case_id": case_row["id"]},
            ).mappings().all()
            existing_values = {row["identifier_value"] for row in existing_identifiers}
            has_primary = any(bool(row["is_primary"]) for row in existing_identifiers)
            partner_tokens = [token for token in tokens if PARTNER_IDENTIFIER.fullmatch(token)]
            primary_token = partner_tokens[0] if partner_tokens else tokens[0]

            for token in tokens:
                if token in existing_values:
                    continue
                is_partner_number = bool(PARTNER_IDENTIFIER.fullmatch(token))
                identifier_id = str(uuid.uuid4())
                is_primary = not has_primary and token == primary_token
                bind.execute(
                    sa.text(
                        'INSERT INTO "tblCaseIdentifiers" '
                        '(id, created_at, updated_at, is_active, is_deleted, version, '
                        'case_id, identifier_value, partner_id, organization_name, '
                        'identifier_type, is_primary) '
                        'VALUES (:id, :now, :now, 1, 0, 1, :case_id, :value, '
                        ':identifier_partner_id, :organization_name, :identifier_type, '
                        ':is_primary)'
                    ),
                    {
                        "id": identifier_id,
                        "now": now,
                        "case_id": case_row["id"],
                        "value": token,
                        "identifier_partner_id": partner_id if is_partner_number else None,
                        "organization_name": None if is_partner_number else "Партнёр LALLEMAND",
                        "identifier_type": (
                            "partner_case_number"
                            if is_partner_number
                            else "partner_of_partner_case_number"
                        ),
                        "is_primary": is_primary,
                    },
                )
                has_primary = has_primary or is_primary
                _insert_audit(
                    bind,
                    entity_type="CaseIdentifier",
                    entity_id=identifier_id,
                    case_id=case_row["id"],
                    action="lallemand_identifier_migrated",
                    field_name="identifier_value",
                    old_value=None,
                    new_value=token,
                    comment="Additional LALLEMAND case number created from the legacy composite number.",
                    now=now,
                )

            _reserve_counter(
                bind,
                partner_id=partner_id,
                year=year,
                sequence=sequence,
                now=now,
            )
            bind.execute(
                sa.text(
                    'UPDATE "tblCases" SET case_number = :case_number, '
                    'numbering_partner_id = :partner_id, numbering_scope = :scope, '
                    'numbering_year = :year, numbering_sequence = :sequence, '
                    'number_is_provisional = 0, updated_at = :now, version = version + 1 '
                    'WHERE id = :case_id'
                ),
                {
                    "case_id": case_row["id"],
                    "case_number": case_number,
                    "partner_id": partner_id,
                    "scope": scope,
                    "year": year,
                    "sequence": sequence,
                    "now": now,
                },
            )
            _insert_audit(
                bind,
                entity_type="Case",
                entity_id=case_row["id"],
                case_id=case_row["id"],
                action="lallemand_number_normalized",
                field_name="case_number",
                old_value=original_number,
                new_value=case_number,
                comment="Internal case number normalized; partner numbers retained separately.",
                now=now,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tblAuditTrail" not in inspector.get_table_names():
        return
    normalized_rows = bind.execute(
        sa.text(
            'SELECT id, case_id, old_value, new_value FROM "tblAuditTrail" '
            'WHERE action = :action AND field_name = :field_name '
            'ORDER BY changed_at DESC'
        ),
        {"action": "lallemand_number_normalized", "field_name": "case_number"},
    ).mappings().all()
    for row in normalized_rows:
        bind.execute(
            sa.text(
                'UPDATE "tblCases" SET case_number = :old_value, '
                'numbering_scope = NULL, numbering_year = NULL, '
                'numbering_sequence = NULL, version = version + 1 '
                'WHERE id = :case_id AND case_number = :new_value'
            ),
            {
                "case_id": row["case_id"],
                "old_value": row["old_value"],
                "new_value": row["new_value"],
            },
        )
        identifier_audits = bind.execute(
            sa.text(
                'SELECT id, entity_id FROM "tblAuditTrail" '
                'WHERE case_id = :case_id AND action = :action'
            ),
            {
                "case_id": row["case_id"],
                "action": "lallemand_identifier_migrated",
            },
        ).mappings().all()
        for audit in identifier_audits:
            bind.execute(
                sa.text('DELETE FROM "tblCaseIdentifiers" WHERE id = :id'),
                {"id": audit["entity_id"]},
            )
            bind.execute(
                sa.text('DELETE FROM "tblAuditTrail" WHERE id = :id'),
                {"id": audit["id"]},
            )
        bind.execute(
            sa.text('DELETE FROM "tblAuditTrail" WHERE id = :id'),
            {"id": row["id"]},
        )
