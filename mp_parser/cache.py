"""Небольшой in-memory кэш запросов с TTL — для callback-кнопок бота."""

from __future__ import annotations

import time
import uuid
from typing import Any

from .config import settings


class TTLCache:
    def __init__(self, ttl: float | None = None, max_items: int = 500) -> None:
        self.ttl = ttl if ttl is not None else settings.cache_ttl
        self.max_items = max_items
        self._data: dict[str, tuple[float, Any]] = {}

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._data.items() if now - ts > self.ttl]
        for key in expired:
            self._data.pop(key, None)
        while len(self._data) > self.max_items:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            self._data.pop(oldest, None)

    def put(self, value: Any) -> str:
        """Кладёт значение и возвращает короткий токен для callback_data."""
        token = uuid.uuid4().hex[:12]
        self._data[token] = (time.monotonic(), value)
        self._purge()
        return token

    def get(self, token: str) -> Any | None:
        entry = self._data.get(token)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self.ttl:
            self._data.pop(token, None)
            return None
        return value
