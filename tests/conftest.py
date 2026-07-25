"""Локальный HTTP-сервер для тестов: настоящий сетевой путь без выхода в интернет."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import pytest


class StubServer:
    """Отдаёт заранее заданные ответы и запоминает пришедшие запросы."""

    def __init__(self) -> None:
        self.routes: dict[str, Callable[[dict[str, list[str]]], tuple[int, str, str]]] = {}
        self.requests: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # --- настройка ответов -------------------------------------------------- #

    def json_route(self, path: str, payload: Any, *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False)
        self.routes[path] = lambda _q: (status, "application/json; charset=utf-8", body)

    def html_route(self, path: str, html: str) -> None:
        self.routes[path] = lambda _q: (200, "text/html; charset=utf-8", html)

    def flaky_json_route(self, path: str, payload: Any, *, fail_times: int, status: int = 503) -> None:
        """Первые fail_times запросов падают, дальше отдаётся payload."""
        state = {"left": fail_times}
        body = json.dumps(payload, ensure_ascii=False)

        def handler(_q):
            if state["left"] > 0:
                state["left"] -= 1
                return status, "text/plain", "temporarily unavailable"
            return 200, "application/json; charset=utf-8", body

        self.routes[path] = handler

    def raw_route(self, path: str, handler) -> None:
        self.routes[path] = handler

    # --- жизненный цикл ----------------------------------------------------- #

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://127.0.0.1:{port}"

    def url(self, path: str) -> str:
        return self.base_url + path

    def hits(self, path: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["path"] == path]

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - имя задано базовым классом
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query, keep_blank_values=True)
                outer.requests.append(
                    {
                        "path": parsed.path,
                        "query": query,
                        "headers": dict(self.headers),
                    }
                )
                route = outer.routes.get(parsed.path)
                if route is None:
                    self.send_error(404)
                    return
                status, content_type, body = route(query)
                raw = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args: Any) -> None:  # тишина в выводе тестов
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


@pytest.fixture
def stub_server():
    server = StubServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()
