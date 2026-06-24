import os
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pv_system.db")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=Session,
    future=True,
)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401
    from app.psmf import ensure_psmf_seed_data
    from app.rbac import ensure_rbac_defaults
    from app.sop_seed import ensure_sop_demo_data

    Base.metadata.create_all(bind=engine)
    ensure_sqlite_schema()
    with SessionLocal() as db:
        ensure_rbac_defaults(db)
        ensure_psmf_seed_data(db)
        ensure_sop_demo_data(db)


def ensure_sqlite_schema() -> None:
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    if not inspector.has_table("tblPartners"):
        return

    with engine.begin() as connection:
        ensure_soft_delete_columns(inspector, connection)
        ensure_audit_log_columns(inspector, connection)
        ensure_mvp_module_columns(inspector, connection)

        columns = {column["name"] for column in inspector.get_columns("tblPartners")}
        if "reconciliation_frequency" not in columns:
            connection.execute(
                text(
                    'ALTER TABLE "tblPartners" '
                    "ADD COLUMN reconciliation_frequency VARCHAR(50) "
                    "NOT NULL DEFAULT 'not_conducted'"
                )
            )
        connection.execute(
            text(
                """
                UPDATE "tblPartners"
                SET reconciliation_frequency = 'not_conducted'
                WHERE reconciliation_frequency IS NULL
                   OR reconciliation_frequency NOT IN (
                       'monthly',
                       'quarterly',
                       'not_conducted'
                   )
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE "tblPartners"
                SET partner_type = 'fn'
                WHERE partner_type IS NULL
                   OR partner_type NOT IN (
                       'archive',
                       'fn',
                       'registration_in_progress'
                   )
                """
            )
        )


def ensure_audit_log_columns(inspector, connection) -> None:
    if not inspector.has_table("tblAuditTrail"):
        return

    from app.audit import ENTITY_SOURCE_MODULES

    columns = {column["name"] for column in inspector.get_columns("tblAuditTrail")}
    audit_columns = {
        "changed_by": "VARCHAR(36)",
        "changed_at": "DATETIME",
        "source_module": "VARCHAR(100)",
        "comment": "TEXT",
        "ip_address": "VARCHAR(100)",
        "correlation_id": "VARCHAR(100)",
    }
    for column_name, ddl in audit_columns.items():
        if column_name not in columns:
            connection.execute(
                text(f'ALTER TABLE "tblAuditTrail" ADD COLUMN {column_name} {ddl}')
            )

    connection.execute(
        text(
            """
            UPDATE "tblAuditTrail"
            SET changed_by = user_id
            WHERE changed_by IS NULL
            """
        )
    )
    connection.execute(
        text(
            """
            UPDATE "tblAuditTrail"
            SET changed_at = timestamp
            WHERE changed_at IS NULL
            """
        )
    )
    connection.execute(
        text(
            """
            UPDATE "tblAuditTrail"
            SET comment = change_reason
            WHERE comment IS NULL
              AND change_reason IS NOT NULL
            """
        )
    )
    for entity_type, source_module in ENTITY_SOURCE_MODULES.items():
        connection.execute(
            text(
                """
                UPDATE "tblAuditTrail"
                SET source_module = :source_module
                WHERE source_module IS NULL
                  AND entity_type = :entity_type
                """
            ),
            {"entity_type": entity_type, "source_module": source_module},
        )

    connection.execute(
        text(
            'CREATE INDEX IF NOT EXISTS "ix_audit_changed_by" '
            'ON "tblAuditTrail" (changed_by)'
        )
    )
    connection.execute(
        text(
            'CREATE INDEX IF NOT EXISTS "ix_audit_changed_at" '
            'ON "tblAuditTrail" (changed_at)'
        )
    )
    connection.execute(
        text(
            'CREATE INDEX IF NOT EXISTS "ix_audit_source_module" '
            'ON "tblAuditTrail" (source_module)'
        )
    )


def ensure_soft_delete_columns(inspector, connection) -> None:
    soft_delete_columns = {
        "deleted_at": "DATETIME",
        "deleted_by": "VARCHAR(36)",
        "delete_reason": "TEXT",
    }
    table_names = [
        "tblUsers",
        "tblPartners",
        "tblSubstances",
        "tblProducts",
        "tblContracts",
        "tblContractContacts",
        "tblPartnerReconciliations",
        "tblPartnerReconciliationItems",
        "tblProductSubstances",
        "tblSafetyReports",
        "tblCases",
        "tblPatients",
        "tblCaseProducts",
        "tblReactions",
        "tblCaseProductReactionAssessments",
        "tblFollowUps",
        "tblAttachments",
        "tblDocumentVersions",
        "tblSubmissions",
        "tblPSURPlans",
        "tblPSURProducts",
        "tblPSURCases",
        "tblPSURPartnerRequests",
        "tblPSURSections",
        "tblPSURDocuments",
        "tblLiteratureMonitoringPlans",
        "tblLiteratureMonitoringPlanProducts",
        "tblLiteratureSearchLogs",
        "tblLiteratureSearchLogProducts",
        "tblLiteratureResults",
        "tblLiteratureResultProducts",
        "tblTasks",
        "tblSOPs",
        "tblSOPVersions",
        "psmf_components",
        "psmf_component_versions",
        "tblAuditTrail",
    ]
    for table_name in table_names:
        if not inspector.has_table(table_name):
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, ddl in soft_delete_columns.items():
            if column_name not in columns:
                connection.execute(
                    text(f'ALTER TABLE "{table_name}" ADD COLUMN {column_name} {ddl}')
                )


def ensure_mvp_module_columns(inspector, connection) -> None:
    table_columns = {
        "tblPartners": {
            "created_by": "VARCHAR(36)",
            "updated_by": "VARCHAR(36)",
        },
        "tblProducts": {
            "created_by": "VARCHAR(36)",
            "updated_by": "VARCHAR(36)",
        },
        "tblContracts": {
            "created_by": "VARCHAR(36)",
            "updated_by": "VARCHAR(36)",
            "parent_contract_id": "VARCHAR(36)",
        },
        "tblCases": {
            "created_by": "VARCHAR(36)",
            "updated_by": "VARCHAR(36)",
        },
        "tblPartnerReconciliations": {
            "reconciliation_type": "VARCHAR(100) NOT NULL DEFAULT 'periodic'",
            "products": "TEXT",
            "document_path": "VARCHAR(500)",
            "document_filename": "VARCHAR(255)",
            "document_format": "VARCHAR(20) DEFAULT 'xlsx'",
            "email_subject": "VARCHAR(500)",
            "email_body": "TEXT",
            "email_to": "TEXT",
            "email_cc": "TEXT",
            "outlook_message_id": "VARCHAR(255)",
            "outlook_draft_web_link": "VARCHAR(1000)",
            "outlook_status": "VARCHAR(50) NOT NULL DEFAULT 'not_created'",
            "outlook_error": "TEXT",
            "sent_date": "DATE",
            "response_date": "DATE",
            "discrepancy_description": "TEXT",
            "document_id": "VARCHAR(36)",
            "generated_at": "DATETIME",
            "draft_created_at": "DATETIME",
            "sent_at": "DATETIME",
            "comments": "TEXT",
            "created_by": "VARCHAR(36)",
            "updated_by": "VARCHAR(36)",
        },
        "tblPartnerReconciliationItems": {
            "discrepancy_flag": "BOOLEAN NOT NULL DEFAULT 0",
            "discrepancy_comment": "TEXT",
        },
        "tblContractContacts": {
            "is_pv_contact": "BOOLEAN NOT NULL DEFAULT 1",
            "is_reconciliation_recipient": "BOOLEAN NOT NULL DEFAULT 1",
            "cc_reconciliation": "BOOLEAN NOT NULL DEFAULT 0",
            "is_primary": "BOOLEAN NOT NULL DEFAULT 0",
            "contact_type": "VARCHAR(100) DEFAULT 'pv'",
            "comments": "TEXT",
        },
        "tblIncomingRequests": {
            "case_id": "VARCHAR(36)",
        },
        "tblAttachments": {
            "document_title": "VARCHAR(255)",
            "document_type": "VARCHAR(100)",
            "related_object_type": "VARCHAR(100)",
            "related_object_id": "VARCHAR(36)",
            "partner_id": "VARCHAR(36)",
            "product_id": "VARCHAR(36)",
            "file_url": "VARCHAR(500)",
            "document_version": "VARCHAR(50)",
            "document_date": "DATE",
            "status": "VARCHAR(50)",
            "comment": "TEXT",
            "created_by": "VARCHAR(36)",
            "updated_by": "VARCHAR(36)",
        },
        "tblTasks": {
            "responsible_person": "VARCHAR(255)",
            "updated_by": "VARCHAR(36)",
            "comment": "TEXT",
        },
        "tblSOPs": {
            "sop_code": "VARCHAR(100)",
            "title": "VARCHAR(255)",
            "document_type": "VARCHAR(100)",
            "status": "VARCHAR(50)",
            "process_area": "VARCHAR(100)",
            "owner": "VARCHAR(255)",
            "reviewer": "VARCHAR(255)",
            "approver": "VARCHAR(255)",
            "approval_date": "DATE",
            "effective_date": "DATE",
            "next_review_date": "DATE",
            "revision_reason": "TEXT",
            "file_path": "VARCHAR(500)",
            "file_url": "VARCHAR(500)",
            "description": "TEXT",
            "training_required": "BOOLEAN",
            "created_by": "VARCHAR(36)",
            "updated_by": "VARCHAR(36)",
        },
    }
    for table_name, columns_to_add in table_columns.items():
        if not inspector.has_table(table_name):
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, ddl in columns_to_add.items():
            if column_name in columns:
                continue
            connection.execute(
                text(f'ALTER TABLE "{table_name}" ADD COLUMN {column_name} {ddl}')
            )
            columns.add(column_name)

    if inspector.has_table("tblContractContacts"):
        connection.execute(
            text(
                """
                UPDATE "tblContractContacts"
                SET is_pv_contact = 1
                WHERE is_pv_contact IS NULL
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE "tblContractContacts"
                SET is_reconciliation_recipient = 1
                WHERE is_reconciliation_recipient IS NULL
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE "tblContractContacts"
                SET cc_reconciliation = 0
                WHERE cc_reconciliation IS NULL
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE "tblContractContacts"
                SET is_primary = 0
                WHERE is_primary IS NULL
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE "tblContractContacts"
                SET contact_type = 'pv'
                WHERE contact_type IS NULL OR contact_type = ''
                """
            )
        )

    if inspector.has_table("tblContracts"):
        ensure_contract_parent_schema(connection)

    if inspector.has_table("tblIncomingRequests"):
        connection.execute(
            text(
                'CREATE INDEX IF NOT EXISTS "ix_incoming_request_case" '
                'ON "tblIncomingRequests" (case_id)'
            )
        )

    if inspector.has_table("tblPartnerReconciliations"):
        connection.execute(
            text(
                """
                UPDATE "tblPartnerReconciliations"
                SET reconciliation_type = 'periodic'
                WHERE reconciliation_type IS NULL OR reconciliation_type = ''
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE "tblPartnerReconciliations"
                SET document_format = 'xlsx'
                WHERE document_format IS NULL OR document_format = ''
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE "tblPartnerReconciliations"
                SET outlook_status = 'not_created'
                WHERE outlook_status IS NULL OR outlook_status = ''
                """
            )
        )

    if inspector.has_table("tblPartnerReconciliationItems"):
        connection.execute(
            text(
                """
                UPDATE "tblPartnerReconciliationItems"
                SET discrepancy_flag = CASE
                    WHEN reconciliation_status NOT IN ('matched', 'confirmed') THEN 1
                    ELSE 0
                END
                WHERE discrepancy_flag IS NULL
                """
            )
        )


def sqlite_contract_has_legacy_unique_index(connection) -> bool:
    indexes = connection.execute(text('PRAGMA index_list("tblContracts")')).mappings().all()
    for index in indexes:
        if not index.get("unique"):
            continue
        index_name = str(index["name"]).replace('"', '""')
        indexed_columns = [
            row["name"]
            for row in connection.execute(
                text(f'PRAGMA index_info("{index_name}")')
            ).mappings()
        ]
        if indexed_columns == ["partner_id", "product_id"]:
            return True
    return False


def ensure_contract_parent_schema(connection) -> None:
    if not sqlite_contract_has_legacy_unique_index(connection):
        connection.execute(
            text(
                'CREATE INDEX IF NOT EXISTS "ix_contract_parent_contract" '
                'ON "tblContracts" (parent_contract_id)'
            )
        )
        return

    connection.execute(
        text(
            """
            CREATE TABLE "tblContracts_new" (
                id VARCHAR(36) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                is_active BOOLEAN NOT NULL,
                is_deleted BOOLEAN NOT NULL,
                deleted_at DATETIME,
                deleted_by VARCHAR(36),
                delete_reason TEXT,
                version INTEGER NOT NULL,
                partner_id VARCHAR(36) NOT NULL,
                product_id VARCHAR(36) NOT NULL,
                parent_contract_id VARCHAR(36),
                contract_type VARCHAR(100) NOT NULL,
                contract_number VARCHAR(100) NOT NULL,
                contract_date DATE NOT NULL,
                valid_until DATE NOT NULL,
                created_by VARCHAR(36),
                updated_by VARCHAR(36),
                PRIMARY KEY (id),
                FOREIGN KEY(partner_id) REFERENCES "tblPartners" (id),
                FOREIGN KEY(product_id) REFERENCES "tblProducts" (id),
                FOREIGN KEY(parent_contract_id) REFERENCES "tblContracts" (id),
                FOREIGN KEY(created_by) REFERENCES "tblUsers" (id),
                FOREIGN KEY(updated_by) REFERENCES "tblUsers" (id)
            )
            """
        )
    )
    copy_columns = [
        "id",
        "created_at",
        "updated_at",
        "is_active",
        "is_deleted",
        "deleted_at",
        "deleted_by",
        "delete_reason",
        "version",
        "partner_id",
        "product_id",
        "parent_contract_id",
        "contract_type",
        "contract_number",
        "contract_date",
        "valid_until",
        "created_by",
        "updated_by",
    ]
    column_sql = ", ".join(f'"{column}"' for column in copy_columns)
    connection.execute(
        text(
            f"""
            INSERT INTO "tblContracts_new" ({column_sql})
            SELECT {column_sql}
            FROM "tblContracts"
            """
        )
    )
    connection.execute(text('DROP TABLE "tblContracts"'))
    connection.execute(text('ALTER TABLE "tblContracts_new" RENAME TO "tblContracts"'))
    for statement in [
        'CREATE INDEX IF NOT EXISTS "ix_contract_partner_product" '
        'ON "tblContracts" (partner_id, product_id)',
        'CREATE INDEX IF NOT EXISTS "ix_contract_parent_contract" '
        'ON "tblContracts" (parent_contract_id)',
        'CREATE INDEX IF NOT EXISTS "ix_contract_valid_until" '
        'ON "tblContracts" (valid_until)',
        'CREATE INDEX IF NOT EXISTS "ix_tblContracts_partner_id" '
        'ON "tblContracts" (partner_id)',
        'CREATE INDEX IF NOT EXISTS "ix_tblContracts_product_id" '
        'ON "tblContracts" (product_id)',
        'CREATE INDEX IF NOT EXISTS "ix_tblContracts_contract_type" '
        'ON "tblContracts" (contract_type)',
        'CREATE INDEX IF NOT EXISTS "ix_tblContracts_contract_number" '
        'ON "tblContracts" (contract_number)',
    ]:
        connection.execute(text(statement))
