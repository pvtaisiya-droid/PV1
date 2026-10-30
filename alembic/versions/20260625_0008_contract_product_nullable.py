"""allow contracts without a primary product

Revision ID: 20260625_0008
Revises: 20260624_0007
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260625_0008"
down_revision = "20260624_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tblContracts", recreate="always") as batch_op:
        batch_op.alter_column(
            "product_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE "tblContracts"
            SET product_id = (
                SELECT product_id
                FROM "tblContractProducts"
                WHERE "tblContractProducts".contract_id = "tblContracts".id
                  AND "tblContractProducts".is_deleted = 0
                LIMIT 1
            )
            WHERE product_id IS NULL
            """
        )
    )
    with op.batch_alter_table("tblContracts", recreate="always") as batch_op:
        batch_op.alter_column(
            "product_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
