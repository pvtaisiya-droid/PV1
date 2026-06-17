import sys

import pytest
from fastapi.testclient import TestClient


def clear_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            del sys.modules[module_name]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    database_path = tmp_path / "pv_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("PV_APP_MODE", "demo")
    monkeypatch.setenv("PV_DEMO_USER_SWITCH", "true")
    monkeypatch.setenv("PV_ALLOW_QUERY_USER_SWITCH", "false")
    monkeypatch.setenv("PV_DEFAULT_PAGE_SIZE", "5")
    monkeypatch.setenv("PV_MAX_PAGE_SIZE", "20")
    clear_app_modules()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    clear_app_modules()
