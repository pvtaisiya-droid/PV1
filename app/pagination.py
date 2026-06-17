from dataclasses import dataclass
from math import ceil
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Request

from app.config import get_settings


@dataclass(frozen=True)
class Pagination:
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, ceil(self.total / self.per_page)) if self.per_page else 1

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def start_index(self) -> int:
        if self.total == 0:
            return 0
        return self.offset + 1

    @property
    def end_index(self) -> int:
        return min(self.total, self.offset + self.per_page)


def normalize_page(page: int | None) -> int:
    return max(1, page or 1)


def normalize_per_page(per_page: int | None) -> int:
    settings = get_settings()
    requested = per_page or settings.default_page_size
    return min(settings.max_page_size, max(1, requested))


def paginate_items(items: list, page: int | None, per_page: int | None) -> tuple[list, Pagination]:
    pagination = Pagination(
        page=normalize_page(page),
        per_page=normalize_per_page(per_page),
        total=len(items),
    )
    return items[pagination.offset : pagination.offset + pagination.per_page], pagination


def page_url(request: Request, page: int) -> str:
    parts = urlsplit(str(request.url))
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    params["page"] = str(max(1, page))
    return urlunsplit(("", "", parts.path, urlencode(params), ""))
