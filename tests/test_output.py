"""Тесты сборки сообщения для Telegram, сортировки и выгрузок."""

from __future__ import annotations

import asyncio
import json

import pytest

from mp_parser import aggregator as search_mod, export, formatting
from mp_parser.cache import TTLCache
from mp_parser.models import Product
from mp_parser.aggregator import SearchResult


def product(**kwargs) -> Product:
    base = dict(
        source="wb", id="1", name="Кофе", url="https://www.wildberries.ru/catalog/1/detail.aspx"
    )
    base.update(kwargs)
    return Product(**base)


def make_result() -> SearchResult:
    result = SearchResult("кофе")
    result.by_source["wb"] = [
        product(id="1", price=1129, price_base=1890, rating=4.8, reviews=15234, brand="Jacobs"),
        product(id="2", name="Кофе молотый", price=450),
    ]
    result.by_source["ozon"] = [
        product(source="ozon", id="3", name="Кофе Ozon", url="https://www.ozon.ru/product/3/", price=999)
    ]
    return result


def test_money_formatting():
    assert formatting.money(1129) == "1 129 ₽"
    assert formatting.money(None) == "цена неизвестна"


def test_render_contains_sources_and_links():
    text = formatting.render(make_result())
    assert "🔎 <b>кофе</b>" in text
    assert "Wildberries" in text and "Ozon" in text
    assert 'href="https://www.wildberries.ru/catalog/1/detail.aspx"' in text
    assert "−40%" in text  # скидка посчиталась
    assert "от 450 ₽" in text  # минимальная цена по источнику


def test_render_keeps_fixed_source_order():
    """Порядок блоков не должен зависеть от того, кто ответил первым."""
    result = SearchResult("кофе")
    result.by_source["ozon"] = [product(source="ozon", id="3", price=999)]
    result.by_source["wb"] = [product(id="1", price=100)]
    text = formatting.render(result)
    assert text.index("Wildberries") < text.index("Ozon")


def test_render_escapes_html():
    result = SearchResult("<script>")
    result.by_source["wb"] = [product(name='Кофе <b>"злой"</b> & крепкий')]
    text = formatting.render(result)
    assert "<script>" not in text
    assert "&lt;b&gt;" in text


def test_render_reports_failed_source():
    result = SearchResult("кофе")
    result.by_source["wb"] = [product(price=100)]
    result.by_source["ozon"] = []
    result.errors["ozon"] = "403"
    text = formatting.render(result)
    assert "антибот" in text


def test_render_respects_telegram_limit():
    result = SearchResult("кофе")
    result.by_source["wb"] = [product(id=str(i), name="Кофе " * 30, price=100) for i in range(200)]
    assert len(formatting.render(result)) <= formatting.TG_LIMIT


def test_render_truncates_long_names():
    result = SearchResult("кофе")
    result.by_source["wb"] = [product(name="К" * 300, price=100)]
    assert "…" in formatting.render(result)


def test_compact_render_marks_source():
    text = formatting.render_compact(make_result().products)
    assert "<code>WB</code>" in text and "<code>OZ</code>" in text


def test_sorting_by_price():
    result = make_result()
    result.sort = "price_asc"
    prices = [p.price for p in result.products]
    assert prices == sorted(prices)

    result.sort = "price_desc"
    assert [p.price for p in result.products] == sorted(prices, reverse=True)


def test_sorting_puts_missing_prices_last():
    result = SearchResult("кофе", sort="price_asc")
    result.by_source["wb"] = [product(id="1", price=None), product(id="2", price=10)]
    assert [p.price for p in result.products] == [10, None]


def test_csv_export_roundtrip():
    data = export.to_csv(make_result().products).decode("utf-8-sig")
    lines = data.strip().splitlines()
    assert lines[0].startswith("source;id;name")
    assert len(lines) == 4
    assert "Кофе Ozon" in data


def test_json_export():
    payload = json.loads(export.to_json(make_result().products))
    assert len(payload) == 3
    assert payload[0]["discount"] == 40


def test_cache_ttl():
    cache = TTLCache(ttl=0.0)
    token = cache.put("значение")
    assert cache.get(token) is None

    cache = TTLCache(ttl=60)
    token = cache.put("значение")
    assert cache.get(token) == "значение"
    assert cache.get("нет такого") is None


def test_cache_evicts_oldest():
    cache = TTLCache(ttl=60, max_items=2)
    tokens = [cache.put(i) for i in range(4)]
    alive = [t for t in tokens if cache.get(t) is not None]
    assert len(alive) <= 2


def test_search_isolates_source_failure(monkeypatch):
    async def ok(query, **kwargs):
        return [product(price=100)]

    async def boom(query, **kwargs):
        raise search_mod.ozon.OzonBlocked("403")

    monkeypatch.setattr(search_mod.wb, "search", ok)
    monkeypatch.setattr(search_mod.ozon, "search", boom)

    result = asyncio.run(search_mod.search("кофе"))
    assert len(result.by_source["wb"]) == 1
    assert result.by_source["ozon"] == []
    assert "ozon" in result.errors
    assert not result.is_empty


def test_search_catches_unexpected_error(monkeypatch):
    async def kaboom(query, **kwargs):
        raise ValueError("сломалось")

    monkeypatch.setattr(search_mod.wb, "search", kaboom)
    monkeypatch.setattr(search_mod.ozon, "search", kaboom)

    result = asyncio.run(search_mod.search("кофе"))
    assert result.is_empty
    assert set(result.errors) == {"wb", "ozon"}


def test_search_clamps_limit(monkeypatch):
    seen = {}

    async def spy(query, **kwargs):
        seen[query] = kwargs["limit"]
        return []

    monkeypatch.setattr(search_mod.wb, "search", spy)
    asyncio.run(search_mod.search("кофе", sources=("wb",), limit=10_000))
    assert seen["кофе"] == search_mod.settings.max_limit
