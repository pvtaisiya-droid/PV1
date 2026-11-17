"""add local login, password hashes and server sessions

Revision ID: 20260818_0021
Revises: 20260817_0020
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260818_0021"
down_revision = "20260817_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("tblUsers"):
        user_columns = {column["name"] for column in inspector.get_columns("tblUsers")}
        with op.batch_alter_table("tblUsers") as batch_op:
            if "login" not in user_columns:
                batch_op.add_column(sa.Column("login", sa.String(length=100), nullable=True))
            if "password_hash" not in user_columns:
                batch_op.add_column(
                    sa.Column("password_hash", sa.String(length=255), nullable=True)
                )
        op.execute(
            sa.text(
                """
                UPDATE "tblUsers"
                SET login = email
                WHERE login IS NULL OR trim(login) = ''
                """
            )
        )
        indexes = {index["name"] for index in sa.inspect(bind).get_indexes("tblUsers")}
        if "ix_tblUsers_login" not in indexes:
            op.create_index(
                "ix_tblUsers_login",
                "tblUsers",
                ["login"],
                unique=True,
            )

    inspector = sa.inspect(bind)
    if not inspector.has_table("tblAuthSessions"):
        op.create_table(
            "tblAuthSessions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("is_deleted", sa.Boolean(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_by", sa.String(length=36), nullable=True),
            sa.Column("delete_reason", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["tblUsers.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_auth_session_user", "tblAuthSessions", ["user_id"], unique=False
        )
        op.create_index(
            "ix_auth_session_expires", "tblAuthSessions", ["expires_at"], unique=False
        )
        op.create_index(
            "ix_tblAuthSessions_token_hash",
            "tblAuthSessions",
            ["token_hash"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("tblAuthSessions"):
        op.drop_table("tblAuthSessions")

    inspector = sa.inspect(bind)
    if inspector.has_table("tblUsers"):
        indexes = {index["name"] for index in inspector.get_indexes("tblUsers")}
        if "ix_tblUsers_login" in indexes:
            op.drop_index("ix_tblUsers_login", table_name="tblUsers")
        columns = {column["name"] for column in inspector.get_columns("tblUsers")}
        with op.batch_alter_table("tblUsers") as batch_op:
            if "password_hash" in columns:
                batch_op.drop_column("password_hash")
            if "login" in columns:
                batch_op.drop_column("login")
