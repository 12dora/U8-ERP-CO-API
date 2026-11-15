"""进程内的滑动窗口频率限制和在途并发上限。"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from contextlib import contextmanager

from u8co_api.errors import rate_limited

# 超的是并发上限（或算不出窗口）时，Retry-After 给这个秒数。
RETRY_DEFAULT = 5


class Limit:
    """按键计数。键按调用方客户端区分（可能每个用户一个），空闲的键要清掉，否则进程里越积越多。"""

    def __init__(self, rpm: int, concurrency: int, window: float = 60.0, clock=None) -> None:
        self.rpm = rpm
        self.concurrency = concurrency
        self.window = window
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}
        self._inflight: dict[str, int] = {}
        self._swept = self._clock()

    def acquire(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            self._sweep(now)
            hits = self._hits.get(key) or deque()
            self._expire(hits, now)
            inflight = self._inflight.get(key, 0)
            if len(hits) >= self.rpm or inflight >= self.concurrency:
                return False
            hits.append(now)
            self._hits[key] = hits
            self._inflight[key] = inflight + 1
            return True

    def release(self, key: str) -> None:
        with self._lock:
            left = self._inflight.get(key, 0) - 1
            if left > 0:
                self._inflight[key] = left
            else:
                self._inflight.pop(key, None)

    def retry_after(self, key: str) -> int:
        """被拒后建议的重试秒数：频率超限时是窗口里最早一次请求滑出窗口的时间，否则 RETRY_DEFAULT。"""
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits:
                self._expire(hits, now)
            if not hits or len(hits) < self.rpm:
                return RETRY_DEFAULT
            return max(1, math.ceil(hits[0] + self.window - now))

    def _expire(self, hits: deque[float], now: float) -> None:
        while hits and now - hits[0] >= self.window:
            hits.popleft()

    # 每过一个窗口扫一遍：窗口内没有请求、也没有在途请求的键删掉。
    def _sweep(self, now: float) -> None:
        if now - self._swept < self.window:
            return
        self._swept = now
        for key in list(self._hits):
            hits = self._hits[key]
            self._expire(hits, now)
            if not hits and key not in self._inflight:
                del self._hits[key]

    @contextmanager
    def slot(self, key: str):
        if not self.acquire(key):
            error = rate_limited()
            error.retry_after = self.retry_after(key)
            raise error
        try:
            yield
        finally:
            self.release(key)
