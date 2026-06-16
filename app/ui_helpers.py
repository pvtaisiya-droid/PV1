from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi.responses import RedirectResponse
from starlette import status


def clean_param(value: str | None) -> str:
    return (value or "").strip()


def redirect_with_message(
    path: str,
    *,
    message: str | None = None,
    error: str | None = None,
    validation: str | None = None,
) -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    if validation:
        params["validation"] = validation
    if not params:
        return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)
    parts = urlsplit(path)
    query_params = dict(parse_qsl(parts.query, keep_blank_values=True))
    query_params.update(params)
    target = urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query_params),
            parts.fragment,
        )
    )
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


def contains_search(search: str | None, *values: object) -> bool:
    needle = clean_param(search).lower()
    if not needle:
        return True
    haystack = " ".join(str(value or "") for value in values).lower()
    return needle in haystack


def in_date_range(
    value: date | datetime | None,
    date_from: date | None,
    date_to: date | None,
) -> bool:
    if value is None:
        return not date_from and not date_to
    if isinstance(value, datetime):
        value = value.date()
    if date_from and value < date_from:
        return False
    if date_to and value > date_to:
        return False
    return True


def active_filters(**filters: object) -> bool:
    return any(value not in (None, "", []) for value in filters.values())


def unique_values(values: list[str | None]) -> list[str]:
    return sorted({value for value in values if value})
