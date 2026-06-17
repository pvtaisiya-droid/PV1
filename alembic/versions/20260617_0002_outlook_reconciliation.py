"""outlook reconciliation integration

Revision ID: 20260617_0002
Revises: 20260616_0001
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260617_0002"
down_revision = "20260616_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tblContractContacts",
        sa.Column("is_pv_contact", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "tblContractContacts",
        sa.Column(
            "is_reconciliation_recipient",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "tblContractContacts",
        sa.Column("cc_reconciliation", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "tblContractContacts",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "tblContractContacts",
        sa.Column("contact_type", sa.String(length=100), nullable=True, server_default="pv"),
    )
    op.add_column("tblContractContacts", sa.Column("comments", sa.Text(), nullable=True))

    op.add_column(
        "tblPartnerReconciliations",
        sa.Column(
            "reconciliation_type",
            sa.String(length=100),
            nullable=False,
            server_default="periodic",
        ),
    )
    op.add_column(
        "tblPartnerReconciliations",
        sa.Column("document_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "tblPartnerReconciliations",
        sa.Column("document_filename", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "tblPartnerReconciliations",
        sa.Column("document_format", sa.String(length=20), nullable=True, server_default="xlsx"),
    )
    op.add_column(
        "tblPartnerReconciliations",
        sa.Column("email_subject", sa.String(length=500), nullable=True),
    )
    op.add_column("tblPartnerReconciliations", sa.Column("email_body", sa.Text(), nullable=True))
    op.add_column("tblPartnerReconciliations", sa.Column("email_to", sa.Text(), nullable=True))
    op.add_column("tblPartnerReconciliations", sa.Column("email_cc", sa.Text(), nullable=True))
    op.add_column(
        "tblPartnerReconciliations",
        sa.Column("outlook_message_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "tblPartnerReconciliations",
        sa.Column("outlook_draft_web_link", sa.String(length=1000), nullable=True),
    )
    op.add_column(
        "tblPartnerReconciliations",
        sa.Column(
            "outlook_status",
            sa.String(length=50),
            nullable=False,
            server_default="not_created",
        ),
    )
    op.add_column("tblPartnerReconciliations", sa.Column("outlook_error", sa.Text(), nullable=True))
    op.add_column(
        "tblPartnerReconciliations",
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tblPartnerReconciliations",
        sa.Column("draft_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tblPartnerReconciliations",
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("tblPartnerReconciliations", sa.Column("comments", sa.Text(), nullable=True))

    op.add_column(
        "tblPartnerReconciliationItems",
        sa.Column("discrepancy_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "tblPartnerReconciliationItems",
        sa.Column("discrepancy_comment", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tblPartnerReconciliationItems", "discrepancy_comment")
    op.drop_column("tblPartnerReconciliationItems", "discrepancy_flag")

    for column_name in [
        "comments",
        "sent_at",
        "draft_created_at",
        "generated_at",
        "outlook_error",
        "outlook_status",
        "outlook_draft_web_link",
        "outlook_message_id",
        "email_cc",
        "email_to",
        "email_body",
        "email_subject",
        "document_format",
        "document_filename",
        "document_path",
        "reconciliation_type",
    ]:
        op.drop_column("tblPartnerReconciliations", column_name)

    for column_name in [
        "comments",
        "contact_type",
        "is_primary",
        "cc_reconciliation",
        "is_reconciliation_recipient",
        "is_pv_contact",
    ]:
        op.drop_column("tblContractContacts", column_name)
