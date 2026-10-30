"""Open AIS RZN in a persistent browser profile belonging to one PV1 partner.

The launcher never handles credentials. Password saving and sign-in happen in
the browser. Only partner identifiers, the browser choice and a URL are saved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AIS_HOST = "external.roszdravnadzor.ru"
# This legacy portal is officially linked over HTTP; its TLS connection fails.
DEFAULT_URL = f"http://{AIS_HOST}/"
LEGACY_DEFAULT_URL = f"https://{AIS_HOST}/"
BROWSER_NAMES = {"chrome": "Google Chrome", "edge": "Microsoft Edge", "yandex": "Яндекс Браузер"}


def local_data_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "PV1" / "AIS-RZN"


def available_browsers() -> dict[str, Path]:
    locations = {
        "chrome": "Google/Chrome/Application/chrome.exe",
        "edge": "Microsoft/Edge/Application/msedge.exe",
        "yandex": "Yandex/YandexBrowser/Application/browser.exe",
    }
    browsers = {}
    for key, suffix in locations.items():
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base and (path := Path(base) / suffix).is_file():
                browsers[key] = path
                break
    return browsers


def load_partners(database: Path) -> list[dict]:
    # Read SQLite directly: importing the application can run migrations/seeders.
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            'SELECT id, partner_code, partner_name FROM "tblPartners" '
            'WHERE is_deleted = 0 ORDER BY partner_name, partner_code'
        ).fetchall()
    return sorted((dict(row) for row in rows), key=lambda row: row["partner_name"].casefold())


def validate_url(value: str) -> str:
    value = value.strip()
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ValueError("Проверьте адрес страницы входа в АИС.") from exc
    allowed_transport = (
        (parts.scheme == "https" and port in (None, 443))
        or (parts.scheme == "http" and parts.hostname == AIS_HOST and port in (None, 80))
    )
    if (
        not allowed_transport
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or any(character.isspace() or ord(character) < 32 for character in value)
        or any(character in value for character in ('"', "\\"))
    ):
        raise ValueError(
            "Укажите HTTPS-адрес без логина и пароля. Для АИС РЗН также разрешён адрес "
            f"{DEFAULT_URL}"
        )
    return value


def profile_directory(base: Path, partner_id: str, browser: str) -> Path:
    if browser not in BROWSER_NAMES:
        raise ValueError("Выберите браузер.")
    if not partner_id:
        raise ValueError("Выберите партнёра.")
    # Names/codes may change. Use the database ID to preserve saved sessions.
    key = hashlib.sha256(partner_id.encode("utf-8")).hexdigest()
    return base / "profiles" / browser / key


def browser_command(executable: Path, profile: Path, url: str) -> list[str]:
    return [str(executable), f"--user-data-dir={profile}", "--no-first-run", "--new-window", validate_url(url)]


def load_settings(base: Path) -> dict:
    path = base / "settings.json"
    if not path.exists():
        return {"partners": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("partners"), dict):
        raise ValueError("Не удалось прочитать настройки запуска АИС.")
    if any(not isinstance(entry, dict) for entry in data["partners"].values()):
        raise ValueError("Не удалось прочитать настройки партнёров.")
    for entry in data["partners"].values():
        # Repair only the exact incorrect default from the first version.
        # Preserve custom URLs and the existing browser profile directory.
        if entry.get("url") == LEGACY_DEFAULT_URL:
            entry["url"] = DEFAULT_URL
    return data


def save_settings(base: Path, settings: dict) -> None:
    base.mkdir(parents=True, exist_ok=True)
    # Write only these fields, never arbitrary fields supplied by a caller.
    clean = {
        "last_partner_id": settings.get("last_partner_id", ""),
        "partners": {
            key: {"browser": entry["browser"], "url": validate_url(entry["url"])}
            for key, entry in settings["partners"].items()
        },
    }
    temporary = base / "settings.tmp"
    temporary.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(base / "settings.json")


def create_shortcut(partner: dict, command: list[str], browser: str) -> str:
    """Create a native .lnk without putting user text into PowerShell code."""
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", partner["partner_name"]).strip(" .")[:70]
    key = hashlib.sha256(partner["id"].encode("utf-8")).hexdigest()[:12]
    environment = os.environ.copy()
    environment.update({
        "PV_AIS_LINK_NAME": f"АИС РЗН - {safe_name} - {browser} - {key}.lnk",
        "PV_AIS_LINK_TARGET": command[0],
        "PV_AIS_LINK_ARGUMENTS": subprocess.list2cmdline(command[1:]),
        "PV_AIS_LINK_DESCRIPTION": f"АИС РЗН: {partner['partner_name']} ({partner['partner_code']})",
    })
    script = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path $shortcutShell.SpecialFolders.Item('Desktop') $env:PV_AIS_LINK_NAME
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $env:PV_AIS_LINK_TARGET
$shortcut.Arguments = $env:PV_AIS_LINK_ARGUMENTS
$shortcut.Description = $env:PV_AIS_LINK_DESCRIPTION
$shortcut.IconLocation = $env:PV_AIS_LINK_TARGET + ',0'
$shortcut.Save()
Write-Output $shortcutPath
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=environment, capture_output=True, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=20, check=True,
    )
    return result.stdout.strip()


def run_gui(database: Path) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.withdraw()
    root.title("АИС РЗН — вход для партнёра")
    root.minsize(650, 440)
    try:
        base = local_data_dir()
        partners = load_partners(database)
        browsers = available_browsers()
        settings = load_settings(base)
        if not partners:
            raise ValueError("В базе PV1 пока нет партнёров. Сначала добавьте партнёра в PV1.")
        if not browsers:
            raise ValueError("Не найден Chrome, Microsoft Edge или Яндекс Браузер.")
    except (OSError, sqlite3.Error, ValueError) as exc:
        messagebox.showerror("Не удалось открыть запускатель АИС", str(exc), parent=root)
        root.destroy()
        return

    style = ttk.Style(root)
    style.theme_use("vista")
    style.configure("TLabel", font=("Segoe UI", 10))
    style.configure("TButton", font=("Segoe UI", 10), padding=7)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    ttk.Label(frame, text="АИС РЗН", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(frame, text="Выберите партнёра, чтобы открыть его профиль браузера.").grid(row=1, column=0, sticky="w", pady=(4, 18))
    ttk.Label(frame, text="Партнёр").grid(row=2, column=0, sticky="w")
    partner_choice = ttk.Combobox(frame, state="readonly", font=("Segoe UI", 11), values=[
        f"{partner['partner_name']}  ·  {partner['partner_code']}" for partner in partners
    ])
    partner_choice.grid(row=3, column=0, sticky="ew", pady=(4, 12))
    ttk.Label(frame, text="Браузер").grid(row=4, column=0, sticky="w")
    browser_keys = list(browsers)
    browser_choice = ttk.Combobox(frame, state="readonly", values=[BROWSER_NAMES[key] for key in browser_keys])
    browser_choice.grid(row=5, column=0, sticky="ew", pady=(4, 12))
    ttk.Label(frame, text="Адрес страницы входа в АИС").grid(row=6, column=0, sticky="w")
    url_value = tk.StringVar(root, DEFAULT_URL)
    ttk.Entry(frame, textvariable=url_value, font=("Segoe UI", 10)).grid(row=7, column=0, sticky="ew", pady=(4, 14))
    ttk.Label(
        frame, wraplength=600,
        text="При первом запуске введите логин и пароль нужного партнёра на сайте АИС "
        "и выберите «Сохранить пароль» в браузере. В дальнейшем открывайте АИС через этот запускатель "
        "или созданный ярлык. Пароль хранится в браузере. "
        "Адрес external.roszdravnadzor.ru открывается по HTTP: соединение не шифруется.",
    ).grid(row=8, column=0, sticky="w", pady=(0, 16))
    status_value = tk.StringVar(root)

    def choose_partner(_event=None):
        partner = partners[partner_choice.current()]
        entry = settings["partners"].get(partner["id"], {})
        browser = entry.get("browser", browser_keys[0])
        browser_choice.current(browser_keys.index(browser) if browser in browsers else 0)
        url_value.set(entry.get("url", DEFAULT_URL))
        status_value.set("При смене браузера пароль нужно сохранить в нём заново.")

    def perform(*, shortcut: bool = False):
        try:
            partner = partners[partner_choice.current()]
            browser = browser_keys[browser_choice.current()]
            url = validate_url(url_value.get())
            profile = profile_directory(base, partner["id"], browser)
            command = browser_command(browsers[browser], profile, url)
            profile.mkdir(parents=True, exist_ok=True)
            if shortcut:
                create_shortcut(partner, command, browser)
                message = f"Ярлык для «{partner['partner_name']}» создан на рабочем столе."
            else:
                subprocess.Popen(command, shell=False)
                message = f"Браузеру передан адрес АИС: {partner['partner_name']} · {BROWSER_NAMES[browser]}"
            settings["partners"][partner["id"]] = {"browser": browser, "url": url}
            settings["last_partner_id"] = partner["id"]
            save_settings(base, settings)
            status_value.set(message)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            messagebox.showerror("Не удалось выполнить действие", str(exc), parent=root)

    buttons = ttk.Frame(frame)
    buttons.grid(row=9, column=0, sticky="w")
    ttk.Button(buttons, text="Открыть АИС", command=perform).pack(side="left", padx=(0, 10))
    ttk.Button(buttons, text="Создать ярлык на рабочем столе", command=lambda: perform(shortcut=True)).pack(side="left")
    ttk.Label(frame, textvariable=status_value, wraplength=600, foreground="#475569").grid(row=10, column=0, sticky="w", pady=(16, 0))
    partner_choice.bind("<<ComboboxSelected>>", choose_partner)
    selected = next((index for index, partner in enumerate(partners) if partner["id"] == settings.get("last_partner_id")), 0)
    partner_choice.current(selected)
    choose_partner()
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="АИС РЗН: отдельный профиль браузера для каждого партнёра PV1.")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "pv_system.db")
    arguments = parser.parse_args()
    run_gui(arguments.database)
