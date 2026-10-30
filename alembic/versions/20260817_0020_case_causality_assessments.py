"""add structured case causality assessment fields

Revision ID: 20260817_0020
Revises: 20260814_0019
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260817_0020"
down_revision = "20260814_0019"
branch_labels = None
depends_on = None


TABLE_NAME = "tblCaseProductReactionAssessments"
COLUMNS = (
    sa.Column("naranjo_score", sa.Integer(), nullable=True),
    sa.Column("naranjo_answers_json", sa.Text(), nullable=True),
    sa.Column("assessor_role", sa.String(length=50), nullable=True),
    sa.Column("assessor_name", sa.String(length=255), nullable=True),
    sa.Column("assessor_organization", sa.String(length=255), nullable=True),
    sa.Column("assessment_date", sa.Date(), nullable=True),
    sa.Column(
        "created_by_user_id",
        sa.String(length=36),
        sa.ForeignKey("tblUsers.id", name="fk_case_assessment_creator"),
        nullable=True,
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE_NAME):
        return
    existing_columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        for column in COLUMNS:
            if column.name not in existing_columns:
                batch_op.add_column(column)

    op.create_index(
        "ix_case_assessment_method_result",
        TABLE_NAME,
        ["causality_method", "relatedness"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_case_assessment_role_date",
        TABLE_NAME,
        ["assessor_role", "assessment_date"],
        if_not_exists=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE_NAME):
        return
    existing_indexes = {index["name"] for index in inspector.get_indexes(TABLE_NAME)}
    for index_name in (
        "ix_case_assessment_role_date",
        "ix_case_assessment_method_result",
    ):
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name=TABLE_NAME)

    existing_columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        for column in reversed(COLUMNS):
            if column.name in existing_columns:
                batch_op.drop_column(column.name)
