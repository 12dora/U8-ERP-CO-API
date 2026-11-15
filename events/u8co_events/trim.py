"""按最慢的消费组裁剪 stream。

只在 stream 超过 redis.maxlen 时裁，而且只裁「所有消费组都已确认」的事件（XTRIM MINID ~）：
- 每个消费组的下限：有待确认（已投递未 XACK）的取其中最小的 ID，否则取 last-delivered-id；
- 取所有消费组下限里最小的那个作 MINID，小于它的才删；未读、未确认的一条不删。
裁完仍超过 maxlen，说明有消费组落后，记警告并在健康检查里显示，不再多删。
stream 上没有任何消费组时无从知道谁读过，按 MAXLEN ~ maxlen 裁，并记警告日志（文档要求消费方用消费组）。
裁剪失败只记日志，不影响发布。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

log = logging.getLogger("u8co_events")

# 同一个 stream 两次裁剪检查的最短间隔（秒）。
TRIM_EVERY = 60.0


def parse_id(text: str) -> tuple[int, int]:
    ms, _, seq = str(text).partition("-")
    return int(ms), int(seq or 0)


def group_floor(client: Any, stream: str, group: dict[str, Any]) -> str:
    """一个消费组还需要的最小 ID（含）。"""
    if int(group.get("pending") or 0) > 0:
        info = client.xpending(stream, group["name"])
        return str(info["min"])
    return str(group.get("last-delivered-id") or "0-0")


def safe_min_id(client: Any, stream: str) -> str | None:
    """所有消费组都还需要的最小 ID；没有消费组时返回 None。"""
    groups = client.xinfo_groups(stream)
    if not groups:
        return None
    return min((group_floor(client, stream, g) for g in groups), key=parse_id)


class Trimmer:
    def __init__(self, client: Any, maxlen: int, clock: Callable[[], float] | None = None) -> None:
        self._client = client
        self._maxlen = maxlen
        self._clock = clock or time.monotonic
        self._last: dict[str, float] = {}
        self._warnings: dict[str, str] = {}

    def warnings(self) -> list[str]:
        return [self._warnings[k] for k in sorted(self._warnings)]

    def maybe_trim(self, streams: Iterable[str]) -> None:
        now = self._clock()
        for stream in streams:
            last = self._last.get(stream)
            if last is not None and now - last < TRIM_EVERY:
                continue
            self._last[stream] = now
            try:
                self.trim(stream)
            except Exception as exc:  # 裁剪只是清理，失败不影响发布
                log.warning("裁剪 %s 失败: %s: %s", stream, type(exc).__name__, exc)

    def trim(self, stream: str) -> int:
        """裁一次，返回删掉的条数。"""
        length = int(self._client.xlen(stream))
        if length <= self._maxlen:
            self._warnings.pop(stream, None)
            return 0
        floor = safe_min_id(self._client, stream)
        if floor is None:
            removed = int(self._client.xtrim(stream, maxlen=self._maxlen, approximate=True) or 0)
            if removed:
                log.warning(
                    "%s 没有消费组，按 maxlen=%d 裁掉 %d 条（无法确认是否有人读过）", stream, self._maxlen, removed
                )
            self._warnings.pop(stream, None)
            return removed
        removed = int(self._client.xtrim(stream, minid=floor, approximate=True) or 0)
        left = length - removed
        if left > self._maxlen:
            text = (
                f"{stream} 有 {left} 条，超过 maxlen={self._maxlen}："
                f"有消费组没读完（最早未确认 {floor}），未读事件不裁剪"
            )
            if self._warnings.get(stream) is None:
                log.warning("%s", text)
            self._warnings[stream] = text
        else:
            self._warnings.pop(stream, None)
        return removed
