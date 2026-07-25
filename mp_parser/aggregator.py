"""Единая точка входа: ищем сразу в нескольких маркетплейсах."""

from __future__ import annotations

import asyncio
import logging

from . import ozon, wb
from .config import settings
from .http import FetchError
from .models import Product

log = logging.getLogger(__name__)

SOURCES = ("wb", "ozon")
SORT_KEYS = ("popular", "price_asc", "price_desc", "rating", "new")


class SearchResult:
    """Результаты поиска с разбивкой по источникам и списком ошибок."""

    def __init__(self, query: str, sort: str = "popular") -> None:
        self.query = query
        self.sort = sort
        self.by_source: dict[str, list[Product]] = {}
        self.errors: dict[str, str] = {}

    @property
    def products(self) -> list[Product]:
        """Все товары одним списком, отсортированные согласно self.sort."""
        items = [p for source in SOURCES for p in self.by_source.get(source, [])]
        if self.sort == "price_asc":
            items.sort(key=lambda p: (p.price is None, p.price or 0))
        elif self.sort == "price_desc":
            items.sort(key=lambda p: (p.price is None, -(p.price or 0)))
        elif self.sort == "rating":
            items.sort(key=lambda p: (p.rating is None, -(p.rating or 0)))
        return items

    @property
    def is_empty(self) -> bool:
        return not any(self.by_source.values())


async def search(
    query: str,
    *,
    sources: tuple[str, ...] = SOURCES,
    limit: int | None = None,
    page: int = 1,
    sort: str = "popular",
) -> SearchResult:
    """Параллельный поиск по выбранным маркетплейсам.

    Падение одного источника не ломает остальные — ошибка попадает
    в result.errors.
    """
    limit = min(limit or settings.default_limit, settings.max_limit)
    result = SearchResult(query, sort)

    finders = {"wb": wb.search, "ozon": ozon.search}
    targets = [s for s in sources if s in finders]

    async def run(source: str) -> None:
        try:
            result.by_source[source] = await finders[source](
                query, limit=limit, page=page, sort=sort
            )
        except (FetchError, ozon.OzonBlocked, asyncio.TimeoutError) as exc:
            log.warning("%s: поиск не удался: %s", source, exc)
            result.by_source[source] = []
            result.errors[source] = str(exc)
        except Exception as exc:  # неожиданная ошибка не должна ронять весь поиск
            log.exception("%s: неожиданная ошибка", source)
            result.by_source[source] = []
            result.errors[source] = f"{type(exc).__name__}: {exc}"

    await asyncio.gather(*(run(s) for s in targets))
    return result
