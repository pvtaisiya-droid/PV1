"""Add product registration certificates.

Revision ID: 20260704_0013
Revises: 20260702_0012
Create Date: 2026-07-04
"""

from __future__ import annotations

from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision = "20260704_0013"
down_revision = "20260702_0012"
branch_labels = None
depends_on = None


registration_certificates = sa.table(
    "tblProductRegistrationCertificates",
    sa.column("id", sa.String(length=36)),
    sa.column("created_at", sa.DateTime()),
    sa.column("updated_at", sa.DateTime()),
    sa.column("is_active", sa.Boolean()),
    sa.column("is_deleted", sa.Boolean()),
    sa.column("version", sa.Integer()),
    sa.column("product_id", sa.String(length=36)),
    sa.column("registration_number", sa.String(length=100)),
    sa.column("registration_date", sa.Date()),
    sa.column("valid_until", sa.Date()),
    sa.column("status", sa.String(length=50)),
    sa.column("holder", sa.String(length=255)),
    sa.column("country_code", sa.String(length=2)),
    sa.column("dosage_form", sa.String(length=100)),
    sa.column("strength", sa.String(length=100)),
    sa.column("comment", sa.Text()),
    sa.column("created_by", sa.String(length=36)),
    sa.column("updated_by", sa.String(length=36)),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("tblProductRegistrationCertificates"):
        op.create_table(
            "tblProductRegistrationCertificates",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
            sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("0"), nullable=False),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by", sa.String(length=36), nullable=True),
            sa.Column("delete_reason", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
            sa.Column("product_id", sa.String(length=36), nullable=False),
            sa.Column("registration_number", sa.String(length=100), nullable=False),
            sa.Column("registration_date", sa.Date(), nullable=True),
            sa.Column("valid_until", sa.Date(), nullable=True),
            sa.Column("status", sa.String(length=50), server_default="active", nullable=False),
            sa.Column("holder", sa.String(length=255), nullable=True),
            sa.Column("country_code", sa.String(length=2), nullable=True),
            sa.Column("dosage_form", sa.String(length=100), nullable=True),
            sa.Column("strength", sa.String(length=100), nullable=True),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("updated_by", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(["product_id"], ["tblProducts.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["tblUsers.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["tblUsers.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    op.create_index(
        "uq_product_registration_number",
        "tblProductRegistrationCertificates",
        ["product_id", "registration_number"],
        unique=True,
        if_not_exists=True,
    )
    op.create_index(
        "ix_product_registration_product",
        "tblProductRegistrationCertificates",
        ["product_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_product_registration_number",
        "tblProductRegistrationCertificates",
        ["registration_number"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_product_registration_status",
        "tblProductRegistrationCertificates",
        ["status"],
        if_not_exists=True,
    )

    if inspector.has_table("tblAttachments"):
        attachment_columns = {column["name"] for column in inspector.get_columns("tblAttachments")}
        if "registration_certificate_id" not in attachment_columns:
            op.add_column(
                "tblAttachments",
                sa.Column("registration_certificate_id", sa.String(length=36), nullable=True),
            )
        op.create_index(
            "ix_attachment_registration_certificate",
            "tblAttachments",
            ["registration_certificate_id"],
            if_not_exists=True,
        )

    if inspector.has_table("tblProducts"):
        product_columns = {column["name"] for column in inspector.get_columns("tblProducts")}
        if "authorization_number" in product_columns:
            rows = bind.execute(
                sa.text(
                    """
                    SELECT id,
                           created_at,
                           updated_at,
                           authorization_number,
                           authorization_status,
                           authorization_country_code,
                           dosage_form,
                           strength,
                           comment,
                           created_by,
                           updated_by
                    FROM "tblProducts"
                    WHERE authorization_number IS NOT NULL
                      AND trim(authorization_number) <> ''
                      AND COALESCE(is_deleted, 0) = 0
                    """
                )
            ).mappings()
            for row in rows:
                exists = bind.execute(
                    sa.text(
                        """
                        SELECT 1
                        FROM "tblProductRegistrationCertificates"
                        WHERE product_id = :product_id
                          AND lower(registration_number) = lower(:registration_number)
                          AND COALESCE(is_deleted, 0) = 0
                        """
                    ),
                    {
                        "product_id": row["id"],
                        "registration_number": row["authorization_number"].strip(),
                    },
                ).first()
                if exists:
                    continue
                bind.execute(
                    registration_certificates.insert().values(
                        id=str(uuid4()),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        is_active=True,
                        is_deleted=False,
                        version=1,
                        product_id=row["id"],
                        registration_number=row["authorization_number"].strip(),
                        status=row["authorization_status"] or "active",
                        country_code=row["authorization_country_code"],
                        dosage_form=row["dosage_form"],
                        strength=row["strength"],
                        comment=row["comment"],
                        created_by=row["created_by"],
                        updated_by=row["updated_by"],
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("tblAttachments"):
        indexes = {index["name"] for index in inspector.get_indexes("tblAttachments")}
        if "ix_attachment_registration_certificate" in indexes:
            op.drop_index("ix_attachment_registration_certificate", table_name="tblAttachments")
        columns = {column["name"] for column in inspector.get_columns("tblAttachments")}
        if "registration_certificate_id" in columns:
            op.drop_column("tblAttachments", "registration_certificate_id")
    if inspector.has_table("tblProductRegistrationCertificates"):
        op.drop_index("ix_product_registration_status", table_name="tblProductRegistrationCertificates")
        op.drop_index("ix_product_registration_number", table_name="tblProductRegistrationCertificates")
        op.drop_index("ix_product_registration_product", table_name="tblProductRegistrationCertificates")
        op.drop_index("uq_product_registration_number", table_name="tblProductRegistrationCertificates")
        op.drop_table("tblProductRegistrationCertificates")
