"""Настройки через переменные окружения (см. .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Минимальный .env-лоадер, чтобы не тянуть python-dotenv."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(Path.cwd() / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(slots=True)
class Settings:
    bot_token: str = field(default_factory=lambda: os.environ.get("TG_BOT_TOKEN", ""))
    # Регион доставки WB: -1257786 — Москва. Влияет на цены и наличие.
    wb_dest: str = field(default_factory=lambda: os.environ.get("WB_DEST", "-1257786"))
    request_timeout: float = field(default_factory=lambda: _env_float("REQUEST_TIMEOUT", 20.0))
    retries: int = field(default_factory=lambda: _env_int("RETRIES", 3))
    retry_backoff: float = field(default_factory=lambda: _env_float("RETRY_BACKOFF", 1.0))
    default_limit: int = field(default_factory=lambda: _env_int("DEFAULT_LIMIT", 5))
    max_limit: int = field(default_factory=lambda: _env_int("MAX_LIMIT", 30))
    # Ozon почти всегда требует браузер: без него ждём 403 от анти-бота.
    ozon_use_browser: bool = field(default_factory=lambda: _env_bool("OZON_USE_BROWSER", True))
    ozon_browser_path: str = field(
        default_factory=lambda: os.environ.get("OZON_BROWSER_PATH", "")
    )
    cache_ttl: float = field(default_factory=lambda: _env_float("CACHE_TTL", 600.0))


settings = Settings()
