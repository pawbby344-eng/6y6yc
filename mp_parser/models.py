"""Общая модель товара для всех маркетплейсов."""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any


@dataclass(slots=True)
class Product:
    source: str  # "wb" | "ozon"
    id: str
    name: str
    url: str
    price: float | None = None  # итоговая цена, руб.
    price_base: float | None = None  # цена без скидки, руб.
    brand: str | None = None
    seller: str | None = None
    rating: float | None = None
    reviews: int | None = None
    image: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def discount(self) -> int | None:
        """Скидка в процентах, если известны обе цены."""
        if not self.price or not self.price_base or self.price_base <= self.price:
            return None
        return round((1 - self.price / self.price_base) * 100)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["discount"] = self.discount
        return d


def to_float(value: Any) -> float | None:
    """Достаёт число из значения любого вида: 1290, "1 290 ₽", "1290.5"."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    cleaned = "".join(ch for ch in value if ch.isdigit() or ch in ".,")
    cleaned = cleaned.replace(",", ".")
    if cleaned.count(".") > 1:  # "1.290.50" — считаем точки разделителями разрядов
        head, _, tail = cleaned.rpartition(".")
        cleaned = head.replace(".", "") + "." + tail
    try:
        return float(cleaned)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    f = to_float(value)
    return None if f is None else int(f)
