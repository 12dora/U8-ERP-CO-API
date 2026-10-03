"""Bearer 令牌来源。

client_credentials：向 token_url 用表单换令牌，缓存到过期前 60 秒；接口返回 401 时作废缓存重取一次。
file：每次调用重新读文件（外部工具负责刷新）。env：每次调用读环境变量。
incoming：HTTP 方式下取当前请求的 Bearer 令牌（见 with_incoming），不缓存、不刷新，也不记进脱敏集合
（多人共用一个进程，令牌只在本次请求内有效）。
"""

from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlencode

from u8co_mcp.config import TokenConfig, read_secret_file
from u8co_mcp.http import Send, TransportError
from u8co_mcp.mgmt import token_claims

_SKEW_S = 60
_DEFAULT_TTL_S = 300
_OAUTH_CODE = re.compile(r"^[a-z_]{1,40}\Z")
_UUID = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_INCOMING: ContextVar[str] = ContextVar("u8co_mcp_incoming_token", default="")


@contextmanager
def with_incoming(token: str):
    """在这个上下文里，incoming 令牌取 token（HTTP 方式每个请求进一次）。"""
    mark = _INCOMING.set(token)
    try:
        yield
    finally:
        _INCOMING.reset(mark)


def token_user(token: str) -> str:
    """令牌（JWT）的 sub 是 UUID 时返回它，否则空串。只用于审计头，不验签。"""
    sub = token_claims(token).get("sub")
    return sub if isinstance(sub, str) and _UUID.match(sub) else ""


class TokenError(Exception):
    """拿不到令牌。消息不含密钥。"""


class TokenProvider:
    def __init__(self, cfg: TokenConfig, send: Send, timeout: float, clock=time.monotonic):
        self.cfg = cfg
        self.send = send
        self.timeout = timeout
        self.clock = clock
        self._token = ""
        self._until = 0.0
        self._seen: set[str] = set()

    @property
    def refreshable(self) -> bool:
        return self.cfg.type == "client_credentials"

    @property
    def incoming(self) -> bool:
        return self.cfg.type == "incoming"

    def secrets(self) -> set[str]:
        """用过的令牌和客户端密钥（incoming 方式为当前请求的令牌），供错误文本脱敏。"""
        found = set(self._seen)
        if self.incoming and _INCOMING.get():
            found.add(_INCOMING.get())
        return found

    def user(self) -> str:
        """incoming 方式下当前请求令牌的 sub（UUID），转给 API 记审计；其余方式是机器身份，返回空串。"""
        return token_user(_INCOMING.get()) if self.incoming else ""

    def invalidate(self) -> None:
        self._token = ""
        self._until = 0.0

    def token(self) -> str:
        if self.incoming:
            value = _INCOMING.get()
            if not value:
                raise TokenError("请求没有带 Bearer 令牌")
            return value
        if self.cfg.type == "file":
            value = read_secret_file(self.cfg.path, "令牌")
        elif self.cfg.type == "env":
            value = os.environ.get(self.cfg.name, "").strip()
            if not value:
                raise TokenError(f"环境变量 {self.cfg.name} 里没有令牌")
        else:
            value = self._client_credentials()
        self._seen.add(value)
        return value

    def _client_credentials(self) -> str:
        if self._token and self.clock() < self._until:
            return self._token
        secret = read_secret_file(self.cfg.client_secret_file, "客户端密钥")
        self._seen.add(secret)
        form = {"grant_type": "client_credentials", "client_id": self.cfg.client_id, "client_secret": secret}
        if self.cfg.scope:
            form["scope"] = self.cfg.scope
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        try:
            resp = self.send("POST", self.cfg.token_url, headers, urlencode(form).encode("ascii"), self.timeout)
        except TransportError as exc:
            raise TokenError(f"令牌端点不可达：{exc}") from None
        payload = resp.json()
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if resp.status != 200 or not isinstance(token, str) or not token:
            raise TokenError(_token_failure(resp.status, payload))
        self._token = token
        self._until = self.clock() + max(0, _ttl(payload) - _SKEW_S)
        return token


def _ttl(payload: dict) -> float:
    value = payload.get("expires_in")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return _DEFAULT_TTL_S
    return float(value)


def _token_failure(status: int, payload: object) -> str:
    # 只带 OAuth 标准错误码（如 invalid_client），不带 error_description，免得回显别的内容。
    code = payload.get("error") if isinstance(payload, dict) else None
    suffix = f"（{code}）" if isinstance(code, str) and _OAUTH_CODE.match(code) else ""
    if status == 200:
        return "令牌端点的响应里没有 access_token"
    return f"令牌端点返回 HTTP {status}{suffix}"
