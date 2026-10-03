"""出箱发布循环：按 seq 顺序取一批 → 发布 → 删掉这批。

至少一次：只有发布器返回（Redis 已落盘）之后才 mark_sent。在两者之间断电或崩溃，重启后这批会重发，
消费方按 event_id 去重。发布失败时整批留在出箱里，按指数退避重试，不跳过、不打乱顺序。

一条发不出去的事件会挡住后面所有事件（按顺序投递的代价）。连续失败几次后改为一次只发出箱首条，
日志里写出它的 event_id；再多失败几次就在健康检查里报出来。永远不自动丢弃或跳过。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from u8co_events.publisher import Publisher
from u8co_events.state import OutboxRow, Store

log = logging.getLogger("u8co_events")

_BACKOFF_START = 1.0
_BACKOFF_MAX = 60.0
# 首条连续失败这么多次后，一次只发一条，把出问题的那条单独暴露出来。
ISOLATE_AFTER = 3
# 首条连续失败这么多次后，健康检查报不健康。
ALERT_AFTER = 5
# 发布线程这么久没有成功一轮，健康检查报不健康。
STALE_SECONDS = 300.0


class OutboxLoop:
    def __init__(
        self,
        store: Store,
        publisher: Publisher,
        batch: int,
        idle_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._batch = batch
        self._idle = idle_seconds
        self._clock = clock or time.monotonic
        self.published = 0
        self.failures = 0
        self.last_beat = self._clock()
        self.last_error: str | None = None
        # 出箱首条（最早未发出的那条）连续失败的情况。
        self.head_seq: int | None = None
        self.head_event: str | None = None
        self.head_failures = 0

    def drain_once(self) -> int:
        """发布一批，返回条数（0 表示出箱空）。发布或 mark_sent 失败时抛异常，出箱不变。"""
        limit = 1 if self.head_failures >= ISOLATE_AFTER else self._batch
        rows = self._store.pending_outbox(limit)
        if rows:
            try:
                self._publisher.publish(rows)
                self._store.mark_sent([row.seq for row in rows])
            except Exception:
                self._head_failed(rows[0], len(rows))
                raise
            self.published += len(rows)
        self.head_seq, self.head_event, self.head_failures = None, None, 0
        self.last_beat = self._clock()
        self.last_error = None
        return len(rows)

    def _head_failed(self, head: OutboxRow, count: int) -> None:
        if head.seq == self.head_seq:
            self.head_failures += 1
        else:
            self.head_seq, self.head_event, self.head_failures = head.seq, head.event_id, 1
        if count == 1 and self.head_failures >= ISOLATE_AFTER:
            log.error(
                "事件 %s（账套 %s %s id=%s %s）已连续发布失败 %d 次；出箱被它挡住，不会跳过",
                head.event_id,
                head.account,
                head.type,
                head.doc_id,
                head.kind,
                self.head_failures,
            )

    def drain_all(self) -> int:
        """一直发到出箱空。任何失败都向上抛。"""
        total = 0
        while True:
            n = self.drain_once()
            total += n
            if n == 0:
                return total

    def problems(self) -> list[str]:
        """给健康检查：发布卡住的情况。"""
        found: list[str] = []
        idle = self._clock() - self.last_beat
        if idle > STALE_SECONDS:
            found.append(f"发布线程 {idle:.0f} 秒没有成功一轮")
        if self.head_failures >= ALERT_AFTER:
            found.append(
                f"出箱首条事件 {self.head_event}（seq {self.head_seq}）已连续发布失败 {self.head_failures} 次："
                f"{self.last_error}"
            )
        return found

    def run_forever(self, stop: threading.Event) -> None:
        backoff = _BACKOFF_START
        while not stop.is_set():
            try:
                n = self.drain_once()
            except Exception as exc:
                self.failures += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "发布失败（首条 %s，连续第 %d 次），%.0f 秒后重试: %s",
                    self.head_event,
                    self.head_failures,
                    backoff,
                    self.last_error,
                )
                stop.wait(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
                continue
            backoff = _BACKOFF_START
            if n < self._batch:
                stop.wait(self._idle)
