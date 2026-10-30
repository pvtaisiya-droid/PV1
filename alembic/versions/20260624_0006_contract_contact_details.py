"""contract contact details

Revision ID: 20260624_0006
Revises: 20260623_0005
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260624_0006"
down_revision = "20260623_0005"
branch_labels = None
depends_on = None


CONTACT_DETAIL_COLUMNS = [
    ("mobile_phone", sa.Column("mobile_phone", sa.String(length=100), nullable=True)),
    ("work_phone", sa.Column("work_phone", sa.String(length=100), nullable=True)),
    ("extension_phone", sa.Column("extension_phone", sa.String(length=50), nullable=True)),
    ("company_website", sa.Column("company_website", sa.String(length=255), nullable=True)),
    ("address", sa.Column("address", sa.Text(), nullable=True)),
]


def _existing_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    existing = _existing_columns("tblContractContacts")
    for column_name, column in CONTACT_DETAIL_COLUMNS:
        if column_name not in existing:
            op.add_column("tblContractContacts", column)


def downgrade() -> None:
    existing = _existing_columns("tblContractContacts")
    for column_name, _column in reversed(CONTACT_DETAIL_COLUMNS):
        if column_name in existing:
            op.drop_column("tblContractContacts", column_name)
