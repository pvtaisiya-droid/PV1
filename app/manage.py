"""Local installation and administrator recovery commands (python -m app.manage)."""
import argparse
from getpass import getpass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.database import SessionLocal, engine, init_db, initialize_reference_data


def migration_config():
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config


def bootstrap_admin(login: str, password: str, *, recover: bool = False):
    from app.auth import hash_password, normalize_login, validate_login, validate_password
    from app.models import AuthSession, Role, RolePermission, Permission, User, UserRole
    from app.audit import log_audit
    from app.rbac import ROLE_PRESETS
    error = validate_login(login) or validate_password(password)
    if error:
        raise ValueError(error)
    init_db()
    with SessionLocal() as db:
        role = db.query(Role).filter(Role.role_code == "admin").one()
        user = db.query(User).filter(User.login == normalize_login(login)).first()
        if recover:
            if not user:
                raise ValueError("Recovery requires an existing login.")
        else:
            if db.query(User).filter(User.password_hash.is_not(None)).first():
                raise ValueError("A password is already configured; bootstrap will not reset it. Use recover-admin explicitly.")
            if user is None:
                user = db.query(User).filter(User.login == "pv.admin").one_or_none()
            if user is None:
                raise ValueError("Initial administrator not found. Run init-db first.")
        user.login = normalize_login(login)
        user.password_hash = hash_password(password)
        user.is_active, user.is_deleted = True, False
        role.is_active, role.is_deleted = True, False
        link = db.query(UserRole).filter_by(user_id=user.id, role_id=role.id).one_or_none()
        if link is None:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        else:
            link.is_active, link.is_deleted = True, False
        if recover:
            for permission in db.query(Permission).filter(Permission.permission_code.in_(ROLE_PRESETS["admin"]["permissions"])):
                permission.is_active, permission.is_deleted = True, False
                grant = db.query(RolePermission).filter_by(role_id=role.id, permission_id=permission.id).one_or_none()
                if grant:
                    grant.is_active, grant.is_deleted = True, False
                else:
                    db.add(RolePermission(role_id=role.id, permission_id=permission.id))
            for session in db.query(AuthSession).filter_by(user_id=user.id):
                session.is_active = False
        log_audit(db, entity_type="User", entity_id=user.id, user_id=user.id,
                  action="admin_recovery" if recover else "admin_bootstrap",
                  change_reason="Explicit local administrator command")
        db.commit()


def check_legacy_schema():
    """Compare the unversioned installation before adopting its known baseline."""
    from migration_support.schema_20260930 import metadata
    from migration_support.reconciliation_20260930 import validate_unique_keys
    inspector = inspect(engine)
    if inspector.has_table("alembic_version"):
        with engine.connect() as connection:
            if connection.execute(text("SELECT version_num FROM alembic_version")).first():
                raise ValueError("This database is already versioned; use upgrade-db.")
    differences = []
    for table in metadata.tables.values():
        if table.name == "outlook_oauth_requests":
            continue
        if not inspector.has_table(table.name):
            differences.append(f"Missing table: {table.name}")
            continue
        columns = {col["name"]: col for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name == "row_version":
                continue
            if column.name not in columns:
                differences.append(f"Missing column: {table.name}.{column.name}")
            elif column.type._type_affinity is not columns[column.name]["type"]._type_affinity:
                differences.append(f"Different type: {table.name}.{column.name}")
    with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            differences += [str(row) for row in connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()]
    if differences:
        raise ValueError("Legacy schema requires review:\n" + "\n".join(differences))
    # Reject duplicate data before adopt-legacy can stamp an unversioned DB.
    with engine.connect() as connection:
        validate_unique_keys(connection, metadata)


def adopt_legacy_schema():
    """Stamp and upgrade together; failed adoption leaves no new revision."""
    check_legacy_schema()
    config = migration_config()
    with engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            # Rebuilding mutually referencing tables requires FK enforcement
            # off on this maintenance connection. Alembic validates all links
            # before committing, and application connections keep it enabled.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        try:
            with connection.begin():
                if sqlite:
                    connection.exec_driver_sql("BEGIN")
                config.attributes["connection"] = connection
                command.stamp(config, "20260824_0025")
                command.upgrade(config, "head")
        finally:
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init-db", "upgrade-db", "check-db", "check-legacy", "adopt-legacy", "bootstrap-admin", "recover-admin"])
    parser.add_argument("--login")
    args = parser.parse_args()
    try:
        if args.action == "init-db":
            if set(inspect(engine).get_table_names()) - {"alembic_version"}:
                raise ValueError("init-db requires an empty database. Use upgrade-db or check-legacy.")
            command.upgrade(migration_config(), "head")
            initialize_reference_data()
        elif args.action == "upgrade-db":
            if not inspect(engine).has_table("alembic_version"):
                raise ValueError("Unversioned database. Run check-legacy and follow README before adoption.")
            command.upgrade(migration_config(), "head")
        elif args.action in {"check-legacy", "adopt-legacy"}:
            if args.action == "adopt-legacy":
                adopt_legacy_schema()
            else:
                check_legacy_schema()
        elif args.action == "check-db":
            init_db()
        else:
            if not args.login:
                raise ValueError("--login is required")
            password = getpass("Password (minimum 12 characters): ")
            if password != getpass("Repeat password: "):
                raise ValueError("Passwords do not match.")
            bootstrap_admin(args.login, password, recover=args.action == "recover-admin")
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
    print("Completed.")


if __name__ == "__main__":
    main()
