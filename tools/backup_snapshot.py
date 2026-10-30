"""Offline SQLite + storage + source snapshots; never import or start the app."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess


# original_path/technical_path in E2B are archive/XML locations, not disk files.
FILE_COLUMNS = {
    "storage_path", "document_path", "file_path", "docx_file_path",
    "pdf_file_path", "package_file_path", "source_zip_path", "stored_path",
    "storage_dir",
}
SOURCE_DIRS = {"app", "alembic", "migration_support", "tests", "examples"}
SOURCE_FILES = {
    ".env.example", ".gitignore", "AGENTS.md", "README.md", "passport_PV1.md",
    "alembic.ini", "requirements.txt", "requirements.lock.txt",
    "requirements-dev.txt", "requirements-dev.lock.txt", "run.py",
    "start.ps1", "start.cmd", "open-ais-rzn.cmd",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def inventory(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Linked files/directories are not supported")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                "size": path.stat().st_size, "sha256": digest(path),
            }
    return result


def readonly(database: Path) -> sqlite3.Connection:
    return sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def database_report(database: Path, original_root: Path,
                    original_storage: Path, copied_storage: Path) -> dict:
    """Resolve original DB paths against the copied tree, never the live files."""
    references = []
    with closing(readonly(database)) as db:
        integrity = [row[0] for row in db.execute("PRAGMA integrity_check")]
        foreign_keys = [list(row) for row in db.execute("PRAGMA foreign_key_check")]
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        counts = {table: db.execute(f"SELECT count(*) FROM {quote(table)}").fetchone()[0]
                  for table in tables}
        revision = ([row[0] for row in db.execute("SELECT version_num FROM alembic_version")]
                    if "alembic_version" in tables else [])
        for table in tables:
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({quote(table)})")}
            for column in sorted(columns & FILE_COLUMNS):
                key = '"id"' if "id" in columns else "rowid"
                for record_id, value in db.execute(
                    f"SELECT {key}, {quote(column)} FROM {quote(table)} "
                    f"WHERE {quote(column)} IS NOT NULL AND {quote(column)} != ''"
                ):
                    original = (original_root / value).resolve()
                    relative = None
                    if original.is_relative_to(original_storage):
                        relative = original.relative_to(original_storage).as_posix()
                        copied = copied_storage / relative
                        exists = copied.is_dir() if column == "storage_dir" else copied.is_file()
                        status = "present" if exists else "missing_in_storage"
                    else:
                        status = "outside_storage"
                    references.append({"table": table, "id": str(record_id), "column": column,
                                       "path": value, "relative": relative, "status": status})
    unresolved = sum(row["status"] != "present" for row in references)
    return {"integrity": integrity, "foreign_key_violations": foreign_keys,
            "revision": revision, "row_counts": counts, "references": references,
            "unresolved_references": unresolved,
            "recovery_complete": integrity == ["ok"] and not foreign_keys and not unresolved}


def source_files(root: Path) -> tuple[list[str], list[str]]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
    ).decode("utf-8")
    included, excluded = [], []
    for name in sorted(set(output.split("\0")) - {""}):
        path = Path(name)
        selected = (name in SOURCE_FILES or path.parts[0] in SOURCE_DIRS
                    or (path.parts[0] in {"tools", "migration_access"} and path.suffix == ".py")
                    or (path.parts[0] == "docs" and
                        (path.suffix == ".md" or name == "docs/contract_import.schema.json")))
        if path.suffix in {".log", ".db", ".sqlite", ".sqlite3", ".pyc"}:
            selected = False
        if any(part.startswith(".tmp-") or part == "__pycache__" for part in path.parts):
            selected = False
        (included if selected else excluded).append(name)
    return included, excluded


def database_state(database: Path) -> dict:
    return {path.name: digest(path) for path in
            (database, Path(str(database) + "-wal")) if path.exists()}


def create_snapshot(root: Path, database: Path, storage: Path, destination: Path,
                    *, offline: bool = False) -> dict:
    if not offline:
        raise ValueError("Stop all application/import writers and specify --offline")
    root, database, storage, destination = [p.resolve() for p in (root, database, storage, destination)]
    if not database.is_file() or not storage.is_dir():
        raise ValueError("Database and storage must exist; no empty substitutes")
    if destination == root or destination.is_relative_to(storage) or root.is_relative_to(destination):
        raise ValueError("Destination overlaps the source")
    destination.mkdir(parents=True, exist_ok=False)
    before_database = database_state(database)
    before_storage = inventory(storage)
    with closing(readonly(database)) as source, closing(sqlite3.connect(destination / "database.sqlite3")) as target:
        source.backup(target)
    shutil.copytree(storage, destination / "storage")
    included, excluded = source_files(root)
    for name in included:
        source = root / name
        if not source.resolve().is_relative_to(root) or source.is_symlink():
            raise ValueError("Source links are not supported")
        target = destination / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # A manifest is only published once both data sources have remained unchanged.
    if (before_database != database_state(database) or before_storage != inventory(storage)
            or before_storage != inventory(destination / "storage")):
        raise ValueError("Sources changed while copying; snapshot is incomplete")
    for name in included:
        if digest(root / name) != digest(destination / "source" / name):
            raise ValueError("Source code changed while copying; snapshot is incomplete")
    report = database_report(destination / "database.sqlite3", root, storage, destination / "storage")
    write_json(destination / "recovery-report.json", report)
    status = subprocess.check_output(["git", "status", "--porcelain=v1", "-uall"], cwd=root).decode("utf-8")
    manifest = {
        "format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "original_root": str(root), "original_database": str(database), "original_storage": str(storage),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
        "git_status": status.splitlines(), "excluded_from_source": excluded,
        "source_database_state": before_database, "recovery_complete": report["recovery_complete"],
        "files": inventory(destination),
    }
    write_json(destination / "manifest.json", manifest)
    return report


def verify_snapshot(snapshot: Path) -> dict:
    snapshot = snapshot.resolve()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1:
        raise ValueError("Unsupported snapshot format")
    actual = inventory(snapshot)
    del actual["manifest.json"]
    if actual != manifest["files"]:
        raise ValueError("Snapshot file inventory/hash mismatch")
    report = database_report(snapshot / "database.sqlite3", Path(manifest["original_root"]),
                             Path(manifest["original_storage"]), snapshot / "storage")
    saved = json.loads((snapshot / "recovery-report.json").read_text(encoding="utf-8"))
    if report != saved:
        raise ValueError("Restored database/reference report differs from snapshot")
    return report


def restore_snapshot(snapshot: Path, destination: Path) -> dict:
    """Restore byte-for-byte into a new directory. This does not start the app."""
    snapshot, destination = snapshot.resolve(), destination.resolve()
    verify_snapshot(snapshot)
    if destination.is_relative_to(snapshot) or snapshot.is_relative_to(destination):
        raise ValueError("Restore destination overlaps snapshot")
    shutil.copytree(snapshot, destination)  # refuses to overwrite any directory
    return verify_snapshot(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--database", type=Path, required=True)
    create.add_argument("--storage", type=Path, required=True)
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument("--offline", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("snapshot", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("snapshot", type=Path)
    restore.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            report = create_snapshot(args.root, args.database, args.storage, args.destination, offline=args.offline)
        elif args.command == "restore":
            report = restore_snapshot(args.snapshot, args.destination)
        else:
            report = verify_snapshot(args.snapshot)
    except (ValueError, OSError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Snapshot failed ({type(exc).__name__}); inspect inputs and local artifacts.\n")
    print(json.dumps({"snapshot_verified": True, "integrity": report["integrity"],
                      "foreign_key_violations": len(report["foreign_key_violations"]),
                      "references": len(report["references"]),
                      "unresolved_references": report["unresolved_references"],
                      "recovery_complete": report["recovery_complete"]}))
    return 0 if report["recovery_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
