"""Разбор слегка испорченного JSON.

WB иногда отвечает 200, но с телом, которое json.loads не берёт: висячие
запятые, пропущенные элементы, управляющие символы внутри строк. Ронять из-за
этого весь поиск незачем — сначала пробуем аккуратно починить, и только если
не вышло, сохраняем сырое тело на диск, чтобы было с чем разбираться.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

DUMP_DIR = Path("dumps")

# Порядок важен: сначала убираем лишние запятые, потом чиним кавычки.
_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r",\s*(?=[}\]])"), ""),          # висячая запятая: {"a":1,}
    (re.compile(r"(?<=[{\[])\s*,"), ""),         # пустой первый элемент: [,1]
    (re.compile(r",\s*,"), ","),                 # пропущенный элемент: [1,,2]
    (re.compile(r"(?<=[{,])\s*'([^'\\]+)'\s*:"), r'"\1":'),  # ключ в одинарных кавычках
    (re.compile(r":\s*'([^'\\]*)'\s*(?=[,}\]])"), r':"\1"'),  # значение в одинарных кавычках
)

# Управляющие символы, недопустимые внутри JSON-строк.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class JsonRepairError(ValueError):
    """JSON не удалось разобрать даже после починки."""

    def __init__(self, message: str, *, fragment: str, dump: Path | None) -> None:
        super().__init__(message)
        self.fragment = fragment
        self.dump = dump


def fragment_around(text: str, position: int, *, width: int = 120) -> str:
    """Кусок текста вокруг позиции ошибки — то, что нужно увидеть глазами."""
    start = max(0, position - width)
    end = min(len(text), position + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    marker = " " * (position - start + len(prefix)) + "^"
    return f"{prefix}{text[start:end]}{suffix}\n{marker}"


def dump_body(text: str, url: str, *, directory: Path | None = None) -> Path | None:
    """Сохраняет сырое тело ответа, чтобы можно было починить разбор по факту."""
    directory = directory or DUMP_DIR
    try:
        directory.mkdir(parents=True, exist_ok=True)
        host = urlparse(url).hostname or "response"
        path = directory / f"{host}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        path.write_text(text, encoding="utf-8")
        return path
    except OSError as exc:
        log.warning("не смог сохранить дамп ответа: %s", exc)
        return None


def repair(text: str) -> str:
    """Применяет типовые починки к тексту JSON."""
    fixed = _CONTROL_CHARS.sub("", text)
    for pattern, replacement in _FIXES:
        fixed = pattern.sub(replacement, fixed)
    return fixed


def loads_lenient(text: str, *, url: str = "", dump_on_failure: bool = True) -> Any:
    """json.loads, но с попыткой починить типовые поломки.

    Если починить не удалось — сохраняет тело в dumps/ и кидает JsonRepairError
    с фрагментом вокруг места ошибки.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as first_error:
        log.debug("JSON не разобрался с первого раза: %s", first_error)

    repaired = repair(text)
    if repaired != text:
        try:
            payload = json.loads(repaired)
            log.info("JSON от %s разобрался после починки", url or "источника")
            return payload
        except json.JSONDecodeError:
            pass

    # Последняя попытка: обрезать мусор после последней закрывающей скобки.
    trimmed = text.rstrip()
    for closing in ("}", "]"):
        cut = trimmed.rfind(closing)
        if cut > 0:
            try:
                return json.loads(trimmed[: cut + 1])
            except json.JSONDecodeError:
                continue

    error = json.JSONDecodeError("", text, 0)
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        error = exc

    fragment = fragment_around(text, error.pos)
    dump = dump_body(text, url) if dump_on_failure else None
    where = f", сырой ответ сохранён в {dump}" if dump else ""
    raise JsonRepairError(
        f"{error.msg} (позиция {error.pos}){where}\n{fragment}",
        fragment=fragment,
        dump=dump,
    )
