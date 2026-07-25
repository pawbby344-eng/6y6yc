"""HTTP-клиент с ретраями и вежливыми задержками."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from .config import settings
from .jsonfix import JsonRepairError, loads_lenient

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

# Дольше этого ждать бессмысленно: пользователь ждёт ответа в чате.
MAX_RETRY_AFTER = 10.0


def _retry_after(response: httpx.Response) -> float | None:
    """Значение заголовка Retry-After в секундах, если оно там есть."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return None  # формат с датой не поддерживаем — подождём обычным бэкоффом


class FetchError(RuntimeError):
    """Не удалось получить данные после всех попыток."""


_clients: dict[asyncio.AbstractEventLoop, httpx.AsyncClient] = {}
async def get_client() -> httpx.AsyncClient:
    """Клиент на каждый event loop — соединения переиспользуются внутри loop.

    Пул httpx привязан к тому loop, в котором создан, поэтому один клиент
    «на процесс» ломается, если в процессе сменился loop (например, два
    вызова asyncio.run подряд).
    """
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            headers=BASE_HEADERS,
            timeout=httpx.Timeout(settings.request_timeout),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
        _clients[loop] = client
    return client


async def close_client() -> None:
    """Закрывает клиент текущего loop и выбрасывает клиентов от мёртвых loop'ов."""
    loop = asyncio.get_running_loop()
    client = _clients.pop(loop, None)
    if client is not None and not client.is_closed:
        await client.aclose()
    for dead in [key for key in _clients if key.is_closed()]:
        _clients.pop(dead, None)


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
                # При 429 сервер сам говорит, сколько ждать — уважаем просьбу.
                retry_after = _retry_after(resp)
                if retry_after is not None and attempt < attempts:
                    log.info("жду %.1f с по Retry-After", retry_after)
                    await asyncio.sleep(min(retry_after, MAX_RETRY_AFTER))
                    continue
            else:
                resp.raise_for_status()
                return loads_lenient(resp.text, url=url)
        except JsonRepairError as exc:
            # Повторять нечего: ответ пришёл целиком, просто он не JSON.
            raise FetchError(f"{url} вернул неразбираемый ответ: {exc}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            log.warning("попытка %s/%s не удалась (%s): %s", attempt, attempts, type(exc).__name__, exc)

        if attempt < attempts:
            delay = settings.retry_backoff * (2 ** (attempt - 1))
            await asyncio.sleep(delay + random.uniform(0, 0.3))

    raise FetchError(f"не удалось получить {url}: {last_error}")
