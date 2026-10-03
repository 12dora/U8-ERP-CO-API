"""HTTP：urllib 发送器（可替换，测试注入假的）和 /v1/co 调用。

发送器签名：send(method, url, headers, data, timeout) -> Response；网络层失败抛 TransportError。
不跟随重定向（3xx 原样返回），HTTPS 始终校验证书（可另给 ca_file）。
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
from dataclasses import dataclass, field
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from u8co_mcp import __version__
from u8co_mcp.config import ConfigError

MAX_BODY = 16 * 1024 * 1024
USER_AGENT = f"u8co-mcp/{__version__}"


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> object:
        """解析 JSON；解析不了返回 None。"""
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


class TransportError(Exception):
    """网络层失败。sent=False 表示请求肯定没有送到服务端（连接被拒、域名解析失败、证书错误）。"""

    def __init__(self, message: str, sent: bool):
        super().__init__(message)
        self.sent = sent


Send = Callable[[str, str, dict, "bytes | None", float], Response]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # 返回 None：不跟随，3xx 作为 HTTPError 抛出
        return None


class UrllibTransport:
    def __init__(self, ca_file=None):
        context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
        # ProxyHandler({})：不走系统代理（HTTPS_PROXY 等），令牌和 U8 口令只发给配置的地址。
        self._opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect())

    def __call__(self, method: str, url: str, headers: dict, data: bytes | None, timeout: float) -> Response:
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout) as resp:
                return Response(resp.status, _read(resp), _headers(resp.headers))
        except HTTPError as exc:
            try:
                return Response(exc.code, _read(exc), _headers(exc.headers))
            finally:
                exc.close()
        except URLError as exc:
            raise TransportError(_reason(exc.reason), sent=_maybe_sent(exc.reason)) from None
        except (TimeoutError, socket.timeout):
            raise TransportError("请求超时", sent=True) from None
        except (OSError, http.client.HTTPException) as exc:
            raise TransportError(f"连接中断（{type(exc).__name__}）", sent=True) from None


def make_transport(ca_file=None) -> UrllibTransport:
    """ca_file 不存在或不是证书时抛 ConfigError，不让进程崩掉。"""
    try:
        return UrllibTransport(ca_file)
    except (OSError, ValueError, ssl.SSLError):
        raise ConfigError(f"ca_file {ca_file} 不可用（找不到或不是 PEM 证书）") from None


def _read(resp) -> bytes:
    body = resp.read(MAX_BODY + 1)
    if len(body) > MAX_BODY:
        raise TransportError("响应超过 16 MiB", sent=True)
    return body


def _headers(headers) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _maybe_sent(reason: object) -> bool:
    if isinstance(reason, (ConnectionRefusedError, socket.gaierror, ssl.SSLError, str)):
        return False
    return True


def _reason(reason: object) -> str:
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "请求超时"
    if isinstance(reason, ConnectionRefusedError):
        return "连接被拒绝"
    if isinstance(reason, socket.gaierror):
        return "域名解析失败"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "TLS 证书校验失败"
    if isinstance(reason, ssl.SSLError):
        return "TLS 握手失败"
    return f"网络错误（{type(reason).__name__}）"


def scrub(text: str, secrets) -> str:
    """把已知密钥（口令、令牌、客户端密钥）从错误文本里换成 ***。太短的不换，免得误伤。"""
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    return text


# 长时操作：存货核算记账、期末处理，以及经过存货核算的月末结账（module=ia 或 through）。桥对它们等
# iaCommandSeconds（缺省 900）+ 60 秒，所以用配置的 long_timeout_s，其余调用用 timeout_s。
LONG_PATHS = frozenset({"/v1/co/ia/post", "/v1/co/ia/period_end"})
PERIODS_CLOSE = "/v1/co/periods/close"


def is_long_call(path: str, body: object) -> bool:
    if path in LONG_PATHS:
        return True
    if path != PERIODS_CLOSE or not isinstance(body, dict):
        return False
    return body.get("module") == "ia" or body.get("through") is True


class Api:
    """带 Bearer 令牌调 {base_url}/v1/...。令牌 401 时换一次新令牌重试（只对可刷新的令牌）。

    令牌来源给出终端用户（incoming 方式下令牌 sub 为 UUID）时，另带 user_header 头，API 只记审计。"""

    user_header = "X-U8co-User"

    def __init__(self, base_url: str, timeout: float, send: Send, tokens, long_timeout: float | None = None):
        self.base_url = base_url
        self.timeout = timeout
        self.long_timeout = max(long_timeout or timeout, timeout)
        self.send = send
        self.tokens = tokens

    def request(self, method: str, path: str, body=None, query=None, headers=None) -> Response:
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        timeout = self.long_timeout if is_long_call(path, body) else self.timeout
        resp = self._once(method, url, data, headers, timeout)
        if resp.status == 401 and self.tokens.refreshable:
            self.tokens.invalidate()
            resp = self._once(method, url, data, headers, timeout)
        return resp

    def _once(self, method: str, url: str, data: bytes | None, extra: dict | None, timeout: float) -> Response:
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": "Bearer " + self.tokens.token(),
        }
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        user = self.tokens.user()
        if user:
            headers[self.user_header] = user
        if extra:
            headers.update(extra)
        return self.send(method, url, headers, data, timeout)
