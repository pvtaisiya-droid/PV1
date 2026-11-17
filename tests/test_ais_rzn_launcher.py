import json
import sqlite3

import pytest

from tools.ais_rzn_launcher import (
    DEFAULT_URL, LEGACY_DEFAULT_URL, browser_command, load_partners,
    load_settings, profile_directory, save_settings, validate_url,
)


def test_partner_profiles_are_persistent_and_isolated(tmp_path):
    first = profile_directory(tmp_path, "partner-a", "chrome")
    assert first == profile_directory(tmp_path, "partner-a", "chrome")
    assert first != profile_directory(tmp_path, "partner-b", "chrome")
    assert first != profile_directory(tmp_path, "partner-a", "edge")
    assert profile_directory(tmp_path, "../../outside", "chrome").is_relative_to(tmp_path)


@pytest.mark.parametrize("url", [
    "http://example.com/", "javascript:alert(1)", "file:///C:/test",
    "https://login:secret@external.roszdravnadzor.ru/", "https://", "--incognito",
    'https://example.com/" --user-data-dir=other', "https://example.com/\npath",
    "https://example.com:invalid/", "https://example.com\\@other.example/",
    "http://external.roszdravnadzor.ru.evil.example/",
    "http://external.roszdravnadzor.ru@evil.example/",
    "http://external.roszdravnadzor.ru:8080/",
])
def test_invalid_urls_are_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_official_http_entry_point_is_allowed(tmp_path):
    assert validate_url(DEFAULT_URL) == DEFAULT_URL
    command = browser_command(tmp_path / "chrome.exe", tmp_path / "profile", DEFAULT_URL)
    assert command[-1] == "http://external.roszdravnadzor.ru/"


def test_previous_default_is_repaired_without_changing_custom_urls_or_browser(tmp_path):
    original = {"last_partner_id": "1", "partners": {
        "1": {"browser": "chrome", "url": LEGACY_DEFAULT_URL},
        "2": {"browser": "edge", "url": "https://example.com/login"},
        "3": {"browser": "chrome", "url": LEGACY_DEFAULT_URL + "custom-login"},
    }}
    save_settings(tmp_path, original)
    restored = load_settings(tmp_path)
    assert restored["partners"]["1"] == {"browser": "chrome", "url": DEFAULT_URL}
    assert restored["partners"]["2"] == original["partners"]["2"]
    assert restored["partners"]["3"] == original["partners"]["3"]
    assert restored["last_partner_id"] == "1"


def test_browser_launch_keeps_paths_and_url_as_separate_arguments(tmp_path):
    executable = tmp_path / "Program Files" / "chrome.exe"
    profile = profile_directory(tmp_path / "Данные партнёра", "partner-id", "chrome")
    url = "https://external.roszdravnadzor.ru/?section=login&lang=ru"
    command = browser_command(executable, profile, url)
    assert command[0] == str(executable)
    assert f"--user-data-dir={profile}" in command
    assert command[-1] == url
    assert not any(value in command for value in ("--incognito", "--guest", "--enable-automation"))


def test_partner_list_does_not_modify_database_or_include_deleted_records(tmp_path):
    path = tmp_path / "partners.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE tblPartners (id TEXT, partner_code TEXT, partner_name TEXT, is_deleted INTEGER)")
        db.executemany("INSERT INTO tblPartners VALUES (?, ?, ?, ?)", [
            ("1", "A", "Партнёр", 0), ("2", "B", "Удалённый партнёр", 1),
        ])
    before = path.read_bytes()
    assert load_partners(path) == [{"id": "1", "partner_code": "A", "partner_name": "Партнёр"}]
    assert path.read_bytes() == before
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        load_partners(missing)
    assert not missing.exists()


def test_settings_only_store_launch_preferences(tmp_path):
    save_settings(tmp_path, {
        "last_partner_id": "1",
        "password": "must-not-be-saved",
        "partners": {"1": {"browser": "chrome", "url": "https://example.com/", "password": "secret"}},
    })
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved == {"last_partner_id": "1", "partners": {"1": {"browser": "chrome", "url": "https://example.com/"}}}
