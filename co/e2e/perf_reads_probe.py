"""读路由性能测量：逐次计时、记录状态码和响应字节数，按 next 翻页。perf_reads 用，单元测试可换假客户端。"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from co.client.u8co_client import U8CoClient, U8CoError

# 单次翻页的页数上限：最大的档案按 500 一页也只有二三十页，到上限说明游标没有前进。
PAGE_CAP = 400
_MESSAGE_MAX = 200


class MeteredClient(U8CoClient):
    """记下最近一次请求的 HTTP 状态码和响应体字节数，其余行为与 U8CoClient 相同。"""

    last_status: int = 0
    last_bytes: int | None = None

    def _exchange(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        self.last_status, self.last_bytes = 0, None
        status, raw = super()._exchange(method, path, body, headers, timeout)
        self.last_status, self.last_bytes = status, len(raw)
        return status, raw


@dataclass
class CallRecord:
    group: str
    route: str
    params: str
    ok: bool
    status: int
    code: str
    ms: float
    items: int | None
    bytes: int | None
    message: str = ""


@dataclass
class Probe:
    """顺序执行并计时。异常只记录，不中断；消息里的口令和密钥换成 ***。"""

    client: Any
    needles: list[str] = field(default_factory=list)
    clock: Callable[[], float] = time.perf_counter
    group: str = ""
    records: list[CallRecord] = field(default_factory=list)

    def call(self, route: str, params: str, fn: Callable[[], dict]) -> dict | None:
        self._reset()
        start = self.clock()
        try:
            payload = fn()
        except U8CoError as exc:
            self._add(route, params, start, None, exc)
            return None
        except ValueError as exc:
            self._add(route, params, start, None, U8CoError(0, "client_invalid", str(exc)))
            return None
        self._add(route, params, start, payload, None)
        return payload

    def _reset(self) -> None:
        if hasattr(self.client, "last_bytes"):
            self.client.last_bytes = None
            self.client.last_status = 0

    def _add(self, route: str, params: str, start: float, payload: dict | None, error: U8CoError | None) -> None:
        ms = round((self.clock() - start) * 1000.0, 1)
        size = getattr(self.client, "last_bytes", None)
        status = getattr(self.client, "last_status", 0) or 0
        if error is None:
            if size is None:
                size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            record = CallRecord(self.group, route, params, True, status or 200, "", ms, item_count(payload), size)
        else:
            text = self._scrub(error.message or "")[:_MESSAGE_MAX]
            record = CallRecord(self.group, route, params, False, error.status or status, error.code, ms, None, size, text)
        self.records.append(record)

    def _scrub(self, text: str) -> str:
        for needle in self.needles:
            if needle:
                text = text.replace(needle, "***")
        return text

    def dump(self) -> list[dict]:
        return [asdict(item) for item in self.records]


def item_count(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    items = payload.get("items")
    return len(items) if isinstance(items, list) else None


def walk(probe: Probe, route: str, label: Callable[[Any], str], page: Callable[[Any], dict]) -> list[dict]:
    """从 after 缺省开始按 next 读到没有下一页；出错、游标不前进或超过 PAGE_CAP 页即停。返回成功的各页。"""
    pages: list[dict] = []
    after: Any = None
    seen: set = set()
    for _ in range(PAGE_CAP):
        cursor = after
        payload = probe.call(route, label(cursor), lambda: page(cursor))
        if payload is None:
            break
        pages.append(payload)
        nxt = payload.get("next")
        if nxt is None or nxt == "" or nxt in seen:
            break
        seen.add(nxt)
        after = nxt
    return pages


def params_text(**values: object) -> str:
    # 记录里的参数摘要：值为 None、空串、False 的省略，True 只写键名。
    parts = []
    for key, value in values.items():
        if value is None or value == "" or value is False:
            continue
        parts.append(key if value is True else f"{key}={value}")
    return " ".join(parts)


def group_totals(records: list[CallRecord]) -> dict[str, dict]:
    totals: dict[str, dict] = {}
    for item in records:
        row = totals.setdefault(item.group, {"calls": 0, "failed": 0, "ms_total": 0.0, "ms_max": 0.0, "items": 0, "bytes": 0})
        row["calls"] += 1
        row["failed"] += 0 if item.ok else 1
        row["ms_total"] = round(row["ms_total"] + item.ms, 1)
        row["ms_max"] = max(row["ms_max"], item.ms)
        row["items"] += item.items or 0
        row["bytes"] += item.bytes or 0
    return totals
