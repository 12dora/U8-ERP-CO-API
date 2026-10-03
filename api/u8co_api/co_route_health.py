"""/v1/co/health 里分流桥（U8CO_BRIDGE_ROUTES_FILE）的探测：并行、短超时、总时限，一个卡住的桥拖不住健康检查。"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from u8co_api.errors import ApiError

# 所有分流桥合计最多等这么久；单个桥的读取超时见 co_client.PROBE_READ_TIMEOUT（建连 5 秒）。
PROBE_DEADLINE = 10.0


class RouteProbes:
    """构造时就把各分流桥的探测并行发出去，调用方同时探测缺省桥；collect 按总时限收结果。"""

    def __init__(self, entries: tuple[tuple[str, tuple[str, ...], Any], ...]) -> None:
        self._deadline = time.monotonic() + PROBE_DEADLINE
        self._pool = ThreadPoolExecutor(max_workers=len(entries), thread_name_prefix="u8co-probe")
        self._pending: list[tuple[str, tuple[str, ...], Future]] = [
            (name, accounts, self._pool.submit(bridge.probe)) for name, accounts, bridge in entries
        ]

    def collect(self) -> list[dict]:
        return [self._one(name, accounts, future) for name, accounts, future in self._pending]

    def close(self) -> None:
        # 不等还没回来的探测：它们最迟在各自的读取超时后自行结束。
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _one(self, name: str, accounts: tuple[str, ...], future: Future) -> dict:
        item: dict = {"route": name, "accounts": list(accounts)}
        try:
            reply = future.result(timeout=max(0.0, self._deadline - time.monotonic()))
        except ApiError as exc:
            item.update(ok=False, error=exc.code)
            return item
        except Exception:
            # 含超过总时限（TimeoutError）和传输异常。
            item.update(ok=False, error="unavailable")
            return item
        version = reply.get("version") if isinstance(reply, dict) else None
        ok = isinstance(reply, dict) and reply.get("ok") is True
        item.update(ok=ok, version=version if isinstance(version, str) else None)
        # 桥的写入策略状态原样带上（形状由 CoRouteHealth 校验，不认的丢掉）。
        policy = reply.get("write_policy") if isinstance(reply, dict) else None
        if isinstance(policy, dict):
            item["write_policy"] = policy
        # 桥的第二级写入总开关（enableReplicatedWrites）；旧版桥不报则省略。
        replicated = reply.get("replicated_writes") if isinstance(reply, dict) else None
        if isinstance(replicated, bool):
            item["replicated_writes"] = replicated
        return item
