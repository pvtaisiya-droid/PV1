"""contract additional agreements

Revision ID: 20260622_0003
Revises: 20260617_0002
Create Date: 2026-06-22
"""

from alembic import op
import sqlalchemy as sa


revision = "20260622_0003"
down_revision = "20260617_0002"
branch_labels = None
depends_on = None


def _table_columns(inspector) -> set[str]:
    return {column["name"] for column in inspector.get_columns("tblContracts")}


def _unique_constraint_name(inspector) -> str | None:
    for constraint in inspector.get_unique_constraints("tblContracts"):
        if constraint.get("column_names") == ["partner_id", "product_id"]:
            return constraint.get("name")
    return None


def _has_index(inspector, index_name: str) -> bool:
    return any(index.get("name") == index_name for index in inspector.get_indexes("tblContracts"))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = _table_columns(inspector)
    unique_name = _unique_constraint_name(inspector)
    needs_parent_column = "parent_contract_id" not in columns

    if needs_parent_column:
        op.add_column(
            "tblContracts",
            sa.Column("parent_contract_id", sa.String(length=36), nullable=True),
        )

    if unique_name:
        with op.batch_alter_table("tblContracts", recreate="always") as batch_op:
            batch_op.drop_constraint(unique_name, type_="unique")

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "ix_contract_parent_contract"):
        op.create_index(
            "ix_contract_parent_contract",
            "tblContracts",
            ["parent_contract_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = _table_columns(inspector)

    if _has_index(inspector, "ix_contract_parent_contract"):
        op.drop_index("ix_contract_parent_contract", table_name="tblContracts")

    if "parent_contract_id" in columns:
        with op.batch_alter_table("tblContracts", recreate="always") as batch_op:
            batch_op.drop_column("parent_contract_id")
            batch_op.create_unique_constraint(
                "uq_contract_partner_product",
                ["partner_id", "product_id"],
            )
