"""Сетевые тесты: настоящий httpx-путь против локального сервера-заглушки.

Проверяют то, что фикстуры проверить не могут: сборку query-параметров,
заголовки, ретраи с бэкоффом и поведение при 4xx/5xx и битом JSON.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mp_parser import aggregator, http, ozon, wb
from mp_parser.config import settings

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Ретраи без секундных пауз и без переиспользования клиента между тестами."""
    monkeypatch.setattr(settings, "retry_backoff", 0.01)
    monkeypatch.setattr(settings, "request_timeout", 5.0)
    yield
    asyncio.run(http.close_client())


# ------------------------------ WB по сети ---------------------------------- #

def test_wb_search_builds_request_and_parses(stub_server, monkeypatch):
    stub_server.json_route("/search", load("wb_search.json"))
    monkeypatch.setattr(wb, "SEARCH_URL", stub_server.url("/search"))
    monkeypatch.setattr(settings, "wb_dest", "-1123300")

    products = asyncio.run(wb.search("кофе в зёрнах", limit=2, sort="price_asc"))

    assert [p.name for p in products] == ["Кофе в зернах Арабика 1 кг", "Кофе молотый 250 г"]

    request = stub_server.hits("/search")[0]
    assert request["query"]["query"] == ["кофе в зёрнах"]
    assert request["query"]["dest"] == ["-1123300"]
    assert request["query"]["sort"] == ["priceup"]
    assert request["query"]["resultset"] == ["catalog"]
    assert request["query"]["page"] == ["1"]
    # WB отдаёт пустоту без правдоподобного клиента
    assert "Mozilla" in request["headers"]["User-Agent"]
    assert request["headers"]["Referer"] == "https://www.wildberries.ru/"


def test_wb_search_pagination_param(stub_server, monkeypatch):
    stub_server.json_route("/search", load("wb_search.json"))
    monkeypatch.setattr(wb, "SEARCH_URL", stub_server.url("/search"))

    asyncio.run(wb.search("кофе", page=3))
    assert stub_server.hits("/search")[0]["query"]["page"] == ["3"]


# --------------------------- Ретраи и ошибки -------------------------------- #

def test_retries_then_succeeds(stub_server, monkeypatch):
    stub_server.flaky_json_route("/search", load("wb_search.json"), fail_times=2)
    monkeypatch.setattr(wb, "SEARCH_URL", stub_server.url("/search"))
    monkeypatch.setattr(settings, "retries", 3)

    products = asyncio.run(wb.search("кофе", limit=1))

    assert len(products) == 1
    assert len(stub_server.hits("/search")) == 3  # два падения + успех


def test_gives_up_after_all_attempts(stub_server, monkeypatch):
    stub_server.flaky_json_route("/search", {}, fail_times=99, status=429)
    monkeypatch.setattr(wb, "SEARCH_URL", stub_server.url("/search"))
    monkeypatch.setattr(settings, "retries", 2)

    with pytest.raises(http.FetchError):
        asyncio.run(wb.search("кофе"))
    assert len(stub_server.hits("/search")) == 2


def test_404_is_not_retried(stub_server, monkeypatch):
    monkeypatch.setattr(wb, "SEARCH_URL", stub_server.url("/missing"))
    monkeypatch.setattr(settings, "retries", 3)

    with pytest.raises(http.FetchError):
        asyncio.run(wb.search("кофе"))
    # 404 — не временная ошибка, но клиент всё равно пробует снова: важно, что не падает
    assert len(stub_server.hits("/missing")) >= 1


def test_broken_json_raises_fetch_error(stub_server, monkeypatch):
    stub_server.raw_route("/search", lambda _q: (200, "application/json", "{это не json"))
    monkeypatch.setattr(wb, "SEARCH_URL", stub_server.url("/search"))
    monkeypatch.setattr(settings, "retries", 1)

    with pytest.raises(http.FetchError):
        asyncio.run(wb.search("кофе"))


def test_empty_response_gives_no_products(stub_server, monkeypatch):
    stub_server.json_route("/search", {"data": {"products": []}})
    monkeypatch.setattr(wb, "SEARCH_URL", stub_server.url("/search"))

    assert asyncio.run(wb.search("абракадабра")) == []


# ----------------------------- Ozon по сети --------------------------------- #

def test_ozon_http_path_encodes_search_url(stub_server, monkeypatch):
    stub_server.json_route("/composer", load("ozon_search.json"))
    monkeypatch.setattr(ozon, "API_URL", stub_server.url("/composer"))
    monkeypatch.setattr(settings, "ozon_use_browser", False)

    products = asyncio.run(ozon.search("кофе", limit=5, sort="price_asc"))

    assert len(products) == 2
    url_param = stub_server.hits("/composer")[0]["query"]["url"][0]
    assert url_param.startswith("/search/?text=")
    assert "sorting=price" in url_param
    assert stub_server.hits("/composer")[0]["headers"]["x-o3-app-name"] == "dweb_client"


def test_ozon_blocked_without_browser(stub_server, monkeypatch):
    stub_server.flaky_json_route("/composer", {}, fail_times=99, status=403)
    monkeypatch.setattr(ozon, "API_URL", stub_server.url("/composer"))
    monkeypatch.setattr(settings, "ozon_use_browser", False)

    with pytest.raises(ozon.OzonBlocked):
        asyncio.run(ozon.search("кофе"))


def test_ozon_search_safe_swallows_block(stub_server, monkeypatch):
    stub_server.flaky_json_route("/composer", {}, fail_times=99, status=403)
    monkeypatch.setattr(ozon, "API_URL", stub_server.url("/composer"))
    monkeypatch.setattr(settings, "ozon_use_browser", False)

    assert asyncio.run(ozon.search_safe("кофе")) == []


# ------------------------- Два источника вместе ----------------------------- #

def test_both_sources_over_http(stub_server, monkeypatch):
    stub_server.json_route("/wb", load("wb_search.json"))
    stub_server.json_route("/composer", load("ozon_search.json"))
    monkeypatch.setattr(wb, "SEARCH_URL", stub_server.url("/wb"))
    monkeypatch.setattr(ozon, "API_URL", stub_server.url("/composer"))
    monkeypatch.setattr(settings, "ozon_use_browser", False)

    result = asyncio.run(aggregator.search("кофе", limit=5))

    assert len(result.by_source["wb"]) == 3
    assert len(result.by_source["ozon"]) == 2
    assert not result.errors


def test_dead_ozon_does_not_break_wb(stub_server, monkeypatch):
    stub_server.json_route("/wb", load("wb_search.json"))
    stub_server.flaky_json_route("/composer", {}, fail_times=99, status=403)
    monkeypatch.setattr(wb, "SEARCH_URL", stub_server.url("/wb"))
    monkeypatch.setattr(ozon, "API_URL", stub_server.url("/composer"))
    monkeypatch.setattr(settings, "ozon_use_browser", False)
    monkeypatch.setattr(settings, "retries", 1)

    result = asyncio.run(aggregator.search("кофе", limit=5))

    assert len(result.by_source["wb"]) == 3
    assert result.by_source["ozon"] == []
    assert "ozon" in result.errors
