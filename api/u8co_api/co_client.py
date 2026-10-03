"""Sync client for the Windows U8 CO bridge. The operator password stays in memory."""

from __future__ import annotations

import http.client
import json
import urllib.parse
from typing import Any

from u8co_api.co_bridge import _AfterSend, to_api_error, transport_error
from u8co_api.co_crypto import auth_headers, body_bytes, derive_keys, encrypt_password, fresh_signed
from u8co_api.config import Settings
from u8co_api.errors import ApiError, bad_request, unavailable

# 成功响应可能是整张单据或一页列表，最多读 8 MiB（与桥的上限一致）。错误响应仍只读 64 KiB。
_OK_LIMIT = 8 * 1024 * 1024
_ERROR_LIMIT = 65536
_CONNECT_TIMEOUT = 5.0
# 长时操作：存货核算记账、期末处理、存货核算期初记账（openings/post module=ia），以及经过存货核算的月末结账（module=ia 或 through）。
# 桥对这些路由等 iaCommandSeconds（缺省 900）+ 60 秒，读取超时用 U8CO_BRIDGE_LONG_TIMEOUT。
LONG_ROUTES = frozenset({"/v1/ia/post", "/v1/ia/period_end"})
PERIODS_CLOSE = "/v1/periods/close"
OPENINGS_POST = "/v1/openings/post"
# 分流桥健康探测的读取超时（秒）。缺省桥的健康检查仍用 U8CO_BRIDGE_TIMEOUT。
PROBE_READ_TIMEOUT = 8.0


def is_long_call(path: str, payload: dict) -> bool:
    if path in LONG_ROUTES:
        return True
    if path == OPENINGS_POST:
        return payload.get("module") == "ia"
    return path == PERIODS_CLOSE and (payload.get("module") == "ia" or payload.get("through") is True)


class CoBridge:
    def __init__(self, base_url: str, secret_hex: str, timeout: float = 90, long_timeout: float = 1000) -> None:
        self._parts = _base(base_url)
        self._timeout = timeout
        self._long_timeout = max(long_timeout, timeout)
        self._k_mac, self._k_enc = derive_keys(secret_hex)

    def health(self) -> dict[str, Any]:
        return self._health(None)

    def probe(self) -> dict[str, Any]:
        """分流桥的健康探测：读取超时 PROBE_READ_TIMEOUT 秒（建连仍是 5 秒），卡住的测试桥拖不住健康检查。"""
        return self._health(PROBE_READ_TIMEOUT)

    def _health(self, timeout: float | None) -> dict[str, Any]:
        # 健康检查没有写操作。任何失败都是 503，不用 outcome_unknown。
        try:
            return self._roundtrip("GET", "/v1/health", None, signed=False, timeout=timeout)
        except ApiError as exc:
            if exc.status == 503:
                raise
            raise unavailable(exc.message) from exc

    def meta(self) -> dict[str, Any]:
        # 字段元数据：要签名，但不带账套和口令，桥不登录 U8。请求体固定为 {}。
        return self._roundtrip("POST", "/v1/meta", body_bytes({}), signed=True)

    def call(self, path: str, payload: dict) -> dict[str, Any]:
        password = payload.get("password")
        if not isinstance(password, str):
            raise bad_request("缺少操作员口令")
        sealed = _sealed(payload, encrypt_password(self._k_enc, password))
        timeout = self._long_timeout if is_long_call(path, payload) else self._timeout
        return self._roundtrip("POST", path, body_bytes(sealed), signed=True, timeout=timeout)

    def _roundtrip(
        self, method: str, suffix: str, body: bytes | None, signed: bool, timeout: float | None = None
    ) -> dict[str, Any]:
        path = "/u8co" + (suffix if suffix.startswith("/") else "/" + suffix)
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if signed:
            headers.update(auth_headers(self._k_mac, fresh_signed(method, path, body or b"")))
        status, raw = self._send(method, path, body, headers, timeout or self._timeout)
        return _finish(status, raw)

    def _send(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        # 建连最多 5 秒。连上之后才按读取超时等桥：桥自己大约等 75 秒，长时操作等 iaCommandSeconds + 60 秒。
        conn = _connection(self._parts)
        sent = False
        try:
            conn.connect()
            _arm_read(conn, timeout)
            sent = True
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, response.read(_limit(response.status) + 1)
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            failure = _AfterSend() if sent else exc
            raise transport_error(failure) from exc
        finally:
            conn.close()


def build_bridge(settings: Settings) -> CoBridge | None:
    if not settings.enabled or not settings.configured:
        return None
    return CoBridge(settings.bridge_url, settings.bridge_secret, settings.bridge_timeout, settings.bridge_long_timeout)


DEFAULT_ROUTE = "default"


def route_name(index: int) -> str:
    """分流桥在审计行和健康检查里的名字，序号与 U8CO_BRIDGE_ROUTES_FILE 里 routes 的下标一致。"""
    return f"routes[{index}]"


class BridgeRoutes:
    """按账套分流的桥。entries 按分流文件的顺序，每项是（路由名，账套号，桥）。为空时所有账套走缺省桥。"""

    def __init__(self, entries: tuple[tuple[str, tuple[str, ...], Any], ...] = ()) -> None:
        self.entries = entries
        self._by_acc = {acc: (name, bridge) for name, accounts, bridge in entries for acc in accounts}

    def pick(self, acc: str) -> tuple[str, Any] | None:
        return self._by_acc.get(acc)


def build_routes(settings: Settings) -> BridgeRoutes:
    # 缺省桥没配好时整个 CO 接口都是 503，分流桥也不建。每个分流桥各自一套密钥派生，超时不写就沿用缺省桥的。
    if not settings.enabled or not settings.configured:
        return BridgeRoutes()
    entries = []
    for index, route in enumerate(settings.bridge_routes):
        timeout = route.timeout or settings.bridge_timeout
        bridge = CoBridge(route.url, route.secret, timeout, max(settings.bridge_long_timeout, timeout))
        entries.append((route_name(index), route.accounts, bridge))
    return BridgeRoutes(tuple(entries))


def _base(base_url: str) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(base_url.rstrip("/"))
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("base_url 必须以 http:// 或 https:// 开头并带主机名")
    if parts.path != "/u8co" or parts.query or parts.fragment:
        raise ValueError("base_url 的路径必须是 /u8co，不能带查询串")
    return parts


def _connection(parts: urllib.parse.SplitResult) -> http.client.HTTPConnection:
    # http.client 不走 http_proxy，也不会改写 X-U8co-* 的大小写。
    port = _port(parts)
    host = parts.hostname or ""
    if parts.scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=_CONNECT_TIMEOUT)
    return http.client.HTTPConnection(host, port, timeout=_CONNECT_TIMEOUT)


def _port(parts: urllib.parse.SplitResult) -> int:
    try:
        found = parts.port
    except ValueError as exc:
        raise unavailable("CO 桥地址的端口无效") from exc
    if found is None:
        return 443 if parts.scheme == "https" else 80
    if found < 1 or found > 65535:
        raise unavailable("CO 桥地址的端口无效")
    return found


def _arm_read(conn: http.client.HTTPConnection, timeout: float) -> None:
    sock = conn.sock
    if sock is not None:
        sock.settimeout(timeout)


def _sealed(payload: dict, password_enc: str) -> dict[str, object]:
    sealed: dict[str, object] = {}
    for key, value in payload.items():
        if key == "password":
            sealed["password_enc"] = password_enc
            continue
        sealed[key] = value
    return sealed


def _finish(status: int, raw: bytes) -> dict[str, Any]:
    parsed = _object(status, raw)
    if parsed is not None and status == 200 and parsed.get("ok") is True:
        return parsed
    raise to_api_error(status, parsed)


def _object(status: int, raw: bytes) -> dict[str, Any] | None:
    if len(raw) > _limit(status) or _redirect(status):
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _limit(status: int) -> int:
    if 200 <= status < 300:
        return _OK_LIMIT
    return _ERROR_LIMIT


def _redirect(status: int) -> bool:
    return status >= 300 and status < 400
