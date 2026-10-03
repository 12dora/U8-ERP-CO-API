"""健康检查与状态：每种单据的延迟、删除扫描、出箱积压、线程是否活着。

HTTP 只读、只绑配置里的地址（缺省 127.0.0.1:8090）：GET /healthz（健康 200，否则 503）、GET /status（总是 200）。
正文都是同一份 JSON。状态库用只读连接打开（readonly.py），健康检查不写库。
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Protocol

from u8co_events.config import Config
from u8co_events.readonly import ReadOnlyState
from u8co_events.state import TypeStatus

log = logging.getLogger("u8co_events")

# 类型多久没有成功提交算「停滞」：至少 10 分钟，或 10 个轮询周期。
_STALE_MIN_SECONDS = 600.0
_STALE_POLLS = 10
# 删除扫描多久没有完成算停滞：3 个扫描周期（且不短于上面的停滞时间）。
_STALE_SCANS = 3
# 水位倒退后报不健康的时长。
_RESET_ALERT_SECONDS = 24 * 3600.0

Probe = Callable[[], list[str]]


class StateView(Protocol):
    def status(self, pairs: Collection[tuple[str, str]] | None = None) -> list[TypeStatus]: ...

    def outbox_size(self) -> int: ...


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        got = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return got if got.tzinfo else got.replace(tzinfo=UTC)


def _age(ts: str | None, now: datetime) -> float | None:
    got = _parse(ts)
    return None if got is None else max(0.0, (now - got).total_seconds())


def type_row(st: TypeStatus, now: datetime) -> dict[str, Any]:
    return {
        "account": st.account,
        "type": st.type,
        "watermark": st.watermark,
        "snapshots": st.snapshots,
        "last_poll_at": st.last_poll_at,
        "last_ok_at": st.last_ok_at,
        "last_scan_at": st.last_scan_at,
        "lag_seconds": _age(st.last_ok_at, now),
        "scan_age_seconds": _age(st.last_scan_at, now),
        "last_error": st.last_error,
        "scan_error": st.scan_error,
        "watermark_reset_at": st.watermark_reset_at,
        "watermark_reset": st.watermark_reset,
    }


def _reset_problems(rows: list[dict[str, Any]], now: datetime) -> list[str]:
    """水位倒退（库被还原等）后 24 小时内报不健康，提醒人工核对；之后只在 types 里留记录。"""
    found: list[str] = []
    for row in rows:
        age = _age(row["watermark_reset_at"], now)
        if age is not None and age <= _RESET_ALERT_SECONDS:
            found.append(
                f"{row['account']}/{row['type']} 在 {row['watermark_reset_at']} 发现水位倒退"
                f"（{row['watermark_reset']}），已整轮重新对比；请确认数据库是否被还原"
            )
    return found


class Monitor:
    """汇总状态。threads / checks / notes 在 run 模式下由 main 传入；status 命令下为空。

    checks 返回的是问题（报不健康），notes 返回的是警告（照常 200，写进 warnings）。
    只看配置里的（账套, 类型），配置里去掉的类型留下的旧行不算。
    """

    def __init__(
        self,
        cfg: Config,
        threads: Mapping[str, threading.Thread] | None = None,
        checks: Sequence[Probe] = (),
        notes: Sequence[Probe] = (),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._cfg = cfg
        self._threads = dict(threads or {})
        self._checks = tuple(checks)
        self._notes = tuple(notes)
        self._now = now or (lambda: datetime.now(UTC))
        self._started = self._now()
        # 含附加数据源的类型行（ar_process、gl_voucher、archive:customer……），同样看延迟、扫描、水位倒退。
        self._pairs = set(cfg.pairs())

    def stale_after(self) -> float:
        return max(_STALE_MIN_SECONDS, self._cfg.poll_interval_seconds * _STALE_POLLS)

    def scan_stale_after(self) -> float:
        """0 表示不扫描、不检查。"""
        minutes = self._cfg.delete_scan_minutes
        return 0.0 if minutes <= 0 else max(self.stale_after(), minutes * 60 * _STALE_SCANS)

    def report(self, state: StateView) -> dict[str, Any]:
        now = self._now()
        rows = [type_row(st, now) for st in state.status(self._pairs)]
        size = state.outbox_size()
        problems = self._problems(rows, now)
        return {
            "ok": not problems,
            "problems": problems,
            "warnings": self._warnings(rows),
            "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "outbox_size": size,
            "outbox_high_water": self._cfg.outbox_high_water,
            "types": rows,
        }

    def _problems(self, rows: list[dict[str, Any]], now: datetime) -> list[str]:
        problems = [f"线程 {name} 已退出" for name, th in self._threads.items() if not th.is_alive()]
        for check in self._checks:
            problems.extend(check())
        problems.extend(
            f"{row['account']}/{row['type']} 删除扫描失败：{row['scan_error']}" for row in rows if row["scan_error"]
        )
        problems.extend(_reset_problems(rows, now))
        if (now - self._started).total_seconds() >= self.stale_after():
            problems.extend(self._stale(rows))
        return problems

    def _stale(self, rows: list[dict[str, Any]]) -> list[str]:
        found: list[str] = []
        poll_limit = self.stale_after()
        scan_limit = self.scan_stale_after()
        for row in rows:
            name = f"{row['account']}/{row['type']}"
            lag = row["lag_seconds"]
            if lag is None or lag > poll_limit:
                found.append(f"{name} 超过 {poll_limit:.0f} 秒没有成功轮询")
            scan_age = row["scan_age_seconds"]
            if scan_limit and (scan_age is None or scan_age > scan_limit):
                found.append(f"{name} 超过 {scan_limit:.0f} 秒没有完成删除扫描")
        return found

    def _warnings(self, rows: list[dict[str, Any]]) -> list[str]:
        found: list[str] = []
        for note in self._notes:
            found.extend(note())
        return found


class _Server(HTTPServer):
    monitor: Monitor
    state_path: str


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:  # noqa: N802  http.server 约定的方法名
        if self.path not in ("/healthz", "/status"):
            self._send(404, {"error": "not found"})
            return
        try:
            # 每个请求开一个只读连接，用完就关（SQLite 连接不跨线程）。
            with closing(ReadOnlyState(self.server.state_path)) as state:
                body = self.server.monitor.report(state)
        except Exception as exc:
            self._send(503, {"ok": False, "problems": [f"读状态失败: {type(exc).__name__}"]})
            return
        code = 200 if body["ok"] or self.path == "/status" else 503
        self._send(code, body)

    def _send(self, code: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002  覆盖基类签名
        log.debug("health: " + format, *args)


def split_listen(listen: str) -> tuple[str, int]:
    host, _, port = listen.rpartition(":")
    return host.strip("[]"), int(port)


def start_server(listen: str, monitor: Monitor, state_path: str) -> _Server:
    """在后台线程起 HTTP 服务，返回 server（调用方 shutdown）。"""
    host, port = split_listen(listen)
    server = _Server((host, port), _Handler)
    server.monitor = monitor
    server.state_path = state_path
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    return server


def stop_server(server: _Server) -> None:
    server.shutdown()
    server.server_close()


def probe(listen: str, timeout: float = 5.0) -> int:
    """给容器 HEALTHCHECK 用：/healthz 返回 200 则 0，否则 1。"""
    if not listen:
        return 0
    host, port = split_listen(listen)
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=timeout) as resp:
            return 0 if resp.status == 200 else 1
    except (urllib.error.URLError, OSError):
        return 1
