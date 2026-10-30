"""partner-scoped annual case numbering

Revision ID: 20260818_0022
Revises: 20260818_0021
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import re
import uuid

from alembic import op
import sqlalchemy as sa


revision = "20260818_0022"
down_revision = "20260818_0021"
branch_labels = None
depends_on = None


EXACT_NUMBER = re.compile(r"^(\d{4})-(\d{3,})$")
EXACT_UNASSIGNED = re.compile(r"^UNASSIGNED-(\d{4})-(\d{3,})$")
LEADING_NUMBER = re.compile(r"^(?:UNASSIGNED-)?(\d{4})-(\d{3,})")


def common_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(length=36), nullable=True),
        sa.Column("delete_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {row["name"]: row for row in inspector.get_indexes("tblCases")}
    case_number_index = indexes.get("ix_tblCases_case_number")
    if case_number_index and case_number_index.get("unique"):
        op.drop_index("ix_tblCases_case_number", table_name="tblCases")

    existing_columns = {row["name"] for row in inspector.get_columns("tblCases")}
    existing_unique_constraints = {
        row["name"] for row in inspector.get_unique_constraints("tblCases")
    }
    required_case_columns = {
        "numbering_partner_id",
        "numbering_scope",
        "numbering_year",
        "numbering_sequence",
        "number_is_provisional",
    }
    if (
        not required_case_columns.issubset(existing_columns)
        or "uq_case_number_scope_year_sequence" not in existing_unique_constraints
    ):
        with op.batch_alter_table("tblCases", recreate="always") as batch_op:
            if "numbering_partner_id" not in existing_columns:
                batch_op.add_column(sa.Column("numbering_partner_id", sa.String(36), nullable=True))
                batch_op.create_foreign_key(
                    "fk_case_numbering_partner",
                    "tblPartners",
                    ["numbering_partner_id"],
                    ["id"],
                )
            if "numbering_scope" not in existing_columns:
                batch_op.add_column(sa.Column("numbering_scope", sa.String(64), nullable=True))
            if "numbering_year" not in existing_columns:
                batch_op.add_column(sa.Column("numbering_year", sa.Integer(), nullable=True))
            if "numbering_sequence" not in existing_columns:
                batch_op.add_column(sa.Column("numbering_sequence", sa.Integer(), nullable=True))
            if "number_is_provisional" not in existing_columns:
                batch_op.add_column(
                    sa.Column(
                        "number_is_provisional",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )
            if "uq_case_number_scope_year_sequence" not in existing_unique_constraints:
                batch_op.create_unique_constraint(
                    "uq_case_number_scope_year_sequence",
                    ["numbering_scope", "numbering_year", "numbering_sequence"],
                )

    post_batch_indexes = {
        row["name"]: row for row in sa.inspect(bind).get_indexes("tblCases")
    }
    recreated_case_number_index = post_batch_indexes.get("ix_tblCases_case_number")
    if recreated_case_number_index and recreated_case_number_index.get("unique"):
        op.drop_index("ix_tblCases_case_number", table_name="tblCases")
        recreated_case_number_index = None
    if not recreated_case_number_index:
        op.create_index("ix_tblCases_case_number", "tblCases", ["case_number"], unique=False)
    post_batch_index_names = {
        row["name"] for row in sa.inspect(bind).get_indexes("tblCases")
    }
    requested_case_indexes = {
        "ix_tblCases_numbering_partner_id": ["numbering_partner_id"],
        "ix_tblCases_numbering_scope": ["numbering_scope"],
        "ix_tblCases_numbering_year": ["numbering_year"],
        "ix_tblCases_number_is_provisional": ["number_is_provisional"],
    }
    for index_name, columns in requested_case_indexes.items():
        if index_name not in post_batch_index_names:
            op.create_index(index_name, "tblCases", columns, unique=False)
    post_batch_index_names = {
        row["name"] for row in sa.inspect(bind).get_indexes("tblCases")
    }
    if "ux_case_local_sender_case_id" not in post_batch_index_names:
        op.create_index(
            "ux_case_local_sender_case_id",
            "tblCases",
            ["local_sender_case_id"],
            unique=True,
        )
    if "ux_case_worldwide_case_id" not in post_batch_index_names:
        op.create_index(
            "ux_case_worldwide_case_id",
            "tblCases",
            ["worldwide_case_id"],
            unique=True,
        )

    tables_before_creation = set(sa.inspect(bind).get_table_names())
    if "tblCaseNumberCounters" not in tables_before_creation:
        op.create_table(
        "tblCaseNumberCounters",
        *common_columns(),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("numbering_year", sa.Integer(), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_key",
            "numbering_year",
            name="uq_case_number_counter_scope_year",
        ),
        )
    counter_indexes = {
        row["name"] for row in sa.inspect(bind).get_indexes("tblCaseNumberCounters")
    }
    if "ix_tblCaseNumberCounters_scope_key" not in counter_indexes:
        op.create_index(
        "ix_tblCaseNumberCounters_scope_key",
        "tblCaseNumberCounters",
        ["scope_key"],
        unique=False,
        )
    if "ix_tblCaseNumberCounters_numbering_year" not in counter_indexes:
        op.create_index(
        "ix_tblCaseNumberCounters_numbering_year",
        "tblCaseNumberCounters",
        ["numbering_year"],
        unique=False,
        )

    if "tblCasePartnerLinks" not in tables_before_creation:
        op.create_table(
        "tblCasePartnerLinks",
        *common_columns(),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("partner_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_type", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["tblCases.id"]),
        sa.ForeignKeyConstraint(["partner_id"], ["tblPartners.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "partner_id", name="uq_case_partner_link"),
        )
    partner_link_indexes = {
        row["name"] for row in sa.inspect(bind).get_indexes("tblCasePartnerLinks")
    }
    if "ix_case_partner_link_case" not in partner_link_indexes:
        op.create_index("ix_case_partner_link_case", "tblCasePartnerLinks", ["case_id"])
    if "ix_case_partner_link_partner" not in partner_link_indexes:
        op.create_index("ix_case_partner_link_partner", "tblCasePartnerLinks", ["partner_id"])

    if "tblCaseIdentifiers" not in tables_before_creation:
        op.create_table(
        "tblCaseIdentifiers",
        *common_columns(),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("identifier_value", sa.String(length=255), nullable=False),
        sa.Column("partner_id", sa.String(length=36), nullable=True),
        sa.Column("organization_name", sa.String(length=255), nullable=True),
        sa.Column("identifier_type", sa.String(length=100), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["tblCases.id"]),
        sa.ForeignKeyConstraint(["partner_id"], ["tblPartners.id"]),
        sa.PrimaryKeyConstraint("id"),
        )
    identifier_indexes = {
        row["name"] for row in sa.inspect(bind).get_indexes("tblCaseIdentifiers")
    }
    if "ix_case_identifier_case" not in identifier_indexes:
        op.create_index("ix_case_identifier_case", "tblCaseIdentifiers", ["case_id"])
    if "ix_case_identifier_partner_value" not in identifier_indexes:
        op.create_index(
        "ix_case_identifier_partner_value",
        "tblCaseIdentifiers",
        ["partner_id", "identifier_value"],
        )
    if "ix_tblCaseIdentifiers_identifier_value" not in identifier_indexes:
        op.create_index(
        "ix_tblCaseIdentifiers_identifier_value",
        "tblCaseIdentifiers",
        ["identifier_value"],
        )

    now = datetime.now(timezone.utc)
    rows = bind.execute(
        sa.text(
            'SELECT id, case_number, partner_id FROM "tblCases" '
            'WHERE COALESCE(is_deleted, 0) = 0'
        )
    ).mappings().all()
    exact_keys = []
    for row in rows:
        number = (row["case_number"] or "").strip()
        match = EXACT_NUMBER.fullmatch(number) if row["partner_id"] else (
            EXACT_UNASSIGNED.fullmatch(number) or EXACT_NUMBER.fullmatch(number)
        )
        if match:
            scope = f'PARTNER:{row["partner_id"]}' if row["partner_id"] else "UNASSIGNED"
            exact_keys.append((scope, int(match.group(1)), int(match.group(2))))
    duplicate_exact_keys = {
        key for key, count in Counter(exact_keys).items() if count > 1
    }

    counter_maxima: dict[tuple[str, int], int] = defaultdict(int)
    for row in rows:
        number = (row["case_number"] or "").strip()
        partner_id = row["partner_id"]
        scope = f"PARTNER:{partner_id}" if partner_id else "UNASSIGNED"
        provisional = not bool(partner_id)
        values = {
            "case_id": row["id"],
            "partner_id": partner_id,
            "provisional": provisional,
        }
        exact = EXACT_NUMBER.fullmatch(number) if partner_id else (
            EXACT_UNASSIGNED.fullmatch(number) or EXACT_NUMBER.fullmatch(number)
        )
        if exact:
            year = int(exact.group(1))
            sequence = int(exact.group(2))
            if (scope, year, sequence) not in duplicate_exact_keys:
                values.update(scope=scope, year=year, sequence=sequence)
            counter_maxima[(scope, year)] = max(
                counter_maxima[(scope, year)],
                sequence,
            )
        else:
            leading = LEADING_NUMBER.match(number)
            if leading:
                year = int(leading.group(1))
                sequence = int(leading.group(2))
                counter_maxima[(scope, year)] = max(
                    counter_maxima[(scope, year)],
                    sequence,
                )
        bind.execute(
            sa.text(
                'UPDATE "tblCases" SET '
                'numbering_partner_id = :partner_id, '
                'number_is_provisional = :provisional, '
                'numbering_scope = :scope, '
                'numbering_year = :year, '
                'numbering_sequence = :sequence '
                'WHERE id = :case_id'
            ),
            {
                **values,
                "scope": values.get("scope"),
                "year": values.get("year"),
                "sequence": values.get("sequence"),
            },
        )
        if partner_id:
            bind.execute(
                sa.text(
                    'INSERT INTO "tblCasePartnerLinks" '
                    '(id, created_at, updated_at, is_active, is_deleted, version, '
                    'case_id, partner_id, relationship_type) '
                    'VALUES (:id, :created_at, :updated_at, 1, 0, 1, '
                    ':case_id, :partner_id, :relationship_type)'
                ),
                {
                    "id": str(uuid.uuid4()),
                    "created_at": now,
                    "updated_at": now,
                    "case_id": row["id"],
                    "partner_id": partner_id,
                    "relationship_type": "primary",
                },
            )

    for (scope, year), last_value in counter_maxima.items():
        bind.execute(
            sa.text(
                'INSERT INTO "tblCaseNumberCounters" '
                '(id, created_at, updated_at, is_active, is_deleted, version, '
                'scope_key, numbering_year, last_value) '
                'VALUES (:id, :created_at, :updated_at, 1, 0, 1, '
                ':scope, :year, :last_value)'
            ),
            {
                "id": str(uuid.uuid4()),
                "created_at": now,
                "updated_at": now,
                "scope": scope,
                "year": year,
                "last_value": last_value,
            },
        )


def downgrade() -> None:
    op.drop_table("tblCaseIdentifiers")
    op.drop_table("tblCasePartnerLinks")
    op.drop_table("tblCaseNumberCounters")
    op.drop_index("ix_tblCases_number_is_provisional", table_name="tblCases")
    op.drop_index("ux_case_worldwide_case_id", table_name="tblCases")
    op.drop_index("ux_case_local_sender_case_id", table_name="tblCases")
    op.drop_index("ix_tblCases_numbering_year", table_name="tblCases")
    op.drop_index("ix_tblCases_numbering_scope", table_name="tblCases")
    op.drop_index("ix_tblCases_numbering_partner_id", table_name="tblCases")
    op.drop_index("ix_tblCases_case_number", table_name="tblCases")
    with op.batch_alter_table("tblCases", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_case_number_scope_year_sequence", type_="unique")
        batch_op.drop_column("number_is_provisional")
        batch_op.drop_column("numbering_sequence")
        batch_op.drop_column("numbering_year")
        batch_op.drop_column("numbering_scope")
        batch_op.drop_column("numbering_partner_id")
    op.create_index("ix_tblCases_case_number", "tblCases", ["case_number"], unique=False)
