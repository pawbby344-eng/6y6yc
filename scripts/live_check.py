#!/usr/bin/env python3
"""Живая проверка парсеров на настоящих WB и Ozon.

Запускать на машине, у которой есть доступ к маркетплейсам:

    python scripts/live_check.py "кофе в зёрнах"

Показывает по шагам: доступность эндпоинтов, что вернул каждый источник, какие
поля удалось разобрать, и сообщение ровно в том виде, в каком его отправит бот.
Ненулевой код возврата — что-то не работает.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from mp_parser import aggregator, formatting, ozon, wb  # noqa: E402
from mp_parser.config import settings  # noqa: E402
from mp_parser.http import UA, close_client  # noqa: E402
from mp_parser.models import Product  # noqa: E402

# Щупаем ровно те адреса, которыми пользуется парсер. Корни доменов проверять
# бессмысленно: search.wb.ru/ отдаёт 307 в никуда, и это ничего не говорит
# о работоспособности поиска.
ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("WB search", wb.SEARCH_URL + "?query=%D0%BA%D0%BE%D1%84%D0%B5&resultset=catalog&dest=-1257786&appType=1&curr=rub"),
    ("Ozon composer-api", ozon.API_URL + "?url=%2Fsearch%2F%3Ftext%3Dtest"),
    ("Ozon сайт", ozon.SITE_SEARCH_URL.format(query="test")),
    ("Telegram API", "https://api.telegram.org/"),
)

OK = "✅"
FAIL = "❌"
WARN = "⚠️ "


async def check_endpoints() -> None:
    """Показывает, что отвечают реальные адреса. Ничего не блокирует.

    Любой HTTP-ответ (даже 403 или 498) означает, что сеть есть — дальше всё
    решает уже разбор, поэтому прогон продолжается в любом случае.
    """
    print("== Доступность эндпоинтов ==")
    # follow_redirects=False: редирект — это сам по себе ответ, гнаться за ним
    # незачем, а его цель может висеть до таймаута.
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        for name, url in ENDPOINTS:
            try:
                response = await client.get(url, headers={"User-Agent": UA})
                note = " (антибот, но сеть есть)" if response.status_code in (403, 498) else ""
                print(f"{OK} {name} -> HTTP {response.status_code}{note}")
            except httpx.HTTPError as exc:
                print(f"{WARN}{name}: {type(exc).__name__}: {exc or 'таймаут'}")


def describe(products: list[Product], source: str) -> bool:
    """Печатает, что разобралось, и предупреждает о пустых полях."""
    if not products:
        print(f"{FAIL} {source}: пусто")
        return False

    print(f"{OK} {source}: {len(products)} товаров")
    for product in products[:3]:
        price = f"{product.price:.0f} ₽" if product.price else "—"
        base = f" (было {product.price_base:.0f})" if product.price_base else ""
        rating = f" ⭐{product.rating}" if product.rating else ""
        reviews = f" 💬{product.reviews}" if product.reviews else ""
        print(f"   {price}{base}{rating}{reviews}  {product.name[:60]}")
        print(f"   {product.url}")

    # Пустое поле у всех товаров — почти наверняка изменившаяся схема ответа.
    for field in ("price", "name", "url", "rating", "reviews"):
        if all(getattr(p, field) in (None, "", 0) for p in products):
            print(f"{WARN}{source}: поле {field} не разобралось ни у одного товара —"
                  f" вероятно, схема ответа изменилась")
    return True


async def main(query: str) -> int:
    logging.basicConfig(level=logging.INFO, format="   %(levelname)s %(name)s: %(message)s")
    problems = 0

    await check_endpoints()

    print(f"\n== Wildberries: {query!r} ==")
    try:
        if not describe(await wb.search(query, limit=5), "WB"):
            problems += 1
    except Exception as exc:
        print(f"{FAIL} WB упал: {type(exc).__name__}: {exc}")
        problems += 1

    print(f"\n== Ozon: {query!r} (браузер: {'да' if settings.ozon_use_browser else 'нет'}) ==")
    try:
        if not describe(await ozon.search(query, limit=5), "Ozon"):
            problems += 1
    except ozon.OzonBlocked as exc:
        print(f"{FAIL} Ozon заблокировал: {exc}")
        print("   Проверь, что Playwright на месте: python -m playwright install chromium")
        problems += 1
    except Exception as exc:
        print(f"{FAIL} Ozon упал: {type(exc).__name__}: {exc}")
        problems += 1

    print("\n== Сообщение, как его отправит бот ==")
    result = await aggregator.search(query, limit=3)
    print(formatting.render(result, per_source=3))

    await close_client()

    print("\n== Итог ==")
    if problems:
        print(f"{FAIL} проблем: {problems}. Если сеть в порядке, начни с фикстур в"
              f" tests/fixtures — обнови их свежим ответом и поправь разбор.")
    else:
        print(f"{OK} оба источника отвечают и разбираются")
    return 1 if problems else 0


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "кофе в зёрнах 1 кг"
    sys.exit(asyncio.run(main(query)))
