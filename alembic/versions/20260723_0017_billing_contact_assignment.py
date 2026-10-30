"""add billing contact assignment

Revision ID: 20260723_0017
Revises: 20260723_0016
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260723_0017"
down_revision = "20260723_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("tblContractContacts"):
        return
    columns = {
        column["name"]
        for column in inspector.get_columns("tblContractContacts")
    }
    if "is_billing_contact" not in columns:
        op.add_column(
            "tblContractContacts",
            sa.Column(
                "is_billing_contact",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("tblContractContacts"):
        return
    columns = {
        column["name"]
        for column in inspector.get_columns("tblContractContacts")
    }
    if "is_billing_contact" in columns:
        with op.batch_alter_table("tblContractContacts") as batch_op:
            batch_op.drop_column("is_billing_contact")
