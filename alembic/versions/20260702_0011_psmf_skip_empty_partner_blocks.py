"""add psmf empty partner block skip setting

Revision ID: 20260702_0011
Revises: 20260625_0010
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260702_0011"
down_revision = "20260625_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("psmf_settings"):
        return
    columns = {column["name"] for column in inspector.get_columns("psmf_settings")}
    if "skip_empty_partner_specific_blocks" not in columns:
        op.add_column(
            "psmf_settings",
            sa.Column(
                "skip_empty_partner_specific_blocks",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("psmf_settings"):
        return
    columns = {column["name"] for column in inspector.get_columns("psmf_settings")}
    if "skip_empty_partner_specific_blocks" in columns:
        op.drop_column("psmf_settings", "skip_empty_partner_specific_blocks")
