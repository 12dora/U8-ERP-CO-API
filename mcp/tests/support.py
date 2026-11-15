"""测试共用：假的 HTTP 发送器和配置。"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from u8co_mcp.app import App
from u8co_mcp.config import parse_config
from u8co_mcp.http import Response

BASE = "https://u8co.example.com"
TOKEN_ENV = "U8CO_MCP_TEST_TOKEN"
TOKEN = "test-bearer-token-value"
PASSWORD = "secret-pass-123"


@dataclass
class Call:
    method: str
    url: str
    path: str
    query: dict
    headers: dict
    data: bytes | None

    def body(self) -> object:
        return None if self.data is None else json.loads(self.data.decode("utf-8"))


class FakeSend:
    """按 (方法, 路径) 排队的响应。队列只剩一项时反复用它。项可以是 Response 或要抛的异常。"""

    def __init__(self):
        self.calls: list[Call] = []
        self.queue: dict[tuple[str, str], list] = {}

    def add(self, method: str, path: str, status: int = 200, body: object = None, **extra):
        """extra：headers（响应头字典）、raw（原样响应字节，代替 body）。"""
        raw = extra.get("raw")
        data = raw if raw is not None else json.dumps(body if body is not None else {"ok": True}).encode("utf-8")
        self.queue.setdefault((method, path), []).append(Response(status, data, dict(extra.get("headers") or {})))

    def fail(self, method: str, path: str, exc: Exception):
        self.queue.setdefault((method, path), []).append(exc)

    def __call__(self, method, url, headers, data, timeout):
        parts = urlsplit(url)
        self.calls.append(Call(method, url, parts.path, parse_qs(parts.query), dict(headers), data))
        items = self.queue.get((method, parts.path))
        if not items:
            raise AssertionError(f"没有预设响应：{method} {parts.path}")
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, Exception):
            raise item
        return item


def config_dict(**over) -> dict:
    data = {
        "base_url": BASE,
        "token": {"type": "env", "name": TOKEN_ENV},
        "u8": {"acc": "801", "year": "2026", "operator": "op001"},
    }
    data.update(over)
    return data


def make_app(send: FakeSend | None = None, **over) -> tuple[App, FakeSend]:
    send = send or FakeSend()
    app = App(
        parse_config(config_dict(**over)),
        send,
        today=lambda: dt.date(2026, 6, 1),
        new_key=lambda: "generated-key-1",
    )
    return app, send


def env() -> dict:
    return {TOKEN_ENV: TOKEN, "U8CO_MCP_PASSWORD": PASSWORD}
