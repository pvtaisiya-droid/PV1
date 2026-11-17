import sys
import os
import shutil
import subprocess
import gc

import pytest
from fastapi.testclient import TestClient


TEST_ADMIN_LOGIN = "test-admin"
TEST_ADMIN_PASSWORD = "Test-admin-2026!"


def clear_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            del sys.modules[module_name]


@pytest.fixture(scope="session")
def database_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("installation")
    database = root / "template.db"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    environment["PV_STORAGE_ROOT"] = str(root / "uploads")
    result = subprocess.run([sys.executable, "-m", "app.manage", "init-db"],
                            env=environment, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return database


@pytest.fixture()
def client(tmp_path, monkeypatch, database_template):
    database_path = tmp_path / "pv_test.db"
    shutil.copy2(database_template, database_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("PV_STORAGE_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("PV_APP_MODE", "dev")
    monkeypatch.setenv("PV_USER_SWITCH_ENABLED", "true")
    monkeypatch.setenv("PV_ALLOW_QUERY_USER_SWITCH", "false")
    monkeypatch.setenv("PV_DEFAULT_PAGE_SIZE", "5")
    monkeypatch.setenv("PV_MAX_PAGE_SIZE", "20")
    clear_app_modules()

    from app.main import app
    from app.manage import bootstrap_admin
    bootstrap_admin(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)

    with TestClient(app) as test_client:
        login_response = test_client.post(
            "/login",
            data={
                "login": TEST_ADMIN_LOGIN,
                "password": TEST_ADMIN_PASSWORD,
                "next_url": "/",
            },
            follow_redirects=False,
        )
        assert login_response.status_code == 303
        yield test_client

    from app.database import engine, Base
    engine.dispose()
    # Each client reloads declarative models. Release mapper caches before
    # removing their modules so long concurrency suites do not retain them.
    Base.registry.dispose()
    clear_app_modules()
    gc.collect()
