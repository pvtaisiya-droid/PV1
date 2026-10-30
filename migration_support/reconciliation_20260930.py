"""Comparison rules for revision 0026, independent of application models."""
import sqlalchemy as sa


def comparison_metadata(source):
    """Break ordering cycles without removing any FK from schema comparison.

    Only the three back-references from an import batch are deferred. SQLite
    batch operations still create these constraints inside the rebuilt table.
    Work on a copy: the frozen schema and live ORM metadata remain untouched.
    """
    target = sa.MetaData()
    for table in source.tables.values():
        table.to_metadata(target)
    batch = target.tables.get("tblE2BImportBatches")
    if batch is not None:
        for constraint in batch.foreign_key_constraints:
            if tuple(constraint.column_keys) in {
                ("created_case_id",), ("created_incoming_request_id",),
                ("created_safety_report_id",),
            }:
                constraint.use_alter = True
    return target


def validate_unique_keys(bind, metadata):
    """Reject existing duplicates before DDL; do not log business values."""
    inspector = sa.inspect(bind)
    for table in metadata.tables.values():
        if not inspector.has_table(table.name):
            continue
        present = {column["name"] for column in inspector.get_columns(table.name)}
        keys = [list(key.columns) for key in table.constraints
                if isinstance(key, sa.UniqueConstraint)]
        keys += [list(index.columns) for index in table.indexes if index.unique]
        for columns in keys:
            if not all(column.name in present for column in columns):
                continue
            duplicates = sa.select(*columns).where(
                *(column.is_not(None) for column in columns)
            ).group_by(*columns).having(sa.func.count() > 1).subquery()
            count = bind.scalar(sa.select(sa.func.count()).select_from(duplicates))
            if count:
                names = ", ".join(column.name for column in columns)
                raise RuntimeError(
                    f"Duplicate values in {table.name} ({names}): {count} groups. "
                    "No records were renamed. Review duplicate records before upgrading."
                )
