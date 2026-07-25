"""Тесты хендлеров бота: реальные функции из bot.py на поддельных объектах TG.

Сам Telegram недоступен, но вся логика между «пришло сообщение» и «отправлен
ответ» — наша, и именно она здесь проверяется: тексты, кнопки, переключение
сортировки, расширение выдачи, CSV, устаревшие callback-и и обработка падений.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mp_parser import bot
from mp_parser.aggregator import SearchResult
from mp_parser.config import settings
from mp_parser.models import Product


# ---------------------------- поддельные объекты ---------------------------- #

class FakeMessage:
    """Минимальный Message: запоминает, что бот отправил и чем это отредактировал."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []

    async def answer(self, text: str, **kwargs: Any) -> "FakeMessage":
        self.sent.append({"text": text, **kwargs})
        reply = FakeMessage(text)
        reply.parent = self  # type: ignore[attr-defined]
        self._last_reply = reply  # type: ignore[attr-defined]
        return reply

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append({"text": text, **kwargs})

    async def answer_document(self, document: Any, **kwargs: Any) -> None:
        self.documents.append({"document": document, **kwargs})


class FakeCallback:
    def __init__(self, data: str, message: FakeMessage | None = None) -> None:
        self.data = data
        self.message = message or FakeMessage()
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str = "", **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})


def product(**kwargs: Any) -> Product:
    base = dict(source="wb", id="1", name="Кофе", url="https://www.wildberries.ru/catalog/1/detail.aspx")
    base.update(kwargs)
    return Product(**base)


def fake_search_factory(calls: list[dict[str, Any]], *, empty: bool = False, errors: bool = False):
    """Подменяет aggregator.search и протоколирует переданные аргументы."""

    async def fake_search(query: str, **kwargs: Any) -> SearchResult:
        calls.append({"query": query, **kwargs})
        result = SearchResult(query, kwargs.get("sort", "popular"))
        if empty:
            result.by_source = {"wb": [], "ozon": []}
            if errors:
                result.errors = {"ozon": "403"}
            return result
        limit = kwargs.get("limit", 5)
        result.by_source["wb"] = [
            product(id=str(i), name=f"Кофе WB {i}", price=100 * (i + 1)) for i in range(limit)
        ]
        result.by_source["ozon"] = [
            product(source="ozon", id=f"o{i}", name=f"Кофе OZ {i}",
                    url=f"https://www.ozon.ru/product/{i}/", price=90 * (i + 1))
            for i in range(limit)
        ]
        return result

    return fake_search


@pytest.fixture
def calls(monkeypatch):
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(bot, "search", fake_search_factory(recorded))
    monkeypatch.setattr(bot, "_cache", bot.TTLCache(ttl=60))
    return recorded


def last_edit(message: FakeMessage) -> dict[str, Any]:
    """Плейсхолдер «Ищу…» — это ответ на сообщение; правки уходят в него."""
    placeholder = message._last_reply  # type: ignore[attr-defined]
    assert placeholder.edits, "бот ничего не отредактировал"
    return placeholder.edits[-1]


# --------------------------------- команды ---------------------------------- #

def test_start_sends_help():
    message = FakeMessage("/start")
    asyncio.run(bot.cmd_start(message))
    assert "Wildberries" in message.sent[0]["text"]
    assert "/ozon" in message.sent[0]["text"]


def test_text_query_replies_with_results(calls):
    message = FakeMessage("кофе в зёрнах")
    asyncio.run(bot.on_text(message))

    assert message.sent[0]["text"] == "Ищу… ⏳"
    edit = last_edit(message)
    assert "🔎 <b>кофе в зёрнах</b>" in edit["text"]
    assert "Кофе WB 0" in edit["text"] and "Кофе OZ 0" in edit["text"]
    assert edit["disable_web_page_preview"] is True
    assert calls[0]["query"] == "кофе в зёрнах"
    assert calls[0]["sources"] == ("wb", "ozon")


def test_short_query_rejected(calls):
    message = FakeMessage("к")
    asyncio.run(bot.on_text(message))
    assert "Слишком короткий" in message.sent[0]["text"]
    assert not calls


def test_long_query_is_trimmed(calls):
    asyncio.run(bot.on_text(FakeMessage("к" * 500)))
    assert len(calls[0]["query"]) == 200


def test_wb_command_limits_source(calls):
    asyncio.run(bot.cmd_wb(FakeMessage("/wb кофе арабика")))
    assert calls[0]["sources"] == ("wb",)
    assert calls[0]["query"] == "кофе арабика"


def test_ozon_command_limits_source(calls):
    asyncio.run(bot.cmd_ozon(FakeMessage("/ozon кофе")))
    assert calls[0]["sources"] == ("ozon",)


def test_command_without_query_asks_for_it(calls):
    message = FakeMessage("/wb")
    asyncio.run(bot.cmd_wb(message))
    assert "Добавь запрос" in message.sent[0]["text"]
    assert not calls


def test_empty_result_reports_reason(monkeypatch):
    monkeypatch.setattr(bot, "search", fake_search_factory([], empty=True, errors=True))
    message = FakeMessage("абракадабра")
    asyncio.run(bot.on_text(message))
    text = last_edit(message)["text"]
    assert "Ничего не нашлось" in text
    assert "антибот" in text


def test_search_failure_is_reported(monkeypatch):
    async def boom(*args: Any, **kwargs: Any):
        raise RuntimeError("сеть отвалилась")

    monkeypatch.setattr(bot, "search", boom)
    message = FakeMessage("кофе")
    asyncio.run(bot.on_text(message))
    assert "сломалось" in last_edit(message)["text"]


# --------------------------------- кнопки ----------------------------------- #

def test_keyboard_layout():
    markup = bot.keyboard("tok", limit=5, sort="popular")
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert labels == ["💰 По цене", "➕ Ещё", "📄 CSV", "🔄 Обновить"]
    assert all(b.callback_data.endswith("tok") for row in markup.inline_keyboard for b in row)


def test_keyboard_hides_more_at_max_limit():
    markup = bot.keyboard("tok", limit=settings.max_limit, sort="popular")
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert "➕ Ещё" not in labels


def test_keyboard_toggles_sort_label():
    markup = bot.keyboard("tok", limit=5, sort="price_asc")
    assert markup.inline_keyboard[0][0].text == "🔥 По популярности"


def _token_from(message: FakeMessage) -> str:
    placeholder = message._last_reply  # type: ignore[attr-defined]
    markup = placeholder.edits[-1]["reply_markup"]
    return markup.inline_keyboard[0][0].callback_data.split(":", 1)[1]


def test_sort_button_switches_to_price_view(calls):
    message = FakeMessage("кофе")
    asyncio.run(bot.on_text(message))
    token = _token_from(message)

    view = FakeMessage()
    asyncio.run(bot.on_callback(FakeCallback(f"sort:{token}", view)))

    assert calls[-1]["sort"] == "price_asc"
    text = view.edits[-1]["text"]
    assert "по цене" in text
    assert "<code>WB</code>" in text and "<code>OZ</code>" in text
    # выдача действительно отсортирована по возрастанию цены
    prices = [int(chunk) for chunk in ("90", "100", "180", "200") if chunk in text.replace(" ", "")]
    assert prices == sorted(prices)


def test_more_button_doubles_limit(calls):
    message = FakeMessage("кофе")
    asyncio.run(bot.on_text(message))
    token = _token_from(message)
    first_limit = calls[0]["limit"]

    asyncio.run(bot.on_callback(FakeCallback(f"more:{token}", FakeMessage())))
    assert calls[-1]["limit"] == min(first_limit * 2, settings.max_limit)


def test_more_button_stops_at_max_limit(calls, monkeypatch):
    monkeypatch.setattr(settings, "max_limit", 8)
    message = FakeMessage("кофе")
    asyncio.run(bot.on_text(message))
    token = _token_from(message)

    for _ in range(4):
        asyncio.run(bot.on_callback(FakeCallback(f"more:{token}", FakeMessage())))
    assert calls[-1]["limit"] == 8


def test_stale_token_asks_to_repeat(calls):
    callback = FakeCallback("sort:нетакоготокена")
    asyncio.run(bot.on_callback(callback))
    assert "устарел" in callback.answers[0]["text"]
    assert callback.answers[0]["show_alert"] is True
    assert not calls


def test_csv_button_sends_document(calls):
    message = FakeMessage("кофе")
    asyncio.run(bot.on_text(message))
    token = _token_from(message)

    view = FakeMessage()
    asyncio.run(bot.on_callback(FakeCallback(f"csv:{token}", view)))

    assert len(view.documents) == 1
    document = view.documents[0]["document"]
    assert document.filename == "кофе.csv"
    body = document.data.decode("utf-8-sig")
    assert body.startswith("source;id;name")
    assert "Кофе WB 0" in body and "Кофе OZ 0" in body
    # для файла выгружаем максимум, а не только показанное
    assert calls[-1]["limit"] == settings.max_limit


def test_csv_filename_is_sanitized(calls):
    message = FakeMessage('кофе/../злой "файл"')
    asyncio.run(bot.on_text(message))
    token = _token_from(message)

    view = FakeMessage()
    asyncio.run(bot.on_callback(FakeCallback(f"csv:{token}", view)))

    filename = view.documents[0]["document"].filename
    assert "/" not in filename and '"' not in filename
    assert filename.endswith(".csv")


def test_csv_with_no_results_warns(monkeypatch):
    monkeypatch.setattr(bot, "search", fake_search_factory([], empty=True))
    monkeypatch.setattr(bot, "_cache", bot.TTLCache(ttl=60))
    message = FakeMessage("абракадабра")
    asyncio.run(bot.on_text(message))
    token = _token_from(message)

    callback = FakeCallback(f"csv:{token}", FakeMessage())
    asyncio.run(bot.on_callback(callback))
    assert any("Нечего выгружать" in a["text"] for a in callback.answers)


def test_callback_failure_is_reported(calls, monkeypatch):
    message = FakeMessage("кофе")
    asyncio.run(bot.on_text(message))
    token = _token_from(message)

    async def boom(*args: Any, **kwargs: Any):
        raise RuntimeError("упало")

    monkeypatch.setattr(bot, "search", boom)
    callback = FakeCallback(f"more:{token}", FakeMessage())
    asyncio.run(bot.on_callback(callback))
    assert any("Не получилось" in a["text"] for a in callback.answers)
