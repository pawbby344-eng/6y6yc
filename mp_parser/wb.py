"""Парсер поиска Wildberries через публичный search-API."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from .config import settings
from .http import fetch_json
from .models import Product, to_float, to_int

log = logging.getLogger(__name__)

SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v13/search"
CARD_URL = "https://www.wildberries.ru/catalog/{id}/detail.aspx"

# Границы vol -> номер basket-хоста для картинок.
_BASKET_BOUNDS: tuple[tuple[int, str], ...] = (
    (143, "01"), (287, "02"), (431, "03"), (719, "04"), (1007, "05"),
    (1061, "06"), (1115, "07"), (1169, "08"), (1313, "09"), (1601, "10"),
    (1655, "11"), (1919, "12"), (2045, "13"), (2189, "14"), (2405, "15"),
    (2621, "16"), (2837, "17"), (3053, "18"), (3269, "19"), (3485, "20"),
    (3701, "21"), (3917, "22"), (4133, "23"), (4349, "24"), (4565, "25"),
)

SORTS = {
    "popular": "popular",
    "price_asc": "priceup",
    "price_desc": "pricedown",
    "rating": "rate",
    "new": "newly",
}


def image_url(nm_id: int) -> str:
    """Ссылка на главное фото товара по его артикулу."""
    vol = nm_id // 100_000
    part = nm_id // 1_000
    host = "26"
    for bound, candidate in _BASKET_BOUNDS:
        if vol <= bound:
            host = candidate
            break
    return f"https://basket-{host}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/1.webp"


def _iter_raw_products(payload: Any) -> Iterable[dict[str, Any]]:
    """WB отдаёт товары то в data.products, то в products — поддерживаем оба."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("products"), list):
        return data["products"]
    if isinstance(payload.get("products"), list):
        return payload["products"]
    return []


def _prices(raw: dict[str, Any]) -> tuple[float | None, float | None]:
    """Возвращает (итоговая цена, цена без скидки) в рублях.

    Актуальные ответы держат цены в sizes[].price (в копейках), старые —
    в salePriceU/priceU на верхнем уровне.
    """
    best: tuple[float, float | None] | None = None
    for size in raw.get("sizes") or []:
        price = size.get("price")
        if not isinstance(price, dict):
            continue
        total = to_float(price.get("product")) or to_float(price.get("total"))
        if total is None:
            continue
        base = to_float(price.get("basic"))
        if best is None or total < best[0]:
            best = (total, base)
    if best is not None:
        total, base = best
        return total / 100, (base / 100 if base else None)

    total = to_float(raw.get("salePriceU"))
    base = to_float(raw.get("priceU"))
    return (total / 100 if total else None), (base / 100 if base else None)


def parse_products(payload: Any, *, limit: int | None = None) -> list[Product]:
    """Превращает ответ search-API в список товаров."""
    products: list[Product] = []
    for raw in _iter_raw_products(payload):
        if not isinstance(raw, dict):
            continue
        nm_id = to_int(raw.get("id"))
        if nm_id is None:
            continue
        price, price_base = _prices(raw)
        products.append(
            Product(
                source="wb",
                id=str(nm_id),
                name=(raw.get("name") or "").strip() or f"Товар {nm_id}",
                url=CARD_URL.format(id=nm_id),
                price=price,
                price_base=price_base,
                brand=(raw.get("brand") or None),
                seller=(raw.get("supplier") or None),
                rating=to_float(raw.get("reviewRating") or raw.get("rating")),
                reviews=to_int(raw.get("feedbacks")),
                image=image_url(nm_id),
                extra={"total_quantity": to_int(raw.get("totalQuantity"))},
            )
        )
        if limit and len(products) >= limit:
            break
    return products


async def search(
    query: str,
    *,
    limit: int | None = None,
    page: int = 1,
    sort: str = "popular",
) -> list[Product]:
    """Поиск товаров на Wildberries по текстовому запросу."""
    limit = limit or settings.default_limit
    params = {
        "ab_testing": "false",
        "appType": "1",
        "curr": "rub",
        "dest": settings.wb_dest,
        "lang": "ru",
        "page": str(max(1, page)),
        "query": query,
        "resultset": "catalog",
        "sort": SORTS.get(sort, "popular"),
        "spp": "30",
        "suppressSpellcheck": "false",
    }
    payload = await fetch_json(
        SEARCH_URL,
        params=params,
        headers={"Origin": "https://www.wildberries.ru", "Referer": "https://www.wildberries.ru/"},
    )
    products = parse_products(payload, limit=limit)
    log.info("WB: запрос %r -> %s товаров", query, len(products))
    return products
