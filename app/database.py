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

    Base.metadata.create_all(bind=engine)
    ensure_sqlite_schema()
    with SessionLocal() as db:
        ensure_rbac_defaults(db)
        ensure_psmf_seed_data(db)


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
        "tblSubmissions",
        "tblPSURPlans",
        "tblPSURProducts",
        "tblPSURCases",
        "tblPSURPartnerRequests",
        "tblPSURSections",
        "tblPSURDocuments",
        "tblTasks",
        "tblSOPs",
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
        },
        "tblCases": {
            "created_by": "VARCHAR(36)",
            "updated_by": "VARCHAR(36)",
        },
        "tblPartnerReconciliations": {
            "products": "TEXT",
            "sent_date": "DATE",
            "response_date": "DATE",
            "discrepancy_description": "TEXT",
            "document_id": "VARCHAR(36)",
            "created_by": "VARCHAR(36)",
            "updated_by": "VARCHAR(36)",
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
