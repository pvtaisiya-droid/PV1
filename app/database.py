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
    from app.rbac import ensure_rbac_defaults

    Base.metadata.create_all(bind=engine)
    ensure_sqlite_schema()
    with SessionLocal() as db:
        ensure_rbac_defaults(db)


def ensure_sqlite_schema() -> None:
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    if not inspector.has_table("tblPartners"):
        return

    with engine.begin() as connection:
        ensure_soft_delete_columns(inspector, connection)

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
