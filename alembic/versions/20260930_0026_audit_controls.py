"""Reconcile historical and legacy schemas against a frozen, reviewed target.

Also adds persistent OAuth requests and independent row versions for documents.
No business records or existing audit entries are rewritten by this migration.
"""
import sqlalchemy as sa
from alembic import op
from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from alembic.operations import ops

from migration_support.schema_20260930 import metadata
from migration_support.reconciliation_20260930 import comparison_metadata, validate_unique_keys

revision = "20260930_0026"
down_revision = "20260824_0025"
branch_labels = None
depends_on = None

NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        problems = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"Resolve foreign key violations before migration: {problems[:20]}")
    validate_unique_keys(bind, metadata)
    # The target is versioned with this migration, independent of current models.
    def include_object(obj, name, type_, reflected, compare_to):
        # Preserve Access-import mappings and other legacy archive data.
        return not (reflected and compare_to is None and type_ in {"table", "column"})
    context = MigrationContext.configure(bind, opts={"compare_type": True, "include_object": include_object})
    changes = produce_migrations(context, comparison_metadata(metadata)).upgrade_ops.ops
    for change in changes:
        if isinstance(change, ops.DropTableOp):
            raise RuntimeError(f"Unexpected table {change.table_name}; review schema before upgrading.")
        if isinstance(change, ops.ModifyTableOps):
            if any(isinstance(item, ops.DropColumnOp) for item in change.ops):
                raise RuntimeError(f"Unexpected columns in {change.table_name}; review schema before upgrading.")
            with op.batch_alter_table(change.table_name, naming_convention=NAMING) as batch:
                for item in change.ops:
                    if isinstance(item, ops.CreateForeignKeyOp) and item.constraint_name is None:
                        item.constraint_name = f"fk_{item.source_table}_{item.local_cols[0]}_{item.referent_table}"
                    batch.invoke(item)
        else:
            op.invoke(change)
    if bind.dialect.name == "sqlite":
        problems = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"Foreign key violations after migration: {problems[:20]}")
        for action in ("UPDATE", "DELETE"):
            bind.exec_driver_sql(f'''CREATE TRIGGER IF NOT EXISTS audit_no_{action.lower()}
                BEFORE {action} ON "tblAuditTrail"
                BEGIN SELECT RAISE(ABORT, 'Audit history is append-only'); END''')


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back the schema reconciliation.")
