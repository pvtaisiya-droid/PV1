"""Bounded stage-0 checks on fresh, synthetic databases; no external services."""
import argparse
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time


PROJECT = Path(__file__).resolve().parents[1]
PREVIOUS = "20261002_0027"
HEAD = "20261002_0028"


def command(*args, **kwargs):
    subprocess.run([sys.executable, *args], cwd=PROJECT, check=True,
                   timeout=120, **kwargs)


def check_database(path):
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def smoke(root, database):
    import httpx

    command("-m", "app.manage", "init-db")
    command("-m", "app.manage", "check-db")
    check_database(database)
    # Use the normal bootstrap function. The one-use credentials remain in RAM.
    login, password = "local-" + secrets.token_hex(6), secrets.token_urlsafe(24)
    command("-c", "import sys; from app.manage import bootstrap_admin; "
            "bootstrap_admin(*sys.stdin.read().splitlines())",
            input=login + "\n" + password, text=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (root / "uvicorn.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port)], cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}",
                              trust_env=False, timeout=5) as client:
                deadline = time.monotonic() + 40
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Uvicorn exited; see isolated uvicorn.log")
                    try:
                        health = client.get("/health")
                        break
                    except httpx.ConnectError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Uvicorn startup exceeded 40 seconds")
                        time.sleep(0.2)
                assert health.status_code == 200 and health.json() == {"status": "ok"}
                denied = client.get("/")
                assert denied.status_code == 303 and denied.headers["location"].startswith("/login")
                page = client.get("/login")
                assert page.status_code == 200 and 'name="password"' in page.text
                invalid = client.post("/login", data={"login": login, "password": "invalid", "next_url": "/"})
                assert invalid.status_code == 401
                accepted = client.post("/login", data={"login": login, "password": password, "next_url": "/"})
                assert accepted.status_code == 303 and "pv_session=" in accepted.headers["set-cookie"]
                dashboard = client.get("/")
                assert dashboard.status_code == 200 and 'href="/safety-reports"' in dashboard.text
                assert 'name="password"' not in dashboard.text
                with sqlite3.connect(database) as db:
                    assert db.execute("SELECT COUNT(*) FROM tblAuthSessions WHERE is_active=1").fetchone()[0] == 1
                print(f"SMOKE PASS: port={port}, pid={process.pid}; health/login/dashboard, invalid password, DB session")
        finally:
            if os.name == "nt" and process.poll() is None:
                # A Windows venv launcher may own a separate interpreter PID.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               check=True, capture_output=True, timeout=10)
            elif process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            print(f"Stopped owned server pid={process.pid}; exit={process.returncode}")
    with socket.socket() as sock:
        sock.settimeout(2)
        assert sock.connect_ex(("127.0.0.1", port)) != 0, "Server port still listening"
    check_database(database)


def upgrade(database):
    command("-m", "alembic", "upgrade", PREVIOUS)
    tables = ("psmf_components", "psmf_component_versions")
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (PREVIOUS,)
        assert "row_version" not in {r[1] for r in db.execute("PRAGMA table_info(psmf_component_versions)")}
        for key, parent in (("parent", None), ("child", "parent")):
            db.execute("""INSERT INTO psmf_components
                (id, code, title, parent_component_id, created_at, updated_at,
                 component_type, scope, status, current_version, is_active, is_deleted, version)
                VALUES (?, ?, ?, ?, '2026-11-10', '2026-11-10',
                        'MAIN_SECTION', 'GLOBAL', 'draft', '0.1', 1, 0, 1)""",
                (key, "STAGE0-" + key, "Synthetic " + key, parent))
            db.execute("""INSERT INTO psmf_component_versions
                (id, component_id, version, content, created_at, updated_at,
                 status, is_locked, is_active, is_deleted)
                VALUES (?, ?, '0.1', ?, '2026-11-10', '2026-11-10', 'draft', 0, 1, 0)""",
                ("version-" + key, key, "Synthetic content " + key))
        columns = {t: [r[1] for r in db.execute(f'PRAGMA table_info("{t}")')] for t in tables}
        before = {t: db.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall() for t in tables}
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    command("-m", "app.manage", "upgrade-db")
    command("-m", "app.manage", "check-db")
    check_database(database)
    with sqlite3.connect(database) as db:
        for table in tables:
            fields = ", ".join('"' + c + '"' for c in columns[table])
            assert db.execute(f'SELECT {fields} FROM "{table}" ORDER BY id').fetchall() == before[table]
        assert db.execute("SELECT row_version FROM psmf_component_versions ORDER BY id").fetchall() == [(1,), (1,)]
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("UPDATE psmf_component_versions SET component_id='missing' WHERE id='version-child'")
        except sqlite3.IntegrityError:
            db.rollback()
        else:
            db.rollback()
            raise AssertionError("Foreign key accepted an invalid reference")
    print(f"UPGRADE PASS: {PREVIOUS} -> {HEAD}; 4 linked rows unchanged; row_version=1; FK rejection; integrity=ok")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("smoke", "upgrade"))
    parser.add_argument("--root", required=True, help="New directory inside .tmp-stage-0")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    allowed = (PROJECT / ".tmp-stage-0").resolve()
    if not root.is_relative_to(allowed) or root == allowed:
        parser.error("Use a new subdirectory of this project's .tmp-stage-0")
    root.mkdir(parents=True, exist_ok=False)
    (root / "storage").mkdir()
    database = root / "database.sqlite3"
    # Set isolation before any application import or database command.
    os.environ.update(DATABASE_URL=f"sqlite:///{database.as_posix()}",
                      PV_STORAGE_ROOT=str(root / "storage"), PV_APP_MODE="prod",
                      PV_USER_SWITCH_ENABLED="false", PV_ALLOW_QUERY_USER_SWITCH="false",
                      PV_AUTH_COOKIE_SECURE="false", PYTHONUTF8="1")
    print(f"DATABASE_URL={os.environ['DATABASE_URL']}\nPV_STORAGE_ROOT={os.environ['PV_STORAGE_ROOT']}", flush=True)
    if args.mode == "smoke":
        smoke(root, database)
    else:
        upgrade(database)


if __name__ == "__main__":
    main()
