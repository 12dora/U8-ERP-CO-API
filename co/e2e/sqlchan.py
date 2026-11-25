"""SQL 工人协议。文本守卫与 sqlguard 是同一函数；不连接服务器。

一条通道背后是一个工人进程、一根 stdin，请求和回复按行一问一答，不能多个线程同时用。
并行跑用例时用 SqlPool：每个线程一条通道（各自一个工人，共用同一条 SSH 主连接）。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from co.e2e.remote import RemoteResult
from co.e2e.settings import conf
from co.e2e.sqlguard import sql_block_reason

GATE_SECONDS = 30.0
Legacy = Callable[[], RemoteResult]


class WorkerDown(RuntimeError):
    pass


@dataclass
class WorkerReply:
    ok: bool
    code: int
    out: str


def gate_due(last: float | None, now: float, limit: float = GATE_SECONDS) -> bool:
    if last is None:
        return True
    return now - last >= limit


def server_should_check(age: float | None, force: bool, limit: float = GATE_SECONDS) -> bool:
    # 与 sql-worker.ps1 的 Update-WorkerGate 同一条规则：最多缓存 30 秒。
    if force or age is None:
        return True
    return age >= limit


def encode_request(database: str, query: str, recheck: bool) -> str:
    line = json.dumps(
        {"db": database, "sql": query, "recheck": recheck},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    if "\n" in line or "\r" in line:
        raise ValueError("请求必须是单行")
    return line


def parse_response(line: str) -> WorkerReply:
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError("响应不是对象")
    code = data.get("code")
    out = data.get("out")
    if isinstance(code, bool) or not isinstance(code, int):
        raise ValueError("响应缺少退出码")
    if not isinstance(out, str):
        raise ValueError("响应缺少输出")
    return WorkerReply(data.get("ok") is True, code, out)


class SqlChannel:
    def __init__(self, transport: object, clock: Callable[[], float] | None = None) -> None:
        self.transport = transport
        self.clock = time.monotonic if clock is None else clock
        self.gate_at: float | None = None
        self.dead = False
        # 保险：误把一条通道给了多个线程时，请求排队，不会串行错位。
        self._lock = threading.Lock()

    def execute(self, database: str, query: str, legacy: Legacy) -> RemoteResult:
        blocked = _block_reason(database, query)
        if blocked:
            raise RuntimeError(blocked)
        with self._lock:
            return self._execute(database, query, legacy)

    def _execute(self, database: str, query: str, legacy: Legacy) -> RemoteResult:
        if self.dead or not _alive(self.transport):
            return self._fallback(legacy)
        try:
            return _as_result(self._ask(database, query))
        except (WorkerDown, OSError, ValueError, TimeoutError):
            return self._fallback(legacy)

    def close(self) -> None:
        self.dead = True
        _close_transport(self.transport)

    def _ask(self, database: str, query: str) -> WorkerReply:
        now = self.clock()
        recheck = gate_due(self.gate_at, now)
        raw = _send(self.transport, encode_request(database, query, recheck))
        reply = parse_response(raw)
        if recheck:
            self.gate_at = now
        return reply

    def _fallback(self, legacy: Legacy) -> RemoteResult:
        self.dead = True
        _close_transport(self.transport)
        return legacy()


def _block_reason(database: str, query: str) -> str:
    if database not in conf().databases:
        return "拒绝访问名单以外的数据库"
    return sql_block_reason(query)


def _as_result(reply: WorkerReply) -> RemoteResult:
    return RemoteResult(reply.code, reply.out)


def _alive(transport: object) -> bool:
    alive = getattr(transport, "alive", None)
    if alive is None:
        return False
    return bool(alive())


def _send(transport: object, line: str) -> str:
    send = getattr(transport, "send", None)
    if send is None:
        raise WorkerDown("worker 不可用")
    reply = send(line)
    if not isinstance(reply, str) or reply == "":
        raise WorkerDown("worker 无响应")
    return reply


def _close_transport(transport: object) -> None:
    close = getattr(transport, "close", None)
    if close is None:
        return
    try:
        close()
    except OSError:
        return


class SqlPool:
    """每个线程一条通道，第一次用时才开。建池的线程不从池里拿（它用 Remote 上原有的那一条）。"""

    def __init__(self, opener: Callable[[], SqlChannel]) -> None:
        self.opener = opener
        self.owner = threading.get_ident()
        self._lock = threading.Lock()
        self._by_thread: dict[int, SqlChannel] = {}
        self.closed = False

    def channel(self) -> SqlChannel:
        key = threading.get_ident()
        with self._lock:
            if self.closed:
                raise RuntimeError("SQL 通道池已关闭")
            found = self._by_thread.get(key)
            if found is None:
                found = self.opener()
                self._by_thread[key] = found
            return found

    def size(self) -> int:
        with self._lock:
            return len(self._by_thread)

    def close(self) -> None:
        with self._lock:
            items = list(self._by_thread.values())
            self._by_thread.clear()
            self.closed = True
        for item in items:
            _close_channel(item)


def _close_channel(channel: SqlChannel) -> None:
    try:
        channel.close()
    except (OSError, RuntimeError):
        return
