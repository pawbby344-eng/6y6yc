"""HTTP-клиент с ретраями и вежливыми задержками."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}

RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """Не удалось получить данные после всех попыток."""


_client: httpx.AsyncClient | None = None
_lock = asyncio.Lock()


async def get_client() -> httpx.AsyncClient:
    """Один общий клиент на процесс — переиспользуем соединения."""
    global _client
    async with _lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(
                headers=BASE_HEADERS,
                timeout=httpx.Timeout(settings.request_timeout),
                follow_redirects=True,
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    attempts: int | None = None,
) -> Any:
    """GET с ретраями по сетевым ошибкам и «мягким» кодам ответа."""
    attempts = attempts or settings.retries
    client = await get_client()
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code in RETRY_STATUSES:
                last_error = FetchError(f"{url} -> HTTP {resp.status_code}")
                log.warning("попытка %s/%s: HTTP %s для %s", attempt, attempts, resp.status_code, url)
            else:
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            log.warning("попытка %s/%s не удалась (%s): %s", attempt, attempts, type(exc).__name__, exc)

        if attempt < attempts:
            delay = settings.retry_backoff * (2 ** (attempt - 1))
            await asyncio.sleep(delay + random.uniform(0, 0.3))

    raise FetchError(f"не удалось получить {url}: {last_error}")
