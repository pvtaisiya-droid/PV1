import os
from functools import lru_cache
from pathlib import Path
from typing import Literal


TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in TRUTHY_ENV_VALUES


def env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        return max(minimum, value)
    return value


class Settings:
    app_mode: Literal["dev", "prod"]
    user_switch_enabled: bool
    allow_query_user_switch: bool
    max_upload_bytes: int
    default_page_size: int
    max_page_size: int
    auth_session_hours: int
    auth_cookie_secure: bool
    business_timezone: str

    def __init__(self) -> None:
        self.storage_root = Path(os.getenv("PV_STORAGE_ROOT", "uploads")).resolve()
        raw_mode = os.getenv("PV_APP_MODE", "prod").strip().lower()
        self.app_mode = "dev" if raw_mode in {"dev", "development", "local"} else "prod"
        self.user_switch_enabled = env_bool(
            "PV_USER_SWITCH_ENABLED",
            default=False,
        )
        self.allow_query_user_switch = env_bool("PV_ALLOW_QUERY_USER_SWITCH", default=False)
        self.max_upload_bytes = env_int(
            "PV_MAX_UPLOAD_BYTES",
            25 * 1024 * 1024,
            minimum=1024 * 1024,
        )
        self.default_page_size = env_int("PV_DEFAULT_PAGE_SIZE", 25, minimum=1)
        self.max_page_size = env_int("PV_MAX_PAGE_SIZE", 100, minimum=1)
        self.auth_session_hours = env_int("PV_AUTH_SESSION_HOURS", 12, minimum=1)
        self.auth_cookie_secure = env_bool("PV_AUTH_COOKIE_SECURE", default=False)
        self.business_timezone = (
            os.getenv("PV_BUSINESS_TIMEZONE", "Europe/Moscow").strip()
            or "Europe/Moscow"
        )

    @property
    def is_dev(self) -> bool:
        return self.app_mode == "dev"

    @property
    def is_prod(self) -> bool:
        return self.app_mode == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
