"""Тесты разбора ответов маркетплейсов на записанных фикстурах."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mp_parser import ozon, wb
from mp_parser.models import Product, to_float, to_int

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --------------------------------- WB -------------------------------------- #

@pytest.fixture
def wb_products() -> list[Product]:
    return wb.parse_products(load("wb_search.json"))


def test_wb_skips_items_without_id(wb_products):
    assert len(wb_products) == 3
    assert all(p.id for p in wb_products)


def test_wb_prices_from_sizes(wb_products):
    first = wb_products[0]
    assert first.name == "Кофе в зернах Арабика 1 кг"
    assert first.price == pytest.approx(1129.0)
    assert first.price_base == pytest.approx(1890.0)
    assert first.discount == 40
    assert first.rating == pytest.approx(4.8)
    assert first.reviews == 15234
    assert first.seller == "ООО Кофейня"
    assert first.url == "https://www.wildberries.ru/catalog/149528072/detail.aspx"


def test_wb_legacy_price_fields(wb_products):
    legacy = wb_products[1]
    assert legacy.price == pytest.approx(450.0)
    assert legacy.price_base == pytest.approx(900.0)
    assert legacy.discount == 50


def test_wb_missing_price_is_none(wb_products):
    assert wb_products[2].price is None
    assert wb_products[2].discount is None


def test_wb_limit_applies():
    assert len(wb.parse_products(load("wb_search.json"), limit=1)) == 1


def test_wb_handles_flat_products_key():
    payload = {"products": [{"id": 1, "name": "X", "salePriceU": 10000}]}
    products = wb.parse_products(payload)
    assert len(products) == 1 and products[0].price == pytest.approx(100.0)


def test_wb_handles_garbage_payload():
    assert wb.parse_products(None) == []
    assert wb.parse_products({"data": {}}) == []
    assert wb.parse_products({"data": {"products": ["мусор"]}}) == []


def test_wb_image_url_buckets():
    assert wb.image_url(149528072).startswith("https://basket-10.wbbasket.ru/vol1495/part149528/")
    assert "/vol0/part0/" in wb.image_url(7)


# -------------------------------- Ozon ------------------------------------- #

@pytest.fixture
def ozon_products() -> list[Product]:
    return ozon.parse_products(load("ozon_search.json"))


def test_ozon_parses_tracking_info(ozon_products):
    assert len(ozon_products) == 2
    first = ozon_products[0]
    assert first.id == "1234567"
    assert first.name == "Кофе в зернах Арабика 1 кг"
    assert first.price == pytest.approx(1129.0)
    assert first.price_base == pytest.approx(1890.0)
    assert first.discount == 40
    assert first.brand == "Jacobs"
    assert first.seller == "Кофейня"
    assert first.rating == pytest.approx(4.7)
    assert first.reviews == 1204
    assert first.url.startswith("https://www.ozon.ru/product/")
    assert first.image == "https://cdn1.ozone.ru/s3/1.jpg"


def test_ozon_falls_back_to_atoms(ozon_products):
    second = ozon_products[1]
    assert second.id == "7654321"
    assert second.name == "Кофе молотый 250 г"  # html-теги вычищены
    assert second.price == pytest.approx(450.0)
    assert second.rating == pytest.approx(4.2)
    assert second.reviews == 38


def test_ozon_deduplicates_by_id(ozon_products):
    assert len({p.id for p in ozon_products}) == len(ozon_products)


def test_ozon_handles_garbage_payload():
    assert ozon.parse_products(None) == []
    assert ozon.parse_products({"widgetStates": {"a": "{broken"}}) == []
    assert ozon.parse_products({"widgetStates": {"a": json.dumps({"items": [1, 2]})}}) == []


def test_ozon_swaps_inverted_prices():
    payload = {
        "widgetStates": {
            "searchResultsV2-1": json.dumps(
                {"items": [{"cellTrackingInfo": {"id": 1, "title": "T", "price": 100, "finalPrice": 200}}]}
            )
        }
    }
    product = ozon.parse_products(payload)[0]
    assert product.price == 100 and product.price_base == 200


# ------------------------------- Утилиты ----------------------------------- #

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1 290 ₽", 1290.0),
        ("4,7", 4.7),
        ("1.290.50", 1290.5),
        (1290, 1290.0),
        (None, None),
        ("", None),
        ("нет цены", None),
        (True, None),
    ],
)
def test_to_float(raw, expected):
    assert to_float(raw) == expected


def test_to_int():
    assert to_int("1 204 отзыва") == 1204
    assert to_int(None) is None
