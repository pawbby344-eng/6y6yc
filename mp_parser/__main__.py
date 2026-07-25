"""CLI: `python -m mp_parser bot` или `python -m mp_parser search "кофе"`.

CLI нужен в первую очередь для отладки парсеров без Telegram.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from . import export
from .config import settings
from .http import close_client
from .aggregator import SORT_KEYS, SOURCES, search


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mp_parser", description="Парсер WB и Ozon")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bot", help="запустить Telegram-бота (нужен TG_BOT_TOKEN)")

    search_cmd = sub.add_parser("search", help="разовый поиск в терминале")
    search_cmd.add_argument("query", help="поисковый запрос")
    search_cmd.add_argument(
        "-s", "--source", choices=[*SOURCES, "both"], default="both", help="где искать"
    )
    search_cmd.add_argument("-n", "--limit", type=int, default=settings.default_limit)
    search_cmd.add_argument("--sort", choices=SORT_KEYS, default="popular")
    search_cmd.add_argument("-o", "--out", type=Path, help="сохранить в .csv или .json")
    search_cmd.add_argument("-v", "--verbose", action="store_true")
    return parser


async def run_search(args: argparse.Namespace) -> int:
    sources = SOURCES if args.source == "both" else (args.source,)
    try:
        result = await search(
            args.query, sources=sources, limit=args.limit, sort=args.sort
        )
    finally:
        await close_client()

    for source, products in result.by_source.items():
        error = result.errors.get(source)
        print(f"\n=== {source.upper()} ({len(products)}){' — ' + error if error else ''} ===")
        for i, product in enumerate(products, 1):
            price = f"{product.price:.0f} ₽" if product.price else "—"
            rating = f" ⭐{product.rating:.1f}" if product.rating else ""
            print(f"{i:>2}. {price:>10}{rating}  {product.name[:70]}")
            print(f"    {product.url}")

    if args.out:
        products = result.products
        data = export.to_json(products) if args.out.suffix == ".json" else export.to_csv(products)
        args.out.write_bytes(data)
        print(f"\nСохранено: {args.out} ({len(products)} позиций)")

    return 0 if not result.is_empty else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "bot":
        from .bot import main as bot_main

        asyncio.run(bot_main())
        return 0

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(run_search(args))


if __name__ == "__main__":
    sys.exit(main())
