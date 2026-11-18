"""Persist incoming message contacts, processing routes and duplicate fingerprints."""
import hashlib
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "20261111_0029"
down_revision = "20261002_0028"
branch_labels = None
depends_on = None


def common_columns():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_by", sa.String(36)),
        sa.Column("delete_reason", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    ]


def feature_columns(batch, table, definitions):
    actual = {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns(table)}
    for definition in definitions:
        previous = actual.get(definition.name)
        if previous is None:
            batch.add_column(definition)
        elif previous["type"]._type_affinity is not definition.type._type_affinity or previous["nullable"] != definition.nullable:
            raise RuntimeError(f"Review existing intake column: {table}.{definition.name}")


def ensure_index(table, name, columns):
    actual = {row["name"]: row for row in sa.inspect(op.get_bind()).get_indexes(table)}
    if name in actual:
        if actual[name]["column_names"] != columns or actual[name]["unique"]:
            raise RuntimeError(f"Review existing intake index: {name}")
    else:
        op.create_index(name, table, columns)


def validate_feature_table(table, definitions):
    actual = {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns(table)}
    for definition in definitions:
        previous = actual.get(definition.name)
        if previous is None or previous["type"]._type_affinity is not definition.type._type_affinity or previous["nullable"] != definition.nullable:
            raise RuntimeError(f"Review existing intake table: {table}.{definition.name}")


def upgrade():
    connection = op.get_bind()
    with op.batch_alter_table("tblIncomingRequests") as batch:
        feature_columns(batch, "tblIncomingRequests", [sa.Column("legacy_message_number", sa.String(50))])
    contacts = {column["name"]: column for column in sa.inspect(connection).get_columns("tblContractContacts")}
    with op.batch_alter_table("tblContractContacts") as batch:
        feature_columns(batch, "tblContractContacts", [sa.Column("display_name", sa.String(255))])
        for name, type_ in [("partner_id", sa.String(36)), ("last_name", sa.String(100)),
                            ("first_name", sa.String(100)), ("position", sa.String(255))]:
            if not contacts[name]["nullable"]:
                batch.alter_column(name, existing_type=type_, nullable=True)
    definitions = [sa.Column("original_text", sa.Text()), sa.Column("received_time", sa.Time())]
    definitions += [sa.Column(name, sa.String(size)) for name, size in [
        ("sender_name",255), ("sender_email",255), ("sender_phone",100), ("reporter_phone",100),
        ("creation_token",36), ("content_fingerprint",64), ("sender_contact_id",36), ("reporter_contact_id",36)]]
    definitions += [sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
                    for name in ["reporter_same_as_sender", "reporter_unknown", "intake_enabled"]]
    inspector = sa.inspect(connection)
    foreign_keys = inspector.get_foreign_keys("tblSafetyReports")
    uniques = inspector.get_unique_constraints("tblSafetyReports")
    with op.batch_alter_table("tblSafetyReports") as batch:
        feature_columns(batch, "tblSafetyReports", definitions)
        for role in ["sender", "reporter"]:
            column = role + "_contact_id"
            previous = [row for row in foreign_keys if row["constrained_columns"] == [column]]
            if previous and any(row["referred_table"] != "tblContractContacts" or row["referred_columns"] != ["id"] for row in previous):
                raise RuntimeError(f"Review existing intake foreign key: {column}")
            if not previous:
                batch.create_foreign_key("fk_safety_report_" + role + "_contact", "tblContractContacts", [column], ["id"])
        if not any(row["column_names"] == ["creation_token"] for row in uniques):
            batch.create_unique_constraint("uq_safety_report_creation_token", ["creation_token"])
    for name in ["sender_contact_id", "reporter_contact_id", "content_fingerprint"]:
        ensure_index("tblSafetyReports", "ix_tblSafetyReports_" + name, [name])
    rows = connection.execute(sa.text('SELECT id, raw_text, original_text FROM "tblSafetyReports" WHERE content_fingerprint IS NULL')).fetchall()
    for identifier, summary, original in rows:
        text = original or summary
        if text is not None and not isinstance(text, str):
            continue
        normalized = " ".join(unicodedata.normalize("NFKC", text or "").casefold().split())
        if normalized:
            connection.execute(sa.text('UPDATE "tblSafetyReports" SET content_fingerprint=:fingerprint WHERE id=:id'),
                               {"id": identifier, "fingerprint": hashlib.sha256(normalized.encode()).hexdigest()})
    routes = common_columns() + [
        sa.Column("safety_report_id", sa.String(36), sa.ForeignKey("tblSafetyReports.id"), nullable=False),
        sa.Column("route_kind", sa.String(20), nullable=False),
        sa.Column("route_number", sa.String(100), nullable=False, unique=True), sa.Column("details", sa.JSON(), nullable=False)]
    if not sa.inspect(connection).has_table("tblSafetyReportRoutes"):
        op.create_table("tblSafetyReportRoutes", *routes,
            sa.UniqueConstraint("safety_report_id", "route_kind", name="uq_safety_report_route_kind"),
            sa.CheckConstraint("route_kind IN ('quality', 'other', 'medical', 'pv')", name="ck_safety_report_route_kind"))
    else:
        validate_feature_table("tblSafetyReportRoutes", routes)
    ensure_index("tblSafetyReportRoutes", "ix_tblSafetyReportRoutes_safety_report_id", ["safety_report_id"])
    documents = common_columns() + [
        sa.Column("route_id", sa.String(36), sa.ForeignKey("tblSafetyReportRoutes.id"), nullable=False),
        sa.Column("attachment_id", sa.String(36), sa.ForeignKey("tblAttachments.id"), nullable=False)]
    if not sa.inspect(connection).has_table("tblSafetyReportRouteDocuments"):
        op.create_table("tblSafetyReportRouteDocuments", *documents,
            sa.UniqueConstraint("route_id", "attachment_id", name="uq_safety_report_route_document"))
    else:
        validate_feature_table("tblSafetyReportRouteDocuments", documents)
    ensure_index("tblSafetyReportRouteDocuments", "ix_tblSafetyReportRouteDocuments_route_id", ["route_id"])
    ensure_index("tblSafetyReportRouteDocuments", "ix_tblSafetyReportRouteDocuments_attachment_id", ["attachment_id"])


def downgrade():
    raise RuntimeError("Message intake contains contact and processing data; downgrade requires an explicit data export plan.")
