"""add explicit safety message direction

Revision ID: 20260814_0018
Revises: 20260723_0017
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260814_0018"
down_revision = "20260723_0017"
branch_labels = None
depends_on = None


TABLES = ("tblSafetyReports", "tblFollowUps")


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in TABLES:
        inspector = sa.inspect(bind)
        if not inspector.has_table(table_name):
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "message_direction" not in columns:
            op.add_column(
                table_name,
                sa.Column(
                    "message_direction",
                    sa.String(length=50),
                    nullable=False,
                    server_default="needs_review",
                ),
            )
        op.execute(
            sa.text(
                f'''
                UPDATE "{table_name}"
                SET message_direction = CASE
                    WHEN lower(trim(source_type)) = 'partner'
                        THEN 'partner_to_our_company'
                    WHEN lower(trim(source_type)) = 'our_company'
                        THEN 'our_company_to_partner'
                    ELSE 'needs_review'
                END
                WHERE message_direction IS NULL
                   OR message_direction = ''
                   OR message_direction NOT IN (
                       'partner_to_our_company',
                       'our_company_to_partner',
                       'needs_review'
                   )
                   OR (
                       message_direction = 'needs_review'
                       AND lower(trim(source_type)) IN ('partner', 'our_company')
                   )
                '''
            )
        )
        op.create_index(
            f"ix_{table_name}_message_direction",
            table_name,
            ["message_direction"],
            if_not_exists=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(TABLES):
        inspector = sa.inspect(bind)
        if not inspector.has_table(table_name):
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "message_direction" not in columns:
            continue
        indexes = {index["name"] for index in inspector.get_indexes(table_name)}
        index_name = f"ix_{table_name}_message_direction"
        if index_name in indexes:
            op.drop_index(index_name, table_name=table_name)
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column("message_direction")
