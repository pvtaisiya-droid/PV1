"""add product comment

Revision ID: 20260625_0009
Revises: 20260625_0008
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260625_0009"
down_revision = "20260625_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("tblProducts")}
    if "comment" not in columns:
        op.add_column("tblProducts", sa.Column("comment", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("tblProducts")}
    if "comment" in columns:
        op.drop_column("tblProducts", "comment")
