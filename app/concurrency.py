import hashlib
import json

from sqlalchemy import inspect, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import StaleDataError
from fastapi import HTTPException


def require_version(record, expected_version: int) -> None:
    """Check the version rendered to the caller before making any changes.

    The mapper's version predicate still protects the later flush if another
    transaction commits after this check. Document labels are not row versions.
    """
    column = inspect(type(record)).version_id_col
    if column is None:
        raise ValueError(f"{type(record).__name__} has no concurrency version")
    if expected_version != getattr(record, column.key):
        raise StaleDataError("Record has changed. Reload before saving.")


def protect_record(db, model, record_id, expected_version):
    """Claim a caller's version, including operations that only change children."""
    record = db.get(model, record_id)
    if record is None or getattr(record, "is_deleted", False):
        raise HTTPException(status_code=404, detail="Record not found")
    require_version(record, expected_version)
    column = inspect(model).version_id_col
    setattr(record, column.key, getattr(record, column.key) + 1)
    return record


def snapshot_token(db, model_names):
    """Fingerprint persisted inputs, including membership and soft deletions.

    Snapshot-based writes serialize before reading on SQLite. Other dialects
    lock the input tables' current rows until the operation commits.
    """
    from app import models
    result = []
    for name in sorted(model_names):
        model = getattr(models, name)
        table = model.__table__
        # Include business columns too: historical importers may not bump a
        # version. Values never leave the database as plaintext in the token.
        rows = db.execute(select(table).order_by(*table.primary_key.columns)).all()
        result.append((name, [tuple(row) for row in rows]))
    return hashlib.sha256(json.dumps(result, default=str, ensure_ascii=False).encode()).hexdigest()


def require_snapshot(db, model_names, expected_snapshot):
    if not expected_snapshot or len(expected_snapshot) != 64:
        raise HTTPException(status_code=422, detail="Expected snapshot is required")
    if snapshot_token(db, model_names) != expected_snapshot:
        raise StaleDataError("Preview inputs have changed. Reload before saving.")


ONBOARDING_INPUTS = ('Partner', 'Contract', 'ContractContact', 'Product', 'Substance',
    'DosageForm', 'ProductRegistrationCertificate', 'PartnerProductRegistrationLink',
    'ProductSubstance', 'ContractProduct', 'ContractAccountingTariff', 'AccountingService', 'Attachment')
IMPORT_INPUTS = ONBOARDING_INPUTS + ('PartnerAccountingInfo',)
PSMF_INPUTS = ONBOARDING_INPUTS + ('PSMFComponent', 'PSMFComponentVersion', 'PSMFBlock',
    'PSMFBlockVersion', 'PSMFSettings', 'PSMFComment', 'DocumentVersion', 'PSMFDocumentLink',
    'PSMFPartnerBuild', 'PSMFPartnerBuildItem', 'PSMFBuildDocument',
    'User', 'UserRole', 'Role', 'SOP', 'PSURPlan', 'AuditTrail')
CASE_INPUTS = ONBOARDING_INPUTS + ('Case', 'CaseProduct', 'Reaction', 'Patient',
    'CasePartnerLink', 'CaseIdentifier', 'CaseDrugSubstance', 'CaseNarrative',
    'CaseLiteratureReference', 'CaseProductReactionAssessment', 'FollowUp',
    'SafetyReport', 'SafetyReportProduct')
PSUR_INPUTS = CASE_INPUTS + ('LiteratureResult', 'LiteratureResultProduct', 'PSURPartnerRequest')
LITERATURE_INPUTS = ONBOARDING_INPUTS + ('LiteratureMonitoringPlan', 'LiteratureMonitoringPlanProduct',
    'LiteratureSearchLog', 'LiteratureSearchLogProduct', 'LiteratureResultProduct', 'PSURPlan', 'PSMFComponent')


def lock_snapshot_inputs(db, model_names):
    """Acquire a write reservation before comparing a derived-data preview."""
    if db.bind.dialect.name == 'sqlite':
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            try:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
            except OperationalError as exc:
                if 'locked' not in str(exc.orig).lower():
                    raise
                db.rollback()
                raise StaleDataError('Database is busy. Reload before saving.') from exc
    elif db.bind.dialect.name == 'postgresql':
        from app import models
        names = sorted({getattr(models, name).__table__.name for name in model_names})
        quote = db.bind.dialect.identifier_preparer.quote
        db.connection().exec_driver_sql('LOCK TABLE ' + ', '.join(quote(name) for name in names) + ' IN SHARE ROW EXCLUSIVE MODE')
    else:
        raise RuntimeError('Snapshot writes require SQLite or PostgreSQL')


def protect_snapshot(db, model_names, expected_snapshot):
    lock_snapshot_inputs(db, model_names)
    require_snapshot(db, model_names, expected_snapshot)
