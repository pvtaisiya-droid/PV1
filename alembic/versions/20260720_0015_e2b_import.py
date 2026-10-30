"""add e2b zip import workflow

Revision ID: 20260720_0015
Revises: 20260720_0014
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260720_0015"
down_revision = "20260720_0014"
branch_labels = None
depends_on = None


def common_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(length=36), nullable=True),
        sa.Column("delete_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    ]


def has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return inspector.has_table(table_name)


def add_columns_if_missing(table_name: str, columns: list[sa.Column]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table_name, column)


def drop_columns_if_present(table_name: str, column_names: list[str]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    for column_name in column_names:
        if column_name in existing:
            op.drop_column(table_name, column_name)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not has_table(inspector, "tblE2BImportBatches"):
        op.create_table(
            "tblE2BImportBatches",
            *common_columns(),
            sa.Column("source_filename", sa.String(length=255), nullable=False),
            sa.Column("source_size_bytes", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("zip_checksum_sha256", sa.String(length=64), nullable=False),
            sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("uploaded_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("status", sa.String(length=50), server_default="uploaded", nullable=False),
            sa.Column("importer_version", sa.String(length=50), server_default="e2b-importer-1", nullable=False),
            sa.Column("storage_dir", sa.String(length=500), nullable=True),
            sa.Column("source_zip_path", sa.String(length=500), nullable=True),
            sa.Column("file_manifest_json", sa.Text(), nullable=True),
            sa.Column("normalized_json", sa.Text(), nullable=True),
            sa.Column("detected_xml_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("detected_pdf_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("detected_other_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("detected_case_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("detected_message_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("error_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("warning_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("duplicate_batch_id", sa.String(length=36), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("confirmed_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("import_summary_json", sa.Text(), nullable=True),
            sa.Column("created_safety_report_id", sa.String(length=36), nullable=True),
            sa.Column("created_incoming_request_id", sa.String(length=36), nullable=True),
            sa.Column("created_case_id", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["tblUsers.id"]),
            sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["tblUsers.id"]),
            sa.ForeignKeyConstraint(["duplicate_batch_id"], ["tblE2BImportBatches.id"]),
            sa.ForeignKeyConstraint(["created_safety_report_id"], ["tblSafetyReports.id"]),
            sa.ForeignKeyConstraint(["created_incoming_request_id"], ["tblIncomingRequests.id"]),
            sa.ForeignKeyConstraint(["created_case_id"], ["tblCases.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    op.create_index("ix_e2b_batch_status_uploaded", "tblE2BImportBatches", ["status", "uploaded_at"], if_not_exists=True)
    op.create_index("ix_e2b_batch_checksum", "tblE2BImportBatches", ["zip_checksum_sha256"], if_not_exists=True)

    if not has_table(inspector, "tblE2BImportItems"):
        op.create_table(
            "tblE2BImportItems",
            *common_columns(),
            sa.Column("batch_id", sa.String(length=36), nullable=False),
            sa.Column("original_path", sa.String(length=1000), nullable=False),
            sa.Column("stored_path", sa.String(length=1000), nullable=True),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("file_role", sa.String(length=50), nullable=False),
            sa.Column("mime_type", sa.String(length=100), nullable=True),
            sa.Column("size_bytes", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=50), server_default="detected", nullable=False),
            sa.Column("e2b_version", sa.String(length=50), nullable=True),
            sa.Column("message_identifier", sa.String(length=255), nullable=True),
            sa.Column("case_identifier", sa.String(length=255), nullable=True),
            sa.Column("recipient", sa.String(length=255), nullable=True),
            sa.Column("validation_json", sa.Text(), nullable=True),
            sa.Column("normalized_json", sa.Text(), nullable=True),
            sa.Column("duplicate_of_item_id", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(["batch_id"], ["tblE2BImportBatches.id"]),
            sa.ForeignKeyConstraint(["duplicate_of_item_id"], ["tblE2BImportItems.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    op.create_index("ix_e2b_item_batch_role", "tblE2BImportItems", ["batch_id", "file_role"], if_not_exists=True)
    op.create_index("ix_e2b_item_checksum", "tblE2BImportItems", ["checksum_sha256"], if_not_exists=True)
    op.create_index("ix_tblE2BImportItems_message_identifier", "tblE2BImportItems", ["message_identifier"], if_not_exists=True)
    op.create_index("ix_tblE2BImportItems_case_identifier", "tblE2BImportItems", ["case_identifier"], if_not_exists=True)

    if not has_table(inspector, "tblE2BImportIssues"):
        op.create_table(
            "tblE2BImportIssues",
            *common_columns(),
            sa.Column("batch_id", sa.String(length=36), nullable=False),
            sa.Column("item_id", sa.String(length=36), nullable=True),
            sa.Column("severity", sa.String(length=20), server_default="warning", nullable=False),
            sa.Column("issue_type", sa.String(length=100), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("technical_path", sa.String(length=1000), nullable=True),
            sa.Column("is_blocking", sa.Boolean(), server_default=sa.text("0"), nullable=False),
            sa.ForeignKeyConstraint(["batch_id"], ["tblE2BImportBatches.id"]),
            sa.ForeignKeyConstraint(["item_id"], ["tblE2BImportItems.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    op.create_index("ix_e2b_issue_batch_severity", "tblE2BImportIssues", ["batch_id", "severity"], if_not_exists=True)
    op.create_index("ix_e2b_issue_item", "tblE2BImportIssues", ["item_id"], if_not_exists=True)

    if not has_table(inspector, "tblE2BTransmissions"):
        op.create_table(
            "tblE2BTransmissions",
            *common_columns(),
            sa.Column("batch_id", sa.String(length=36), nullable=False),
            sa.Column("import_item_id", sa.String(length=36), nullable=False),
            sa.Column("case_id", sa.String(length=36), nullable=True),
            sa.Column("safety_report_id", sa.String(length=36), nullable=True),
            sa.Column("incoming_request_id", sa.String(length=36), nullable=True),
            sa.Column("message_identifier", sa.String(length=255), nullable=True),
            sa.Column("message_version", sa.String(length=50), nullable=True),
            sa.Column("worldwide_case_id", sa.String(length=255), nullable=True),
            sa.Column("local_sender_case_id", sa.String(length=255), nullable=True),
            sa.Column("receiver", sa.String(length=255), nullable=True),
            sa.Column("sender", sa.String(length=255), nullable=True),
            sa.Column("message_type", sa.String(length=100), nullable=True),
            sa.Column("followup_type", sa.String(length=100), nullable=True),
            sa.Column("xml_checksum_sha256", sa.String(length=64), nullable=True),
            sa.Column("normalized_json", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["batch_id"], ["tblE2BImportBatches.id"]),
            sa.ForeignKeyConstraint(["import_item_id"], ["tblE2BImportItems.id"]),
            sa.ForeignKeyConstraint(["case_id"], ["tblCases.id"]),
            sa.ForeignKeyConstraint(["safety_report_id"], ["tblSafetyReports.id"]),
            sa.ForeignKeyConstraint(["incoming_request_id"], ["tblIncomingRequests.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("import_item_id", name="uq_e2b_transmission_import_item"),
        )
    op.create_index("ix_e2b_transmission_case", "tblE2BTransmissions", ["case_id"], if_not_exists=True)
    op.create_index("ix_e2b_transmission_message", "tblE2BTransmissions", ["message_identifier", "message_version"], if_not_exists=True)
    op.create_index("ix_tblE2BTransmissions_xml_checksum_sha256", "tblE2BTransmissions", ["xml_checksum_sha256"], if_not_exists=True)

    if not has_table(inspector, "tblCaseDrugSubstances"):
        op.create_table(
            "tblCaseDrugSubstances",
            *common_columns(),
            sa.Column("case_product_id", sa.String(length=36), nullable=False),
            sa.Column("substance_id", sa.String(length=36), nullable=True),
            sa.Column("reported_substance_name", sa.String(length=255), nullable=True),
            sa.Column("normalized_substance_name", sa.String(length=255), nullable=True),
            sa.Column("substance_role", sa.String(length=50), server_default="active", nullable=False),
            sa.Column("match_status", sa.String(length=50), nullable=True),
            sa.Column("strength_value", sa.String(length=100), nullable=True),
            sa.Column("strength_unit", sa.String(length=100), nullable=True),
            sa.Column("import_batch_id", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(["case_product_id"], ["tblCaseProducts.id"]),
            sa.ForeignKeyConstraint(["substance_id"], ["tblSubstances.id"]),
            sa.ForeignKeyConstraint(["import_batch_id"], ["tblE2BImportBatches.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    op.create_index("ix_case_drug_substance_case_product", "tblCaseDrugSubstances", ["case_product_id"], if_not_exists=True)
    op.create_index("ix_case_drug_substance_substance", "tblCaseDrugSubstances", ["substance_id"], if_not_exists=True)

    if not has_table(inspector, "tblCaseLiteratureReferences"):
        op.create_table(
            "tblCaseLiteratureReferences",
            *common_columns(),
            sa.Column("case_id", sa.String(length=36), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=True),
            sa.Column("authors", sa.Text(), nullable=True),
            sa.Column("journal", sa.String(length=255), nullable=True),
            sa.Column("publication_year", sa.Integer(), nullable=True),
            sa.Column("volume", sa.String(length=100), nullable=True),
            sa.Column("issue", sa.String(length=100), nullable=True),
            sa.Column("pages", sa.String(length=100), nullable=True),
            sa.Column("doi", sa.String(length=255), nullable=True),
            sa.Column("pmid", sa.String(length=100), nullable=True),
            sa.Column("citation", sa.Text(), nullable=True),
            sa.Column("linked_attachment_id", sa.String(length=36), nullable=True),
            sa.Column("additional_source_text", sa.Text(), nullable=True),
            sa.Column("import_batch_id", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(["case_id"], ["tblCases.id"]),
            sa.ForeignKeyConstraint(["linked_attachment_id"], ["tblAttachments.id"]),
            sa.ForeignKeyConstraint(["import_batch_id"], ["tblE2BImportBatches.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    op.create_index("ix_case_literature_case", "tblCaseLiteratureReferences", ["case_id"], if_not_exists=True)
    op.create_index("ix_case_literature_pmid", "tblCaseLiteratureReferences", ["pmid"], if_not_exists=True)
    op.create_index("ix_case_literature_doi", "tblCaseLiteratureReferences", ["doi"], if_not_exists=True)

    if not has_table(inspector, "tblCaseNarratives"):
        op.create_table(
            "tblCaseNarratives",
            *common_columns(),
            sa.Column("case_id", sa.String(length=36), nullable=False),
            sa.Column("narrative_type", sa.String(length=100), server_default="medical_narrative", nullable=False),
            sa.Column("narrative_text", sa.Text(), nullable=True),
            sa.Column("language", sa.String(length=20), nullable=True),
            sa.Column("source", sa.String(length=100), nullable=True),
            sa.Column("import_batch_id", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(["case_id"], ["tblCases.id"]),
            sa.ForeignKeyConstraint(["import_batch_id"], ["tblE2BImportBatches.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    op.create_index("ix_case_narrative_case", "tblCaseNarratives", ["case_id"], if_not_exists=True)
    op.create_index("ix_case_narrative_type", "tblCaseNarratives", ["narrative_type"], if_not_exists=True)

    add_columns_if_missing(
        "tblSafetyReports",
        [
            sa.Column("import_batch_id", sa.String(length=36), nullable=True),
            sa.Column("import_item_id", sa.String(length=36), nullable=True),
            sa.Column("e2b_message_identifier", sa.String(length=255), nullable=True),
            sa.Column("e2b_message_version", sa.String(length=50), nullable=True),
            sa.Column("e2b_followup_type", sa.String(length=100), nullable=True),
            sa.Column("source_checksum_sha256", sa.String(length=64), nullable=True),
        ],
    )
    add_columns_if_missing(
        "tblCases",
        [
            sa.Column("local_sender_case_id", sa.String(length=255), nullable=True),
            sa.Column("source_country_code", sa.String(length=2), nullable=True),
            sa.Column("data_review_status", sa.String(length=100), nullable=True),
            sa.Column("e2b_import_batch_id", sa.String(length=36), nullable=True),
        ],
    )
    add_columns_if_missing(
        "tblPatients",
        [
            sa.Column("country_code", sa.String(length=2), nullable=True),
            sa.Column("age_group", sa.String(length=100), nullable=True),
            sa.Column("date_of_birth_precision", sa.String(length=20), nullable=True),
            sa.Column("death_date", sa.Date(), nullable=True),
            sa.Column("death_date_raw", sa.String(length=100), nullable=True),
            sa.Column("death_date_precision", sa.String(length=20), nullable=True),
            sa.Column("death_cause", sa.Text(), nullable=True),
            sa.Column("import_batch_id", sa.String(length=36), nullable=True),
        ],
    )
    add_columns_if_missing(
        "tblCaseProducts",
        [
            sa.Column("normalized_product_name", sa.String(length=255), nullable=True),
            sa.Column("product_match_status", sa.String(length=50), nullable=True),
            sa.Column("substance_match_status", sa.String(length=50), nullable=True),
            sa.Column("dosage_form", sa.String(length=100), nullable=True),
            sa.Column("therapy_start_date_precision", sa.String(length=20), nullable=True),
            sa.Column("therapy_end_date_precision", sa.String(length=20), nullable=True),
            sa.Column("duration_text", sa.String(length=255), nullable=True),
            sa.Column("expiry_date", sa.Date(), nullable=True),
            sa.Column("expiry_date_raw", sa.String(length=100), nullable=True),
            sa.Column("authorization_number", sa.String(length=100), nullable=True),
            sa.Column("authorization_country_code", sa.String(length=2), nullable=True),
            sa.Column("authorization_holder", sa.String(length=255), nullable=True),
            sa.Column("causality_text", sa.Text(), nullable=True),
            sa.Column("null_flavor", sa.String(length=50), nullable=True),
            sa.Column("import_batch_id", sa.String(length=36), nullable=True),
        ],
    )
    add_columns_if_missing(
        "tblReactions",
        [
            sa.Column("meddra_version", sa.String(length=50), nullable=True),
            sa.Column("term_language", sa.String(length=20), nullable=True),
            sa.Column("term_country_code", sa.String(length=2), nullable=True),
            sa.Column("onset_date_precision", sa.String(length=20), nullable=True),
            sa.Column("end_date_precision", sa.String(length=20), nullable=True),
            sa.Column("duration_text", sa.String(length=255), nullable=True),
            sa.Column("medically_confirmed", sa.Boolean(), nullable=True),
            sa.Column("reporter_comment", sa.Text(), nullable=True),
            sa.Column("null_flavor", sa.String(length=50), nullable=True),
            sa.Column("import_batch_id", sa.String(length=36), nullable=True),
        ],
    )
    add_columns_if_missing(
        "tblAttachments",
        [
            sa.Column("import_batch_id", sa.String(length=36), nullable=True),
            sa.Column("e2b_source", sa.String(length=50), nullable=True),
            sa.Column("duplicate_of_attachment_id", sa.String(length=36), nullable=True),
        ],
    )

    for table, indexes in {
        "tblSafetyReports": [
            ("ix_safety_report_import_batch", ["import_batch_id"]),
            ("ix_safety_report_e2b_message", ["e2b_message_identifier"]),
            ("ix_safety_report_source_checksum", ["source_checksum_sha256"]),
        ],
        "tblCases": [
            ("ix_case_local_sender_case_id", ["local_sender_case_id"]),
            ("ix_case_e2b_import_batch", ["e2b_import_batch_id"]),
        ],
        "tblPatients": [("ix_patient_import_batch", ["import_batch_id"])],
        "tblCaseProducts": [("ix_case_product_import_batch", ["import_batch_id"])],
        "tblReactions": [("ix_reaction_import_batch", ["import_batch_id"])],
        "tblAttachments": [("ix_attachment_import_batch", ["import_batch_id"])],
    }.items():
        for index_name, columns in indexes:
            op.create_index(index_name, table, columns, if_not_exists=True)


def downgrade() -> None:
    for table, indexes in {
        "tblAttachments": ["ix_attachment_import_batch"],
        "tblReactions": ["ix_reaction_import_batch"],
        "tblCaseProducts": ["ix_case_product_import_batch"],
        "tblPatients": ["ix_patient_import_batch"],
        "tblCases": ["ix_case_e2b_import_batch", "ix_case_local_sender_case_id"],
        "tblSafetyReports": ["ix_safety_report_source_checksum", "ix_safety_report_e2b_message", "ix_safety_report_import_batch"],
    }.items():
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        if inspector.has_table(table):
            existing_indexes = {index["name"] for index in inspector.get_indexes(table)}
            for index_name in indexes:
                if index_name in existing_indexes:
                    op.drop_index(index_name, table_name=table)

    drop_columns_if_present("tblAttachments", ["duplicate_of_attachment_id", "e2b_source", "import_batch_id"])
    drop_columns_if_present("tblReactions", ["import_batch_id", "null_flavor", "reporter_comment", "medically_confirmed", "duration_text", "end_date_precision", "onset_date_precision", "term_country_code", "term_language", "meddra_version"])
    drop_columns_if_present("tblCaseProducts", ["import_batch_id", "null_flavor", "causality_text", "authorization_holder", "authorization_country_code", "authorization_number", "expiry_date_raw", "expiry_date", "duration_text", "therapy_end_date_precision", "therapy_start_date_precision", "dosage_form", "substance_match_status", "product_match_status", "normalized_product_name"])
    drop_columns_if_present("tblPatients", ["import_batch_id", "death_cause", "death_date_precision", "death_date_raw", "death_date", "date_of_birth_precision", "age_group", "country_code"])
    drop_columns_if_present("tblCases", ["e2b_import_batch_id", "data_review_status", "source_country_code", "local_sender_case_id"])
    drop_columns_if_present("tblSafetyReports", ["source_checksum_sha256", "e2b_followup_type", "e2b_message_version", "e2b_message_identifier", "import_item_id", "import_batch_id"])

    for table_name, indexes in [
        ("tblCaseNarratives", ["ix_case_narrative_type", "ix_case_narrative_case"]),
        ("tblCaseLiteratureReferences", ["ix_case_literature_doi", "ix_case_literature_pmid", "ix_case_literature_case"]),
        ("tblCaseDrugSubstances", ["ix_case_drug_substance_substance", "ix_case_drug_substance_case_product"]),
        ("tblE2BTransmissions", ["ix_tblE2BTransmissions_xml_checksum_sha256", "ix_e2b_transmission_message", "ix_e2b_transmission_case"]),
        ("tblE2BImportIssues", ["ix_e2b_issue_item", "ix_e2b_issue_batch_severity"]),
        ("tblE2BImportItems", ["ix_tblE2BImportItems_case_identifier", "ix_tblE2BImportItems_message_identifier", "ix_e2b_item_checksum", "ix_e2b_item_batch_role"]),
        ("tblE2BImportBatches", ["ix_e2b_batch_checksum", "ix_e2b_batch_status_uploaded"]),
    ]:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        if inspector.has_table(table_name):
            existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
            for index_name in indexes:
                if index_name in existing_indexes:
                    op.drop_index(index_name, table_name=table_name)
            op.drop_table(table_name)
