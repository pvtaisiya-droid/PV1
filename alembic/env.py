from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool, event


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import models  # noqa: E402,F401
from app.database import DATABASE_URL, Base  # noqa: E402


config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def migrate_connection(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
    if connection.dialect.name == "sqlite":
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchmany(20)
        if violations:
            raise RuntimeError(f"Foreign key violations: {violations}")


def run_migrations_online() -> None:
    # A maintenance command may own one transaction across stamp + upgrade.
    supplied = config.attributes.get("connection")
    if supplied is not None:
        migrate_connection(supplied)
        return
    configuration = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    if connectable.dialect.name == "sqlite":
        @event.listens_for(connectable, "connect")
        def migration_connection(connection, record):
            # Batch migrations rebuild tables with cyclic references. Validate
            # all links after the rebuild, inside an explicit DDL transaction.
            connection.isolation_level = None
            connection.execute("PRAGMA foreign_keys=OFF")

        @event.listens_for(connectable, "begin")
        def migration_begin(connection):
            connection.exec_driver_sql("BEGIN")

    with connectable.begin() as connection:
        migrate_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
