"""字段元数据 meta。要签名，不带账套和口令，请求体固定为 {}；桥不登录 U8。"""

from __future__ import annotations

from typing import Any


class U8CoMetaMixin:
    def _call(self, method: str, suffix: str, body: bytes | None, signed: bool) -> dict[str, Any]:
        raise NotImplementedError

    def meta(self) -> dict[str, Any]:
        return self._call("POST", "/v1/meta", b"{}", signed=True)
