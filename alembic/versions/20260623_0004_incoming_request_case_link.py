"""incoming request case link

Revision ID: 20260623_0004
Revises: 20260622_0003
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260623_0004"
down_revision = "20260622_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("tblIncomingRequests")}
    if "case_id" not in columns:
        op.add_column(
            "tblIncomingRequests",
            sa.Column("case_id", sa.String(length=36), nullable=True),
        )
    indexes = {index["name"] for index in inspector.get_indexes("tblIncomingRequests")}
    if "ix_incoming_request_case" not in indexes:
        op.create_index(
            "ix_incoming_request_case",
            "tblIncomingRequests",
            ["case_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("tblIncomingRequests")}
    if "ix_incoming_request_case" in indexes:
        op.drop_index("ix_incoming_request_case", table_name="tblIncomingRequests")
    columns = {column["name"] for column in inspector.get_columns("tblIncomingRequests")}
    if "case_id" in columns:
        with op.batch_alter_table("tblIncomingRequests", recreate="always") as batch_op:
            batch_op.drop_column("case_id")
