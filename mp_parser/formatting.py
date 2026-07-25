"""Оформление результатов для Telegram (parse_mode=HTML)."""

from __future__ import annotations

from html import escape

from .models import Product
from .aggregator import SearchResult

SOURCE_TITLES = {"wb": "🟣 Wildberries", "ozon": "🔵 Ozon"}
SOURCE_ERRORS = {
    "wb": "Wildberries не ответил",
    "ozon": "Ozon не отдал выдачу (антибот)",
}
TG_LIMIT = 4096


def money(value: float | None) -> str:
    if value is None:
        return "цена неизвестна"
    return f"{value:,.0f} ₽".replace(",", " ")


def _clip(text: str, length: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def product_line(product: Product, index: int) -> str:
    """Одна позиция выдачи: название-ссылка, цена, рейтинг, продавец."""
    title = escape(_clip(product.name))
    lines = [f'{index}. <a href="{escape(product.url, quote=True)}">{title}</a>']

    price = f"💰 <b>{money(product.price)}</b>"
    discount = product.discount
    if discount:
        price += f"  <s>{money(product.price_base)}</s>  −{discount}%"
    lines.append(price)

    meta: list[str] = []
    if product.rating:
        meta.append(f"⭐ {product.rating:.1f}")
    if product.reviews:
        meta.append(f"💬 {product.reviews}")
    if product.brand:
        meta.append(escape(_clip(product.brand, 24)))
    elif product.seller:
        meta.append(escape(_clip(product.seller, 24)))
    if meta:
        lines.append("   ·  ".join(meta))

    return "\n".join(lines)


def render(result: SearchResult, *, per_source: int | None = None) -> str:
    """Собирает сообщение по результатам поиска."""
    header = f"🔎 <b>{escape(result.query)}</b>"
    blocks: list[str] = [header]

    for source, products in result.by_source.items():
        title = SOURCE_TITLES.get(source, source)
        if not products:
            reason = SOURCE_ERRORS.get(source, "ничего не нашлось")
            note = reason if source in result.errors else "ничего не нашлось"
            blocks.append(f"{title}\n<i>{escape(note)}</i>")
            continue

        shown = products[:per_source] if per_source else products
        lines = [product_line(p, i) for i, p in enumerate(shown, 1)]
        cheapest = min((p.price for p in shown if p.price), default=None)
        subtitle = f"{title} · от {money(cheapest)}" if cheapest else title
        blocks.append(subtitle + "\n\n" + "\n\n".join(lines))

    text = "\n\n".join(blocks)
    if len(text) > TG_LIMIT:
        text = text[: TG_LIMIT - 20].rsplit("\n", 1)[0] + "\n…"
    return text


def render_compact(products: list[Product]) -> str:
    """Плоский список — для сортировки «по цене» вперемешку из двух источников."""
    if not products:
        return "Ничего не нашлось 🤷"
    tags = {"wb": "WB", "ozon": "OZ"}
    lines = []
    for i, product in enumerate(products, 1):
        tag = tags.get(product.source, product.source)
        title = escape(_clip(product.name, 70))
        lines.append(
            f'{i}. <code>{tag}</code> <a href="{escape(product.url, quote=True)}">{title}</a>'
            f"\n   💰 <b>{money(product.price)}</b>"
            + (f"  ⭐ {product.rating:.1f}" if product.rating else "")
        )
    text = "\n".join(lines)
    return text[:TG_LIMIT]
