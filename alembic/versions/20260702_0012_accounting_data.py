"""add accounting data module

Revision ID: 20260702_0012
Revises: 20260702_0011
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260702_0012"
down_revision = "20260702_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tblAccountingServices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("default_unit", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        "uq_accounting_service_name",
        "tblAccountingServices",
        ["name"],
        unique=True,
        if_not_exists=True,
    )

    op.create_table(
        "tblAccountingDataPeriods",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(length=36), nullable=True),
        sa.Column("delete_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("partner_id", sa.String(length=36), nullable=False),
        sa.Column("contract_id", sa.String(length=36), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("quarter", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("responsible_user_id", sa.String(length=36), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_to_accounting_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_contract_amount", sa.Float(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["contract_id"], ["tblContracts.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["tblUsers.id"]),
        sa.ForeignKeyConstraint(["partner_id"], ["tblPartners.id"]),
        sa.ForeignKeyConstraint(["responsible_user_id"], ["tblUsers.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["tblUsers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("partner_id", "contract_id", "year", "quarter", name="uq_accounting_period_contract_quarter"),
        if_not_exists=True,
    )
    op.create_index("ix_accounting_period_partner", "tblAccountingDataPeriods", ["partner_id"], if_not_exists=True)
    op.create_index("ix_accounting_period_contract", "tblAccountingDataPeriods", ["contract_id"], if_not_exists=True)
    op.create_index("ix_accounting_period_status", "tblAccountingDataPeriods", ["status"], if_not_exists=True)
    op.create_index(
        "ix_accounting_period_year_quarter",
        "tblAccountingDataPeriods",
        ["year", "quarter"],
        if_not_exists=True,
    )

    op.create_table(
        "tblAccountingDataLines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(length=36), nullable=True),
        sa.Column("delete_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("accounting_period_id", sa.String(length=36), nullable=False),
        sa.Column("service_id", sa.String(length=36), nullable=False),
        sa.Column("object_type", sa.String(length=100), nullable=True),
        sa.Column("object_id", sa.String(length=36), nullable=True),
        sa.Column("product_id", sa.String(length=36), nullable=True),
        sa.Column("active_substance_id", sa.String(length=36), nullable=True),
        sa.Column("product_or_inn", sa.String(length=500), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=100), nullable=False),
        sa.Column("contract_price", sa.Float(), nullable=False),
        sa.Column("contract_amount", sa.Float(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["accounting_period_id"], ["tblAccountingDataPeriods.id"]),
        sa.ForeignKeyConstraint(["active_substance_id"], ["tblSubstances.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["tblProducts.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["tblAccountingServices.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_accounting_line_period",
        "tblAccountingDataLines",
        ["accounting_period_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_accounting_line_service",
        "tblAccountingDataLines",
        ["service_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_accounting_line_object",
        "tblAccountingDataLines",
        ["object_type", "object_id"],
        if_not_exists=True,
    )

    op.create_table(
        "tblContractAccountingTariffs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(length=36), nullable=True),
        sa.Column("delete_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("partner_id", sa.String(length=36), nullable=False),
        sa.Column("contract_id", sa.String(length=36), nullable=False),
        sa.Column("service_id", sa.String(length=36), nullable=False),
        sa.Column("unit", sa.String(length=100), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["contract_id"], ["tblContracts.id"]),
        sa.ForeignKeyConstraint(["partner_id"], ["tblPartners.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["tblAccountingServices.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contract_id", "service_id", name="uq_contract_accounting_tariff_service"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_accounting_tariff_partner",
        "tblContractAccountingTariffs",
        ["partner_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_accounting_tariff_contract",
        "tblContractAccountingTariffs",
        ["contract_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_accounting_tariff_service",
        "tblContractAccountingTariffs",
        ["service_id"],
        if_not_exists=True,
    )

    op.create_table(
        "tblPartnerAccountingInfo",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(length=36), nullable=True),
        sa.Column("delete_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("partner_id", sa.String(length=36), nullable=False),
        sa.Column("legal_entity_name", sa.String(length=500), nullable=True),
        sa.Column("inn", sa.String(length=20), nullable=True),
        sa.Column("kpp", sa.String(length=20), nullable=True),
        sa.Column("billing_contact_name", sa.String(length=255), nullable=True),
        sa.Column("billing_email", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["partner_id"], ["tblPartners.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("partner_id", name="uq_partner_accounting_info_partner"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_partner_accounting_info_partner",
        "tblPartnerAccountingInfo",
        ["partner_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_partner_accounting_info_partner", table_name="tblPartnerAccountingInfo")
    op.drop_table("tblPartnerAccountingInfo")
    op.drop_index("ix_accounting_tariff_service", table_name="tblContractAccountingTariffs")
    op.drop_index("ix_accounting_tariff_contract", table_name="tblContractAccountingTariffs")
    op.drop_index("ix_accounting_tariff_partner", table_name="tblContractAccountingTariffs")
    op.drop_table("tblContractAccountingTariffs")
    op.drop_index("ix_accounting_line_object", table_name="tblAccountingDataLines")
    op.drop_index("ix_accounting_line_service", table_name="tblAccountingDataLines")
    op.drop_index("ix_accounting_line_period", table_name="tblAccountingDataLines")
    op.drop_table("tblAccountingDataLines")
    op.drop_index("ix_accounting_period_year_quarter", table_name="tblAccountingDataPeriods")
    op.drop_index("ix_accounting_period_status", table_name="tblAccountingDataPeriods")
    op.drop_index("ix_accounting_period_contract", table_name="tblAccountingDataPeriods")
    op.drop_index("ix_accounting_period_partner", table_name="tblAccountingDataPeriods")
    op.drop_table("tblAccountingDataPeriods")
    op.drop_index("uq_accounting_service_name", table_name="tblAccountingServices")
    op.drop_table("tblAccountingServices")
