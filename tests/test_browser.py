"""Тесты браузерного пути Ozon на настоящем Chromium против локальной страницы.

Сам ozon.ru недоступен, но проверить можно главное: что Playwright-путь
доходит до composer-api из контекста страницы и что DOM-фолбэк действительно
снимает товары с вёрстки.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mp_parser import ozon
from mp_parser.config import settings

playwright_api = pytest.importorskip("playwright.async_api", reason="нужен playwright")

FIXTURES = Path(__file__).parent / "fixtures"

# Вёрстка похожа на выдачу Ozon: плитка = ссылка на /product/ + цены и метки рядом.
SEARCH_PAGE = """
<meta charset="utf-8">
<div data-widget="searchResultsV2">
  <div data-widget="tile">
    <a href="/product/kofe-v-zernah-arabika-1-kg-1234567/?asb=1">Кофе в зернах Арабика 1 кг</a>
    <div><span>1 129 ₽</span> <span>1 890 ₽</span></div>
    <div>4.7 • 1 204 отзыва</div>
  </div>
  <div data-widget="tile">
    <a href="/product/kofe-molotyy-250-g-7654321/">Кофе молотый 250 г</a>
    <div><span>450 ₽</span></div>
    <div>4.2 • 38 отзывов</div>
  </div>
  <div data-widget="tile">
    <a href="/category/kofe-9373/">Не товар, а категория</a>
  </div>
</div>
"""


def chromium_path() -> str:
    """Ищет установленный Chromium: пакет playwright может не совпасть по сборке."""
    if settings.ozon_browser_path:
        return settings.ozon_browser_path
    roots = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"), reverse=True)
    if not roots:
        pytest.skip("Chromium не найден: playwright install chromium")
    return str(roots[0])


async def _with_page(url: str, coro):
    """Открывает страницу в Chromium и выполняет над ней переданную корутину."""
    async with playwright_api.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
        try:
            page = await (await browser.new_context(locale="ru-RU")).new_page()
            await page.goto(url, wait_until="domcontentloaded")
            return await coro(page)
        finally:
            await browser.close()


@pytest.fixture(autouse=True)
def _quiet_settings(monkeypatch):
    monkeypatch.setattr(settings, "request_timeout", 20.0)
    monkeypatch.setattr(settings, "retries", 1)
    # ozon._fetch_via_browser берёт путь к браузеру из настроек
    monkeypatch.setattr(settings, "ozon_browser_path", chromium_path())


def test_dom_fallback_extracts_products(stub_server):
    stub_server.html_route("/search/", SEARCH_PAGE)

    payload = asyncio.run(_with_page(stub_server.url("/search/"), ozon._scrape_dom))
    products = ozon.parse_products(payload)

    by_id = {p.id: p for p in products}
    assert set(by_id) == {"1234567", "7654321"}  # категория отброшена

    first = by_id["1234567"]
    assert first.name == "Кофе в зернах Арабика 1 кг"
    assert first.price == pytest.approx(1129.0)
    assert first.price_base == pytest.approx(1890.0)
    assert first.discount == 40
    assert first.rating == pytest.approx(4.7)
    assert first.reviews == 1204

    second = by_id["7654321"]
    assert second.price == pytest.approx(450.0)
    assert second.price_base is None
    assert second.reviews == 38


def test_browser_path_reads_composer_api(stub_server, monkeypatch):
    """Основной браузерный путь: страница открылась, composer-api ответил JSON."""
    stub_server.html_route("/search/", SEARCH_PAGE)
    stub_server.json_route("/composer", json.loads((FIXTURES / "ozon_search.json").read_text("utf-8")))

    monkeypatch.setattr(ozon, "API_URL", stub_server.url("/composer"))
    monkeypatch.setattr(ozon, "SITE_SEARCH_URL", stub_server.url("/search/?text={query}"))

    products = asyncio.run(ozon._fetch_via_browser("кофе", 1, "popular"))
    parsed = ozon.parse_products(products)

    assert [p.id for p in parsed] == ["1234567", "7654321"]
    assert stub_server.hits("/composer"), "composer-api не был вызван из страницы"
    assert stub_server.hits("/composer")[0]["headers"]["x-o3-app-name"] == "dweb_client"


def test_browser_path_falls_back_to_dom_on_403(stub_server, monkeypatch):
    """composer-api отвечает 403 — товары всё равно снимаются с вёрстки."""
    stub_server.html_route("/search/", SEARCH_PAGE)
    stub_server.raw_route("/composer", lambda _q: (403, "text/plain", "denied"))

    monkeypatch.setattr(ozon, "API_URL", stub_server.url("/composer"))
    monkeypatch.setattr(ozon, "SITE_SEARCH_URL", stub_server.url("/search/?text={query}"))

    payload = asyncio.run(ozon._fetch_via_browser("кофе", 1, "popular"))
    parsed = ozon.parse_products(payload)

    assert {p.id for p in parsed} == {"1234567", "7654321"}


def test_full_search_uses_browser_when_http_blocked(stub_server, monkeypatch):
    """Сквозной путь ozon.search: httpx получил 403, браузер спас выдачу."""
    stub_server.html_route("/search/", SEARCH_PAGE)
    stub_server.raw_route("/composer", lambda _q: (403, "text/plain", "denied"))

    monkeypatch.setattr(ozon, "API_URL", stub_server.url("/composer"))
    monkeypatch.setattr(ozon, "SITE_SEARCH_URL", stub_server.url("/search/?text={query}"))
    monkeypatch.setattr(settings, "ozon_use_browser", True)

    async def run():
        try:
            return await ozon.search("кофе", limit=2)
        finally:
            from mp_parser.http import close_client

            await close_client()

    products = asyncio.run(run())
    assert len(products) == 2
    assert all(p.source == "ozon" for p in products)
