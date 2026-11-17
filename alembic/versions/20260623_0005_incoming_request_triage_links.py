"""incoming request triage and case links

Revision ID: 20260623_0005
Revises: 20260623_0004
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260623_0005"
down_revision = "20260623_0004"
branch_labels = None
depends_on = None


INCOMING_REQUEST_COLUMNS = [
    ("message_number", sa.Column("message_number", sa.String(length=50), nullable=True)),
    ("received_date", sa.Column("received_date", sa.Date(), nullable=True)),
    ("received_time", sa.Column("received_time", sa.String(length=20), nullable=True)),
    ("source_type", sa.Column("source_type", sa.String(length=100), nullable=True)),
    ("source_name", sa.Column("source_name", sa.String(length=255), nullable=True)),
    ("channel", sa.Column("channel", sa.String(length=100), nullable=True)),
    ("active_substance_id", sa.Column("active_substance_id", sa.String(length=36), nullable=True)),
    ("reporter_name", sa.Column("reporter_name", sa.String(length=255), nullable=True)),
    ("reporter_contact", sa.Column("reporter_contact", sa.String(length=255), nullable=True)),
    ("message_category", sa.Column("message_category", sa.String(length=100), nullable=True)),
    (
        "triage_status",
        sa.Column("triage_status", sa.String(length=50), server_default="not_started", nullable=False),
    ),
    ("safety_relevance", sa.Column("safety_relevance", sa.String(length=50), nullable=True)),
    ("potential_icsr", sa.Column("potential_icsr", sa.String(length=50), nullable=True)),
    ("validity_status", sa.Column("validity_status", sa.String(length=50), nullable=True)),
    (
        "has_identifiable_patient",
        sa.Column("has_identifiable_patient", sa.Boolean(), server_default=sa.false(), nullable=False),
    ),
    (
        "has_identifiable_reporter",
        sa.Column("has_identifiable_reporter", sa.Boolean(), server_default=sa.false(), nullable=False),
    ),
    (
        "has_suspect_product",
        sa.Column("has_suspect_product", sa.Boolean(), server_default=sa.false(), nullable=False),
    ),
    (
        "has_adverse_event",
        sa.Column("has_adverse_event", sa.Boolean(), server_default=sa.false(), nullable=False),
    ),
    ("missing_criteria", sa.Column("missing_criteria", sa.Text(), nullable=True)),
    ("triage_result", sa.Column("triage_result", sa.String(length=100), nullable=True)),
    ("triage_comment", sa.Column("triage_comment", sa.Text(), nullable=True)),
    ("triaged_by", sa.Column("triaged_by", sa.String(length=36), nullable=True)),
    ("triaged_at", sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True)),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("tblIncomingRequests"):
        columns = {column["name"] for column in inspector.get_columns("tblIncomingRequests")}
        for column_name, column in INCOMING_REQUEST_COLUMNS:
            if column_name not in columns:
                op.add_column("tblIncomingRequests", column)

        indexes = {index["name"] for index in inspector.get_indexes("tblIncomingRequests")}
        if "ix_incoming_request_message_number" not in indexes:
            op.create_index(
                "ix_incoming_request_message_number",
                "tblIncomingRequests",
                ["message_number"],
                unique=False,
            )
        if "ix_incoming_request_triage" not in indexes:
            op.create_index(
                "ix_incoming_request_triage",
                "tblIncomingRequests",
                ["triage_status", "validity_status"],
                unique=False,
            )

    if not inspector.has_table("tblIncomingRequestCaseLinks"):
        op.create_table(
            "tblIncomingRequestCaseLinks",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("incoming_request_id", sa.String(length=36), nullable=False),
            sa.Column("case_id", sa.String(length=36), nullable=False),
            sa.Column("link_type", sa.String(length=50), nullable=False, server_default="created_from_message"),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_by", sa.String(length=36), nullable=True),
            sa.Column("delete_reason", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.ForeignKeyConstraint(["case_id"], ["tblCases.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["tblUsers.id"]),
            sa.ForeignKeyConstraint(["incoming_request_id"], ["tblIncomingRequests.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "incoming_request_id",
                "case_id",
                "link_type",
                name="uq_incoming_request_case_link_type",
            ),
        )
        op.create_index(
            "ix_incoming_request_case_link_request",
            "tblIncomingRequestCaseLinks",
            ["incoming_request_id"],
            unique=False,
        )
        op.create_index(
            "ix_incoming_request_case_link_case",
            "tblIncomingRequestCaseLinks",
            ["case_id"],
            unique=False,
        )
        op.create_index(
            "ix_incoming_request_case_link_type",
            "tblIncomingRequestCaseLinks",
            ["link_type"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("tblIncomingRequestCaseLinks"):
        op.drop_table("tblIncomingRequestCaseLinks")

    if inspector.has_table("tblIncomingRequests"):
        indexes = {index["name"] for index in inspector.get_indexes("tblIncomingRequests")}
        if "ix_incoming_request_triage" in indexes:
            op.drop_index("ix_incoming_request_triage", table_name="tblIncomingRequests")
        if "ix_incoming_request_message_number" in indexes:
            op.drop_index("ix_incoming_request_message_number", table_name="tblIncomingRequests")

        columns = {column["name"] for column in inspector.get_columns("tblIncomingRequests")}
        for column_name, _column in reversed(INCOMING_REQUEST_COLUMNS):
            if column_name in columns:
                with op.batch_alter_table("tblIncomingRequests", recreate="always") as batch_op:
                    batch_op.drop_column(column_name)
