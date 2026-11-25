"""中断闸门。并行跑用例时桥客户端包一层：收到中断（Run.stop 置位）后不再发新的桥调用。

engine 在清理（停服务、还原、再启动服务）之前也会置位，所以即使有用例线程没在时限内退出，
它也发不出桥调用，不会在还原之后把数据写进测试账套。已经发出去的那一次调用在停服务、还原之前结束或失败。
"""

from __future__ import annotations

import threading
from typing import Any

from co.client.u8co_client import U8CoError

STOP_CODE = "interrupted"
STOP_TEXT = "收到中断，不再发桥调用"


def stopped(ctx: object) -> bool:
    flag = getattr(ctx, "stop", None)
    return isinstance(flag, threading.Event) and flag.is_set()


class GatedClient:
    """转发到真正的 U8CoClient；每次调用前看一眼 stop。"""

    def __init__(self, client: Any, stop: threading.Event) -> None:
        self._client = client
        self._stop = stop

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._client, name)
        if not callable(value):
            return value
        stop = self._stop

        def gated(*args: Any, **kwargs: Any) -> Any:
            if stop.is_set():
                raise U8CoError(0, STOP_CODE, STOP_TEXT)
            return value(*args, **kwargs)

        return gated
