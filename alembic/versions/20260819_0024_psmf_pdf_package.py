"""add PSMF PDF and package paths

Revision ID: 20260819_0024
Revises: 20260819_0023
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa


revision = "20260819_0024"
down_revision = "20260819_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("psmf_partner_builds"):
        return
    columns = {column["name"] for column in inspector.get_columns("psmf_partner_builds")}
    if "pdf_file_path" not in columns:
        op.add_column(
            "psmf_partner_builds",
            sa.Column("pdf_file_path", sa.String(length=500), nullable=True),
        )
    if "package_file_path" not in columns:
        op.add_column(
            "psmf_partner_builds",
            sa.Column("package_file_path", sa.String(length=500), nullable=True),
        )
    if inspector.has_table("psmf_settings"):
        op.execute(sa.text("UPDATE psmf_settings SET enable_pdf_export = 1"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("psmf_partner_builds"):
        return
    columns = {column["name"] for column in inspector.get_columns("psmf_partner_builds")}
    if "package_file_path" in columns:
        op.drop_column("psmf_partner_builds", "package_file_path")
    if "pdf_file_path" in columns:
        op.drop_column("psmf_partner_builds", "pdf_file_path")
