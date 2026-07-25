"""Парсер поиска Ozon.

Ozon не отдаёт публичного поискового API: страница собирается из виджетов
через composer-api. Прямой запрос к нему часто ловит 403 от анти-бота,
поэтому есть два пути:

1. httpx — быстро, работает не всегда;
2. Playwright (Chromium) — открываем ozon.ru, получаем cookies анти-бота и
   дёргаем composer-api уже из контекста страницы; если и это не сработало,
   снимаем товары прямо с отрендеренной вёрстки.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Iterator
from urllib.parse import quote

from .config import settings
from .http import FetchError, fetch_json
from .models import Product, to_float, to_int

log = logging.getLogger(__name__)

API_URL = "https://api.ozon.ru/composer-api.bx/page/json/v2"
SEARCH_PATH = "/search/?text={query}&from_global=true"
SITE_SEARCH_URL = "https://www.ozon.ru/search/?text={query}"
BASE = "https://www.ozon.ru"

SORTS = {
    "popular": "",
    "price_asc": "&sorting=price",
    "price_desc": "&sorting=price_desc",
    "rating": "&sorting=rating",
    "new": "&sorting=new",
}

_PRODUCT_ID_RE = re.compile(r"/product/(?:[^/?#]*-)?(\d+)")


class OzonBlocked(RuntimeError):
    """Ozon ответил анти-ботом, данных нет."""


# --------------------------------------------------------------------------- #
# Разбор ответа composer-api
# --------------------------------------------------------------------------- #

def _iter_states(payload: Any) -> Iterator[Any]:
    """Отдаёт распарсенные значения widgetStates (они приходят строками JSON)."""
    if not isinstance(payload, dict):
        return
    states = payload.get("widgetStates")
    if not isinstance(states, dict):
        return
    for key, value in states.items():
        if not isinstance(value, str):
            if value is not None:
                yield value
            continue
        try:
            yield json.loads(value)
        except (json.JSONDecodeError, TypeError):
            log.debug("не смог разобрать widgetState %s", key)


def _iter_items(payload: Any) -> Iterator[dict[str, Any]]:
    """Плитки товаров из всех виджетов результатов поиска."""
    for state in _iter_states(payload):
        if not isinstance(state, dict):
            continue
        items = state.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                yield item


def _product_link(item: dict[str, Any]) -> str | None:
    for candidate in (
        item.get("link"),
        (item.get("action") or {}).get("link") if isinstance(item.get("action"), dict) else None,
        (item.get("mainState") or [{}])[0].get("link") if isinstance(item.get("mainState"), list) else None,
    ):
        if isinstance(candidate, str) and "/product/" in candidate:
            return candidate if candidate.startswith("http") else BASE + candidate
    return None


def _iter_atoms(item: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for key in ("mainState", "state", "leftBottomBadge"):
        block = item.get(key)
        if isinstance(block, list):
            for atom in block:
                if isinstance(atom, dict):
                    yield atom


def _prices_from_atoms(item: dict[str, Any]) -> tuple[float | None, float | None]:
    """Достаёт (цена, цена без скидки) из атома priceV2."""
    for atom in _iter_atoms(item):
        price_block = atom.get("priceV2")
        if not isinstance(price_block, dict):
            continue
        final: float | None = None
        original: float | None = None
        for entry in price_block.get("price") or []:
            if not isinstance(entry, dict):
                continue
            value = to_float(entry.get("text"))
            if value is None:
                continue
            style = (entry.get("textStyle") or "").upper()
            if "ORIGINAL" in style or "STRIKE" in style:
                original = value if original is None else min(original, value)
            elif final is None:
                final = value
        if final is not None or original is not None:
            return final, original
    return None, None


def _rating_and_reviews(item: dict[str, Any]) -> tuple[float | None, int | None]:
    """Рейтинг и число отзывов из labelList вида «4.8» / «120 отзывов»."""
    rating: float | None = None
    reviews: int | None = None
    for atom in _iter_atoms(item):
        label_list = atom.get("labelList")
        if not isinstance(label_list, dict):
            continue
        for label in label_list.get("items") or []:
            if not isinstance(label, dict):
                continue
            title = (label.get("title") or "").strip()
            if not title:
                continue
            low = title.lower()
            if "отзыв" in low:
                reviews = reviews if reviews is not None else to_int(title)
            elif rating is None:
                value = to_float(title)
                if value is not None and 0 < value <= 5:
                    rating = value
    return rating, reviews


def _title_from_atoms(item: dict[str, Any]) -> str | None:
    for atom in _iter_atoms(item):
        for key in ("textAtom", "text"):
            block = atom.get(key)
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and len(text.strip()) > 3:
                    return re.sub(r"<[^>]+>", "", text).strip()
    return None


def _image_from_item(item: dict[str, Any]) -> str | None:
    tile = item.get("tileImage")
    if isinstance(tile, dict):
        for image in tile.get("items") or []:
            if isinstance(image, dict) and isinstance(image.get("image"), dict):
                link = image["image"].get("link")
                if isinstance(link, str) and link.startswith("http"):
                    return link
    return None


def parse_products(payload: Any, *, limit: int | None = None) -> list[Product]:
    """Превращает ответ composer-api в список товаров."""
    products: list[Product] = []
    seen: set[str] = set()

    for item in _iter_items(payload):
        tracking = item.get("cellTrackingInfo")
        tracking = tracking if isinstance(tracking, dict) else {}
        link = _product_link(item)

        product_id = str(tracking.get("id") or "").strip()
        if not product_id and link:
            match = _PRODUCT_ID_RE.search(link)
            if match:
                product_id = match.group(1)
        if not product_id or product_id in seen:
            continue

        name = (tracking.get("title") or "").strip() or _title_from_atoms(item)
        if not name:
            continue

        price = to_float(tracking.get("finalPrice"))
        price_base = to_float(tracking.get("price"))
        if price is None and price_base is None:
            price, price_base = _prices_from_atoms(item)
        if price is not None and price_base is not None and price_base < price:
            price, price_base = price_base, price

        rating, reviews = _rating_and_reviews(item)
        seller = tracking.get("seller")
        seller_name = seller.get("name") if isinstance(seller, dict) else seller

        seen.add(product_id)
        products.append(
            Product(
                source="ozon",
                id=product_id,
                name=name,
                url=link or f"{BASE}/product/{product_id}/",
                price=price,
                price_base=price_base,
                brand=tracking.get("brand") or None,
                seller=seller_name or None,
                rating=rating if rating is not None else to_float(tracking.get("rating")),
                reviews=reviews,
                image=_image_from_item(item),
                extra={"availability": to_int(tracking.get("availability"))},
            )
        )
        if limit and len(products) >= limit:
            break
    return products


# --------------------------------------------------------------------------- #
# Получение данных
# --------------------------------------------------------------------------- #

def _api_params(query: str, page: int, sort: str) -> dict[str, str]:
    path = SEARCH_PATH.format(query=quote(query)) + SORTS.get(sort, "")
    if page > 1:
        path += f"&page={page}"
    return {"url": path}


async def _fetch_via_http(query: str, page: int, sort: str) -> Any:
    return await fetch_json(
        API_URL,
        params=_api_params(query, page, sort),
        headers={
            "Origin": BASE,
            "Referer": SITE_SEARCH_URL.format(query=quote(query)),
            "x-o3-app-name": "dweb_client",
        },
        attempts=1,
    )


async def _fetch_via_browser(query: str, page: int, sort: str) -> Any:
    """Открывает Ozon в Chromium и дёргает composer-api из контекста страницы."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise OzonBlocked(
            "нужен Playwright: pip install playwright && playwright install chromium"
        ) from exc

    params = _api_params(query, page, sort)
    api_url = f"{API_URL}?url={quote(params['url'], safe='')}"

    launch_kwargs: dict[str, Any] = {"headless": True}
    if settings.ozon_browser_path:
        launch_kwargs["executable_path"] = settings.ozon_browser_path

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**launch_kwargs)
        try:
            context = await browser.new_context(locale="ru-RU")
            page_obj = await context.new_page()
            await page_obj.goto(
                SITE_SEARCH_URL.format(query=quote(query)),
                wait_until="domcontentloaded",
                timeout=int(settings.request_timeout * 1000),
            )
            payload = await page_obj.evaluate(
                """async (url) => {
                    const r = await fetch(url, {
                        credentials: 'include',
                        headers: {'x-o3-app-name': 'dweb_client'},
                    });
                    if (!r.ok) return null;
                    try { return await r.json(); } catch (e) { return null; }
                }""",
                api_url,
            )
            if payload:
                return payload

            # composer-api не отдался — снимаем плитки с вёрстки.
            log.info("Ozon: composer-api недоступен, читаю DOM")
            return await _scrape_dom(page_obj)
        finally:
            await browser.close()


async def _scrape_dom(page_obj: Any) -> dict[str, Any]:
    """Резервный сбор товаров из отрендеренной страницы поиска."""
    await page_obj.wait_for_selector('a[href*="/product/"]', timeout=15_000)
    raw = await page_obj.evaluate(
        """() => {
            const seen = new Set();
            const out = [];
            for (const link of document.querySelectorAll('a[href*="/product/"]')) {
                const card = link.closest('div[data-widget], article') || link.parentElement;
                if (!card) continue;
                const href = link.getAttribute('href');
                const title = (link.innerText || '').trim().split('\\n')[0];
                if (!href || title.length < 4 || seen.has(href)) continue;
                seen.add(href);
                out.push({href, title, text: (card.innerText || '').slice(0, 400)});
            }
            return out;
        }"""
    )
    items = []
    for entry in raw:
        match = _PRODUCT_ID_RE.search(entry["href"])
        if not match:
            continue
        prices = [to_float(p) for p in re.findall(r"\d[\d\s]*(?=\s*₽)", entry["text"])]
        prices = sorted(p for p in prices if p)
        rating_match = re.search(r"\b([0-5][.,]\d)\b", entry["text"])
        reviews_match = re.search(r"(\d[\d\s]*)\s*отзыв", entry["text"])
        items.append(
            {
                "cellTrackingInfo": {
                    "id": match.group(1),
                    "title": entry["title"],
                    "finalPrice": prices[0] if prices else None,
                    "price": prices[-1] if len(prices) > 1 else None,
                    "rating": rating_match.group(1).replace(",", ".") if rating_match else None,
                },
                "link": entry["href"],
                "mainState": [
                    {
                        "labelList": {
                            "items": [{"title": f"{reviews_match.group(1)} отзывов"}]
                            if reviews_match
                            else []
                        }
                    }
                ],
            }
        )
    return {"widgetStates": {"searchResultsV2-dom": json.dumps({"items": items})}}


async def search(
    query: str,
    *,
    limit: int | None = None,
    page: int = 1,
    sort: str = "popular",
) -> list[Product]:
    """Поиск товаров на Ozon по текстовому запросу."""
    limit = limit or settings.default_limit
    payload: Any = None

    try:
        payload = await _fetch_via_http(query, page, sort)
    except FetchError as exc:
        log.info("Ozon: прямой запрос не прошёл (%s)", exc)

    products = parse_products(payload, limit=limit) if payload else []

    if not products and settings.ozon_use_browser:
        payload = await _fetch_via_browser(query, page, sort)
        products = parse_products(payload, limit=limit)

    if not products:
        raise OzonBlocked("Ozon не отдал результаты поиска")

    log.info("Ozon: запрос %r -> %s товаров", query, len(products))
    return products


async def search_safe(query: str, **kwargs: Any) -> list[Product]:
    """Как search, но при блокировке/ошибке возвращает пустой список."""
    try:
        return await search(query, **kwargs)
    except (OzonBlocked, FetchError, asyncio.TimeoutError) as exc:
        log.warning("Ozon: %s", exc)
        return []
