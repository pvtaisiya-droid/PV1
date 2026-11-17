"""add immutable PSMF document registry links and build snapshots

Revision ID: 20260824_0025
Revises: 20260819_0024
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa
import uuid
from datetime import datetime, timezone


revision = "20260824_0025"
down_revision = "20260819_0024"
branch_labels = None
depends_on = None


def _add_column_if_missing(inspector, table_name: str, column: sa.Column) -> None:
    columns = {row["name"] for row in inspector.get_columns(table_name)}
    if column.name not in columns:
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("tblAttachments"):
        return

    for column in (
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("is_signed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("scope", sa.String(length=50), nullable=False, server_default="GLOBAL"),
        sa.Column("psmf_annex_code", sa.String(length=50), nullable=True),
        sa.Column("include_in_psmf", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("inclusion_order", sa.Integer(), nullable=False, server_default="0"),
    ):
        _add_column_if_missing(inspector, "tblAttachments", column)

    if inspector.has_table("tblDocumentVersions"):
        for column in (
            sa.Column("valid_until", sa.Date(), nullable=True),
            sa.Column("is_signed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("page_count", sa.Integer(), nullable=True),
        ):
            _add_column_if_missing(inspector, "tblDocumentVersions", column)

    if not inspector.has_table("psmf_document_links"):
        op.create_table(
            "psmf_document_links",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_by", sa.String(length=36), nullable=True),
            sa.Column("delete_reason", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("attachment_id", sa.String(length=36), sa.ForeignKey("tblAttachments.id"), nullable=False),
            sa.Column("document_version_id", sa.String(length=36), sa.ForeignKey("tblDocumentVersions.id"), nullable=True),
            sa.Column("annex_code", sa.String(length=50), nullable=False),
            sa.Column("partner_id", sa.String(length=36), sa.ForeignKey("tblPartners.id"), nullable=True),
            sa.Column("scope", sa.String(length=50), nullable=False, server_default="GLOBAL"),
            sa.Column("is_included", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("inclusion_order", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("attachment_id", "annex_code", "partner_id", name="uq_psmf_document_link"),
        )

    if not inspector.has_table("psmf_build_documents"):
        op.create_table(
            "psmf_build_documents",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_by", sa.String(length=36), nullable=True),
            sa.Column("delete_reason", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("build_id", sa.String(length=36), sa.ForeignKey("psmf_partner_builds.id"), nullable=False),
            sa.Column("attachment_id", sa.String(length=36), sa.ForeignKey("tblAttachments.id"), nullable=False),
            sa.Column("document_version_id", sa.String(length=36), sa.ForeignKey("tblDocumentVersions.id"), nullable=False),
            sa.Column("annex_code", sa.String(length=50), nullable=False),
            sa.Column("inclusion_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("document_title_snapshot", sa.String(length=255), nullable=False),
            sa.Column("version_label_snapshot", sa.String(length=50), nullable=False),
            sa.Column("file_name_snapshot", sa.String(length=255), nullable=False),
            sa.Column("checksum_sha256_snapshot", sa.String(length=64), nullable=True),
            sa.Column("page_count_snapshot", sa.Integer(), nullable=True),
            sa.UniqueConstraint(
                "build_id",
                "attachment_id",
                "document_version_id",
                "annex_code",
                name="uq_psmf_build_document",
            ),
        )

    for statement in (
        'CREATE INDEX IF NOT EXISTS "ix_attachment_valid_until" ON "tblAttachments" (valid_until)',
        'CREATE INDEX IF NOT EXISTS "ix_attachment_scope" ON "tblAttachments" (scope)',
        'CREATE INDEX IF NOT EXISTS "ix_attachment_psmf_annex" ON "tblAttachments" (psmf_annex_code)',
        'CREATE INDEX IF NOT EXISTS "ix_attachment_include_psmf" ON "tblAttachments" (include_in_psmf)',
        'CREATE INDEX IF NOT EXISTS "ix_psmf_document_link_attachment" ON "psmf_document_links" (attachment_id)',
        'CREATE INDEX IF NOT EXISTS "ix_psmf_document_link_version" ON "psmf_document_links" (document_version_id)',
        'CREATE INDEX IF NOT EXISTS "ix_psmf_document_link_annex" ON "psmf_document_links" (annex_code)',
        'CREATE INDEX IF NOT EXISTS "ix_psmf_document_link_partner" ON "psmf_document_links" (partner_id)',
        'CREATE INDEX IF NOT EXISTS "ix_psmf_build_document_build" ON "psmf_build_documents" (build_id)',
        'CREATE INDEX IF NOT EXISTS "ix_psmf_build_document_attachment" ON "psmf_build_documents" (attachment_id)',
        'CREATE INDEX IF NOT EXISTS "ix_psmf_build_document_version" ON "psmf_build_documents" (document_version_id)',
        'CREATE INDEX IF NOT EXISTS "ix_psmf_build_document_annex" ON "psmf_build_documents" (annex_code)',
    ):
        op.execute(sa.text(statement))
    if inspector.has_table("psmf_blocks") and inspector.has_table("psmf_components"):
        _backfill_legacy_psmf_attachments(bind)


def _backfill_legacy_psmf_attachments(bind) -> None:
    dot_one_by_block = {
        "B-1-PARTNER-ORG": "Приложение Б-1.1",
        "B-3-PARTNER-CONTRACTS": "Приложение Б-3.1",
        "V-1-PARTNER-SOURCES": "Приложение В-1.1",
        "G-1-PARTNER-SYSTEMS": "Приложение Г-1.1",
        "D-1-PARTNER-SOP": "Приложение Д-1.1",
        "E-2-PARTNER-EFFECTIVENESS": "Приложение Е-2.1",
        "ZH-1-PARTNER-AUDIT-PLAN": "Приложение Ж-1.1",
        "ZH-2-PARTNER-AUDITS": "Приложение Ж-2.1",
        "ZH-3-PARTNER-FINDINGS": "Приложение Ж-3.1",
        "Z-2-PARTNER-MONITORING": "Приложение З-2.1",
        "I-2-PARTNER-CHANGELOG": "Приложение И-2.1",
    }
    rows = bind.execute(
        sa.text(
            """
            SELECT a.id, a.partner_id, b.code AS block_code, b.block_type,
                   c.code AS component_code
            FROM "tblAttachments" a
            LEFT JOIN "psmf_blocks" b
              ON a.related_object_type = 'PSMFBlock' AND a.related_object_id = b.id
            LEFT JOIN "psmf_components" c
              ON (a.related_object_type = 'PSMFBlock' AND b.component_id = c.id)
              OR (a.related_object_type = 'PSMFComponent' AND a.related_object_id = c.id)
            WHERE a.is_deleted = false
              AND a.related_object_type IN ('PSMFBlock', 'PSMFComponent')
            """
        )
    ).mappings().all()
    for row in rows:
        annex_code = dot_one_by_block.get(row["block_code"]) or row["component_code"]
        if not annex_code:
            continue
        scope = "PARTNER_SPECIFIC" if row["partner_id"] or row["block_type"] == "PARTNER_BLOCK" else "GLOBAL"
        bind.execute(
            sa.text(
                """
                UPDATE "tblAttachments"
                SET psmf_annex_code = :annex_code, include_in_psmf = 1, scope = :scope
                WHERE id = :attachment_id
                """
            ),
            {"annex_code": annex_code, "scope": scope, "attachment_id": row["id"]},
        )
        version_id = bind.execute(
            sa.text(
                """
                SELECT id FROM "tblDocumentVersions"
                WHERE attachment_id = :attachment_id AND is_deleted = false
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"attachment_id": row["id"]},
        ).scalar()
        bind.execute(
            sa.text(
                """
                INSERT INTO "psmf_document_links"
                    (id, created_at, updated_at, is_active, is_deleted, version,
                     attachment_id, document_version_id, annex_code, partner_id,
                     scope, is_included, inclusion_order)
                VALUES
                    (:id, :now, :now, 1, 0, 1, :attachment_id, :version_id,
                     :annex_code, :partner_id, :scope, 1, 0)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "now": datetime.now(timezone.utc),
                "attachment_id": row["id"],
                "version_id": version_id,
                "annex_code": annex_code,
                "partner_id": row["partner_id"],
                "scope": scope,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("psmf_build_documents"):
        op.drop_table("psmf_build_documents")
    if inspector.has_table("psmf_document_links"):
        op.drop_table("psmf_document_links")
    if inspector.has_table("tblDocumentVersions"):
        for name in ("page_count", "is_signed", "valid_until"):
            if name in {row["name"] for row in inspector.get_columns("tblDocumentVersions")}:
                op.drop_column("tblDocumentVersions", name)
    if inspector.has_table("tblAttachments"):
        for name in (
            "inclusion_order",
            "include_in_psmf",
            "psmf_annex_code",
            "scope",
            "page_count",
            "is_signed",
            "valid_until",
        ):
            if name in {row["name"] for row in inspector.get_columns("tblAttachments")}:
                op.drop_column("tblAttachments", name)
