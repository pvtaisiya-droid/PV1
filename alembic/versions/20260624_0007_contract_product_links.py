"""contract product links

Revision ID: 20260624_0007
Revises: 20260624_0006
Create Date: 2026-06-24
"""

from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "20260624_0007"
down_revision = "20260624_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("tblContractProducts"):
        op.create_table(
            "tblContractProducts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_by", sa.String(length=36), nullable=True),
            sa.Column("delete_reason", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("contract_id", sa.String(length=36), nullable=False),
            sa.Column("product_id", sa.String(length=36), nullable=False),
            sa.ForeignKeyConstraint(["contract_id"], ["tblContracts.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["tblProducts.id"]),
            sa.UniqueConstraint("contract_id", "product_id", name="uq_contract_product"),
        )
        op.create_index(
            "ix_contract_product_contract",
            "tblContractProducts",
            ["contract_id"],
        )
        op.create_index(
            "ix_contract_product_product",
            "tblContractProducts",
            ["product_id"],
        )

    contract_products = sa.table(
        "tblContractProducts",
        sa.column("id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("is_active", sa.Boolean),
        sa.column("is_deleted", sa.Boolean),
        sa.column("version", sa.Integer),
        sa.column("contract_id", sa.String),
        sa.column("product_id", sa.String),
    )
    existing_links = {
        (row.contract_id, row.product_id)
        for row in bind.execute(
            sa.text('SELECT contract_id, product_id FROM "tblContractProducts"')
        )
    }
    rows = []
    now = datetime.now(timezone.utc)
    for contract in bind.execute(
        sa.text(
            """
            SELECT id, product_id, created_at, updated_at
            FROM "tblContracts"
            WHERE product_id IS NOT NULL
            """
        )
    ).mappings():
        key = (contract["id"], contract["product_id"])
        if key in existing_links:
            continue
        rows.append(
            {
                "id": str(uuid4()),
                "created_at": contract["created_at"] or now,
                "updated_at": contract["updated_at"] or now,
                "is_active": True,
                "is_deleted": False,
                "version": 1,
                "contract_id": contract["id"],
                "product_id": contract["product_id"],
            }
        )
    if rows:
        op.bulk_insert(contract_products, rows)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("tblContractProducts"):
        op.drop_table("tblContractProducts")
