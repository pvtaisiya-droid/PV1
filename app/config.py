import os
from functools import lru_cache
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
    app_mode: Literal["demo", "prod"]
    demo_user_switch_enabled: bool
    allow_query_user_switch: bool
    max_upload_bytes: int
    default_page_size: int
    max_page_size: int

    def __init__(self) -> None:
        raw_mode = os.getenv("PV_APP_MODE", "demo").strip().lower()
        self.app_mode = "prod" if raw_mode == "prod" else "demo"
        self.demo_user_switch_enabled = env_bool(
            "PV_DEMO_USER_SWITCH",
            default=self.app_mode == "demo",
        )
        self.allow_query_user_switch = env_bool("PV_ALLOW_QUERY_USER_SWITCH", default=False)
        self.max_upload_bytes = env_int(
            "PV_MAX_UPLOAD_BYTES",
            25 * 1024 * 1024,
            minimum=1024 * 1024,
        )
        self.default_page_size = env_int("PV_DEFAULT_PAGE_SIZE", 25, minimum=1)
        self.max_page_size = env_int("PV_MAX_PAGE_SIZE", 100, minimum=1)

    @property
    def is_demo(self) -> bool:
        return self.app_mode == "demo"

    @property
    def is_prod(self) -> bool:
        return self.app_mode == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
