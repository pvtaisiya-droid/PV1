"""remove legacy partner contact and SDEA fields

Revision ID: 20260723_0016
Revises: 20260720_0015
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260723_0016"
down_revision = "20260720_0015"
branch_labels = None
depends_on = None


REMOVED_COLUMNS = (
    "country_code",
    "region",
    "email",
    "phone",
    "pv_responsible_person",
    "sdea_required",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("tblPartners"):
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("tblPartners")
    }
    columns_to_drop = [
        column_name
        for column_name in REMOVED_COLUMNS
        if column_name in existing_columns
    ]
    if not columns_to_drop:
        return

    existing_indexes = {
        index["name"] for index in inspector.get_indexes("tblPartners")
    }
    with op.batch_alter_table("tblPartners", recreate="always") as batch_op:
        if "ix_tblPartners_country_code" in existing_indexes:
            batch_op.drop_index("ix_tblPartners_country_code")
        for column_name in columns_to_drop:
            batch_op.drop_column(column_name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("tblPartners"):
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("tblPartners")
    }
    columns = {
        "country_code": sa.Column("country_code", sa.String(length=2), nullable=True),
        "region": sa.Column("region", sa.String(length=100), nullable=True),
        "email": sa.Column("email", sa.String(length=255), nullable=True),
        "phone": sa.Column("phone", sa.String(length=100), nullable=True),
        "pv_responsible_person": sa.Column(
            "pv_responsible_person",
            sa.String(length=255),
            nullable=True,
        ),
        "sdea_required": sa.Column(
            "sdea_required",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    }
    with op.batch_alter_table("tblPartners", recreate="always") as batch_op:
        for column_name in REMOVED_COLUMNS:
            if column_name not in existing_columns:
                batch_op.add_column(columns[column_name])

    inspector = sa.inspect(bind)
    existing_indexes = {
        index["name"] for index in inspector.get_indexes("tblPartners")
    }
    if (
        "country_code" in {
            column["name"] for column in inspector.get_columns("tblPartners")
        }
        and "ix_tblPartners_country_code" not in existing_indexes
    ):
        op.create_index(
            "ix_tblPartners_country_code",
            "tblPartners",
            ["country_code"],
        )
