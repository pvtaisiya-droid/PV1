"""add partner onboarding wizard

Revision ID: 20260720_0014
Revises: 20260704_0013
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260720_0014"
down_revision = "20260704_0013"
branch_labels = None
depends_on = None


def common_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(length=36), nullable=True),
        sa.Column("delete_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("tblPartnerOnboardingWizards"):
        op.create_table(
            "tblPartnerOnboardingWizards",
            *common_columns(),
            sa.Column("partner_id", sa.String(length=36), nullable=True),
            sa.Column("current_step", sa.Integer(), server_default=sa.text("1"), nullable=False),
            sa.Column("completed_steps", sa.Text(), server_default="", nullable=False),
            sa.Column("status", sa.String(length=50), server_default="draft", nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(["partner_id"], ["tblPartners.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["tblUsers.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    op.create_index("ix_partner_onboarding_partner", "tblPartnerOnboardingWizards", ["partner_id"], if_not_exists=True)
    op.create_index("ix_partner_onboarding_status", "tblPartnerOnboardingWizards", ["status"], if_not_exists=True)
    op.create_index("ix_partner_onboarding_user", "tblPartnerOnboardingWizards", ["user_id"], if_not_exists=True)

    if not inspector.has_table("tblPartnerProductRegistrationLinks"):
        op.create_table(
            "tblPartnerProductRegistrationLinks",
            *common_columns(),
            sa.Column("partner_id", sa.String(length=36), nullable=False),
            sa.Column("product_id", sa.String(length=36), nullable=False),
            sa.Column("registration_certificate_id", sa.String(length=36), nullable=True),
            sa.Column("contract_id", sa.String(length=36), nullable=True),
            sa.Column("partner_role", sa.String(length=100), server_default="mah", nullable=False),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("updated_by", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(["partner_id"], ["tblPartners.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["tblProducts.id"]),
            sa.ForeignKeyConstraint(["registration_certificate_id"], ["tblProductRegistrationCertificates.id"]),
            sa.ForeignKeyConstraint(["contract_id"], ["tblContracts.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["tblUsers.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["tblUsers.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "partner_id",
                "product_id",
                "registration_certificate_id",
                "contract_id",
                "partner_role",
                name="uq_partner_product_registration_role",
            ),
        )
    op.create_index(
        "ix_partner_product_registration_partner",
        "tblPartnerProductRegistrationLinks",
        ["partner_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_partner_product_registration_product",
        "tblPartnerProductRegistrationLinks",
        ["product_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_partner_product_registration_certificate",
        "tblPartnerProductRegistrationLinks",
        ["registration_certificate_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_partner_product_registration_contract",
        "tblPartnerProductRegistrationLinks",
        ["contract_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("tblPartnerProductRegistrationLinks"):
        op.drop_index("ix_partner_product_registration_contract", table_name="tblPartnerProductRegistrationLinks")
        op.drop_index("ix_partner_product_registration_certificate", table_name="tblPartnerProductRegistrationLinks")
        op.drop_index("ix_partner_product_registration_product", table_name="tblPartnerProductRegistrationLinks")
        op.drop_index("ix_partner_product_registration_partner", table_name="tblPartnerProductRegistrationLinks")
        op.drop_table("tblPartnerProductRegistrationLinks")
    if inspector.has_table("tblPartnerOnboardingWizards"):
        op.drop_index("ix_partner_onboarding_user", table_name="tblPartnerOnboardingWizards")
        op.drop_index("ix_partner_onboarding_status", table_name="tblPartnerOnboardingWizards")
        op.drop_index("ix_partner_onboarding_partner", table_name="tblPartnerOnboardingWizards")
        op.drop_table("tblPartnerOnboardingWizards")
