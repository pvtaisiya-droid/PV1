"""add multiple product links to safety messages

Revision ID: 20260814_0019
Revises: 20260814_0018
Create Date: 2026-08-14
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision = "20260814_0019"
down_revision = "20260814_0018"
branch_labels = None
depends_on = None


LINK_TABLES = (
    (
        "tblSafetyReportProducts",
        "safety_report_id",
        "tblSafetyReports.id",
        "safety_report_product",
    ),
    (
        "tblIncomingRequestProducts",
        "incoming_request_id",
        "tblIncomingRequests.id",
        "incoming_request_product",
    ),
)


def create_link_table(
    table_name: str,
    parent_column: str,
    parent_reference: str,
    name_prefix: str,
) -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(table_name):
        return
    op.create_table(
        table_name,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(length=36), nullable=True),
        sa.Column("delete_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(parent_column, sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=True),
        sa.Column("reported_product_name", sa.String(length=255), nullable=True),
        sa.Column("drug_role", sa.String(length=50), nullable=False, server_default="suspect"),
        sa.CheckConstraint(
            "product_id IS NOT NULL OR reported_product_name IS NOT NULL",
            name=f"ck_{name_prefix}_reference",
        ),
        sa.ForeignKeyConstraint([parent_column], [parent_reference]),
        sa.ForeignKeyConstraint(["product_id"], ["tblProducts.id"]),
        sa.UniqueConstraint(
            parent_column,
            "product_id",
            name=f"uq_{name_prefix}",
        ),
    )
    op.create_index(
        f"ix_{name_prefix}_{'report' if parent_column == 'safety_report_id' else 'request'}",
        table_name,
        [parent_column],
    )
    op.create_index(
        f"ix_{name_prefix}_product",
        table_name,
        ["product_id"],
    )


def link_table(table_name: str, parent_column: str) -> sa.TableClause:
    return sa.table(
        table_name,
        sa.column("id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("is_active", sa.Boolean),
        sa.column("is_deleted", sa.Boolean),
        sa.column("version", sa.Integer),
        sa.column(parent_column, sa.String),
        sa.column("product_id", sa.String),
        sa.column("reported_product_name", sa.String),
        sa.column("drug_role", sa.String),
    )


def backfill_links() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    incoming_table = link_table("tblIncomingRequestProducts", "incoming_request_id")
    incoming_existing = {
        (row.incoming_request_id, row.product_id)
        for row in bind.execute(
            sa.text(
                'SELECT incoming_request_id, product_id FROM "tblIncomingRequestProducts" '
                "WHERE product_id IS NOT NULL"
            )
        )
    }
    incoming_rows = []
    for row in bind.execute(
        sa.text(
            """
            SELECT request.id AS incoming_request_id,
                   request.product_id AS product_id,
                   product.product_name AS reported_product_name,
                   request.created_at AS created_at,
                   request.updated_at AS updated_at
            FROM "tblIncomingRequests" AS request
            LEFT JOIN "tblProducts" AS product ON product.id = request.product_id
            WHERE request.product_id IS NOT NULL
            """
        )
    ).mappings():
        key = (row["incoming_request_id"], row["product_id"])
        if key in incoming_existing:
            continue
        incoming_existing.add(key)
        incoming_rows.append(
            {
                "id": str(uuid4()),
                "created_at": row["created_at"] or now,
                "updated_at": row["updated_at"] or now,
                "is_active": True,
                "is_deleted": False,
                "version": 1,
                "incoming_request_id": row["incoming_request_id"],
                "product_id": row["product_id"],
                "reported_product_name": row["reported_product_name"],
                "drug_role": "suspect",
            }
        )
    if incoming_rows:
        op.bulk_insert(incoming_table, incoming_rows)

    safety_table = link_table("tblSafetyReportProducts", "safety_report_id")
    safety_existing = {
        (
            row.safety_report_id,
            row.product_id,
            (row.reported_product_name or "").strip().casefold(),
        )
        for row in bind.execute(
            sa.text(
                """
                SELECT safety_report_id, product_id, reported_product_name
                FROM "tblSafetyReportProducts"
                """
            )
        )
    }
    safety_rows = []
    for row in bind.execute(
        sa.text(
            """
            SELECT case_row.safety_report_id AS safety_report_id,
                   case_product.product_id AS product_id,
                   COALESCE(case_product.reported_product_name, product.product_name)
                       AS reported_product_name,
                   case_product.drug_role AS drug_role,
                   case_product.created_at AS created_at,
                   case_product.updated_at AS updated_at
            FROM "tblCases" AS case_row
            JOIN "tblCaseProducts" AS case_product ON case_product.case_id = case_row.id
            LEFT JOIN "tblProducts" AS product ON product.id = case_product.product_id
            WHERE case_row.safety_report_id IS NOT NULL
              AND (case_product.product_id IS NOT NULL
                   OR case_product.reported_product_name IS NOT NULL)
            """
        )
    ).mappings():
        name_key = (row["reported_product_name"] or "").strip().casefold()
        key = (row["safety_report_id"], row["product_id"], name_key)
        catalog_key_exists = any(
            existing[0] == row["safety_report_id"]
            and existing[1] == row["product_id"]
            and row["product_id"] is not None
            for existing in safety_existing
        )
        if key in safety_existing or catalog_key_exists:
            continue
        safety_existing.add(key)
        safety_rows.append(
            {
                "id": str(uuid4()),
                "created_at": row["created_at"] or now,
                "updated_at": row["updated_at"] or now,
                "is_active": True,
                "is_deleted": False,
                "version": 1,
                "safety_report_id": row["safety_report_id"],
                "product_id": row["product_id"],
                "reported_product_name": row["reported_product_name"],
                "drug_role": row["drug_role"] or "suspect",
            }
        )
    if safety_rows:
        op.bulk_insert(safety_table, safety_rows)


def upgrade() -> None:
    for table_name, parent_column, parent_reference, name_prefix in LINK_TABLES:
        create_link_table(table_name, parent_column, parent_reference, name_prefix)
    backfill_links()


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name, *_ in reversed(LINK_TABLES):
        if inspector.has_table(table_name):
            op.drop_table(table_name)
