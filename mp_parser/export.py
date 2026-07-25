"""Выгрузка результатов в CSV/JSON."""

from __future__ import annotations

import csv
import io
import json

from .models import Product

COLUMNS = (
    "source", "id", "name", "price", "price_base", "discount",
    "brand", "seller", "rating", "reviews", "url", "image",
)


def to_csv(products: list[Product]) -> bytes:
    """CSV с BOM — чтобы Excel не ломал кириллицу."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, delimiter=";", extrasaction="ignore")
    writer.writeheader()
    for product in products:
        writer.writerow({k: v for k, v in product.as_dict().items() if k in COLUMNS})
    return buffer.getvalue().encode("utf-8-sig")


def to_json(products: list[Product]) -> bytes:
    payload = [p.as_dict() for p in products]
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
