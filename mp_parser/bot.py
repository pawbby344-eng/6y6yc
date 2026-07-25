"""Telegram-бот: присылаешь запрос — получаешь выдачу WB и Ozon."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import export, formatting
from .cache import TTLCache
from .config import settings
from .http import close_client
from .aggregator import SOURCES, SearchResult, search

log = logging.getLogger(__name__)

router = Dispatcher()
_cache = TTLCache()

HELP = (
    "Привет! Ищу товары на <b>Wildberries</b> и <b>Ozon</b>.\n\n"
    "Просто пришли запрос текстом, например:\n"
    "<code>кофе в зёрнах 1 кг</code>\n\n"
    "Команды:\n"
    "/wb <i>запрос</i> — только Wildberries\n"
    "/ozon <i>запрос</i> — только Ozon\n"
    "/help — эта справка\n\n"
    "Под выдачей есть кнопки: пересортировать по цене, показать больше "
    "позиций и выгрузить всё в CSV."
)


def keyboard(token: str, *, limit: int, sort: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="💰 По цене" if sort != "price_asc" else "🔥 По популярности",
                callback_data=f"sort:{token}",
            ),
            InlineKeyboardButton(text="➕ Ещё", callback_data=f"more:{token}"),
        ],
        [
            InlineKeyboardButton(text="📄 CSV", callback_data=f"csv:{token}"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"again:{token}"),
        ],
    ]
    if limit >= settings.max_limit:
        rows[0].pop()  # больше расширять некуда
    return InlineKeyboardMarkup(inline_keyboard=rows)


class Query:
    """Состояние одного поискового запроса, живёт в кэше под токеном."""

    def __init__(self, text: str, sources: tuple[str, ...], limit: int, sort: str) -> None:
        self.text = text
        self.sources = sources
        self.limit = limit
        self.sort = sort


def _render(result: SearchResult, state: Query) -> str:
    if state.sort in ("price_asc", "price_desc"):
        head = f"🔎 <b>{formatting.escape(result.query)}</b> · по цене\n\n"
        return (head + formatting.render_compact(result.products))[: formatting.TG_LIMIT]
    return formatting.render(result, per_source=state.limit)


async def _run_and_reply(message: Message, state: Query, *, edit: Message | None = None) -> None:
    result = await search(
        state.text, sources=state.sources, limit=state.limit, sort=state.sort
    )
    token = _cache.put(state)
    text = _render(result, state)
    markup = keyboard(token, limit=state.limit, sort=state.sort)

    if result.is_empty:
        note = "Ничего не нашлось 🤷"
        if result.errors:
            note += "\n\n" + "\n".join(
                f"<i>{formatting.SOURCE_ERRORS.get(src, src)}</i>" for src in result.errors
            )
        text = f"🔎 <b>{formatting.escape(state.text)}</b>\n\n{note}"

    if edit is not None:
        await edit.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    else:
        await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message) -> None:
    await message.answer(HELP, disable_web_page_preview=True)


@router.message(Command("wb"))
async def cmd_wb(message: Message) -> None:
    await _handle_scoped(message, ("wb",))


@router.message(Command("ozon"))
async def cmd_ozon(message: Message) -> None:
    await _handle_scoped(message, ("ozon",))


async def _handle_scoped(message: Message, sources: tuple[str, ...]) -> None:
    parts = (message.text or "").split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""
    if not query:
        await message.answer("Добавь запрос после команды, например: <code>/wb кофе</code>")
        return
    await _search_flow(message, query, sources)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Слишком короткий запрос — напиши хотя бы два символа.")
        return
    await _search_flow(message, query, SOURCES)


async def _search_flow(message: Message, query: str, sources: tuple[str, ...]) -> None:
    placeholder = await message.answer("Ищу… ⏳")
    state = Query(query[:200], sources, settings.default_limit, "popular")
    try:
        await _run_and_reply(message, state, edit=placeholder)
    except Exception:
        log.exception("поиск упал для %r", query)
        await placeholder.edit_text("Что-то сломалось при поиске 😕 Попробуй ещё раз.")


@router.callback_query(F.data.startswith(("sort:", "more:", "again:", "csv:")))
async def on_callback(callback: CallbackQuery) -> None:
    action, _, token = (callback.data or "").partition(":")
    state: Query | None = _cache.get(token)
    if state is None:
        await callback.answer("Запрос устарел, отправь его заново", show_alert=True)
        return

    if action == "csv":
        await _send_csv(callback, state)
        return

    if action == "sort":
        state.sort = "popular" if state.sort == "price_asc" else "price_asc"
    elif action == "more":
        state.limit = min(state.limit * 2, settings.max_limit)

    await callback.answer("Обновляю…")
    try:
        await _run_and_reply(callback.message, state, edit=callback.message)
    except Exception:
        log.exception("callback %s упал", action)
        await callback.answer("Не получилось обновить 😕", show_alert=True)


async def _send_csv(callback: CallbackQuery, state: Query) -> None:
    await callback.answer("Готовлю файл…")
    result = await search(
        state.text,
        sources=state.sources,
        limit=max(state.limit, settings.max_limit),
        sort=state.sort,
    )
    products = result.products
    if not products:
        await callback.answer("Нечего выгружать", show_alert=True)
        return
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in state.text)[:40].strip()
    await callback.message.answer_document(
        BufferedInputFile(export.to_csv(products), filename=f"{safe_name or 'search'}.csv"),
        caption=f"{len(products)} позиций по запросу «{formatting.escape(state.text)}»",
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if not settings.bot_token:
        raise SystemExit("Не задан TG_BOT_TOKEN (см. .env.example)")

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        await router.start_polling(bot)
    finally:
        await close_client()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
