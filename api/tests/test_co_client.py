"""CO bridge client against a local fake. Never calls 192.0.2.10."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.parse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from tests.support import settings
from u8co_api.co_client import CoBridge, _arm_read, _connection, build_bridge
from u8co_api.co_crypto import SignedRequest, derive_keys, sign
from u8co_api.errors import (
    ApiError,
    bad_gateway,
    bad_request,
    conflict,
    forbidden,
    rate_limited,
    timeout_error,
    unavailable,
    unprocessable,
)
from u8co_api.http import _HTTP

SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
PASSWORD = "测试Pass#01"

_CASES = (
    (400, "bad_request", 400, "bad_request"),
    (401, "unauthorized", 503, "unavailable"),
    (403, "account_not_allowed", 403, "account_not_allowed"),
    (404, "not_found", 404, "not_found"),
    (409, "state_mismatch", 409, "state_mismatch"),
    (409, "u8_rejected", 409, "u8_rejected"),
    (409, "workflow_enabled", 409, "workflow_enabled"),
    (409, "workflow_unknown", 409, "workflow_unknown"),
    (409, "workflow_paused", 409, "workflow_paused"),
    (422, "login_failed", 422, "login_failed"),
    (429, "busy", 429, "busy"),
    (500, "internal", 502, "internal"),
    (503, "com_unavailable", 503, "com_unavailable"),
    (503, "busy_timeout", 503, "busy_timeout"),
    (503, "stopping", 503, "stopping"),
    (504, "outcome_unknown", 504, "outcome_unknown"),
    (409, "not_submitted", 409, "not_submitted"),
    (409, "not_current_approver", 409, "not_current_approver"),
    (409, "already_submitted", 409, "already_submitted"),
    (409, "stock_shortage", 409, "stock_shortage"),
    (200, "not_submitted", 409, "not_submitted"),
    (200, "future_code", 502, "future_code"),
    (500, "workflow_disabled", 409, "workflow_disabled"),
    (500, "bad_response", 502, "bad_response"),
    (500, "workflow_paused", 409, "workflow_paused"),
)


class _Capture:
    def __init__(self) -> None:
        self.method = ""
        self.path = ""
        self.headers: dict[str, str] = {}
        self.body = b""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def _serve(self) -> None:
        server = self.server
        assert isinstance(server, _Bridge)
        length = int(self.headers.get("Content-Length", "0"))
        server.capture.method = self.command
        server.capture.path = self.path
        server.capture.headers = {key: value for key, value in self.headers.items()}
        server.capture.body = self.rfile.read(length) if length else b""
        payload = server.payload
        self.send_response(server.reply_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _fmt: str, *_args: object) -> None:
        return None


class _Bridge(ThreadingHTTPServer):
    def __init__(self, status: int, payload: bytes) -> None:
        self.capture = _Capture()
        self.reply_status = status
        self.payload = payload
        super().__init__(("127.0.0.1", 0), _Handler)


class _Running:
    def __init__(self, status: int, payload: bytes) -> None:
        self.httpd = _Bridge(status, payload)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _Running:
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def base_url(self) -> str:
        port = self.httpd.server_address[1]
        return f"http://127.0.0.1:{port}/u8co"


def _header(headers: dict[str, str], name: str) -> str | None:
    folded = name.lower()
    for key, value in headers.items():
        if key.lower() == folded:
            return value
    return None


def _fields() -> dict[str, str]:
    return {
        "acc": "803",
        "year": "2026",
        "operator": "op001",
        "password": PASSWORD,
        "date": "2026-09-26",
    }


def _assert_sig(capture: _Capture) -> None:
    k_mac, _k_enc = derive_keys(SECRET)
    ts = _header(capture.headers, "X-U8co-Ts") or ""
    nonce = _header(capture.headers, "X-U8co-Nonce") or ""
    req = SignedRequest(capture.method, capture.path, capture.body, ts, nonce)
    assert _header(capture.headers, "X-U8co-Sig") == sign(k_mac, req)


def test_health_has_no_signature() -> None:
    with _Running(200, b'{"ok":true,"version":"1"}') as bridge:
        payload = CoBridge(bridge.base_url, SECRET, timeout=5).health()
    assert payload["version"] == "1"
    assert bridge.httpd.capture.method == "GET"
    assert bridge.httpd.capture.path == "/u8co/v1/health"
    assert _header(bridge.httpd.capture.headers, "X-U8co-Sig") is None


def test_call_signs_raw_body_and_hides_password() -> None:
    body = '{"ok":true,"operator":"op001","operator_name":"张三"}'.encode()
    payload = _fields()
    with _Running(200, body) as bridge:
        result = CoBridge(bridge.base_url, SECRET, timeout=5).call("/v1/login-check", payload)
    assert result["operator_name"] == "张三"
    assert payload == _fields()
    assert "password_enc" not in payload
    captured = bridge.httpd.capture
    assert PASSWORD.encode() not in captured.body
    assert b'"password"' not in captured.body
    assert b"password_enc" in captured.body
    assert captured.path == "/u8co/v1/login-check"
    assert _header(captured.headers, "Content-Type") == "application/json; charset=utf-8"
    _assert_sig(captured)


@pytest.mark.parametrize(("status", "code", "expect_status", "expect_code"), _CASES)
def test_bridge_errors(status: int, code: str, expect_status: int, expect_code: str) -> None:
    raw = json.dumps({"ok": False, "code": code, "message": "说明"}, ensure_ascii=False).encode()
    payload = _fields()
    with _Running(status, raw) as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).call("/v1/login-check", payload)
    err = caught.value
    assert err.status == expect_status
    assert err.code == expect_code
    if status == 401:
        assert err.message == "CO 桥认证失败"
    else:
        assert err.message == "说明"
    assert SECRET not in err.message and PASSWORD not in err.message
    assert payload["password"] == PASSWORD
    sent = bridge.httpd.capture.body
    assert PASSWORD.encode() not in sent
    assert b'"password"' not in sent
    assert b"password_enc" in sent


def test_bridge_500_without_code_is_bad_gateway() -> None:
    with _Running(500, b'{"ok":false}') as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).call("/v1/login-check", _fields())
    assert caught.value.status == 502
    assert caught.value.code == "internal"
    assert caught.value.message == "CO 桥返回错误"


def test_garbled_health_is_unavailable() -> None:
    with _Running(200, b"\xff") as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).health()
    assert caught.value.status == 503
    assert caught.value.code == "unavailable"
    assert caught.value.message == "CO 桥响应无法解析"


def test_garbled_success_is_outcome_unknown() -> None:
    with _Running(200, b"\xff") as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).call("/v1/login-check", _fields())
    assert caught.value.status == 504
    assert caught.value.code == "outcome_unknown"
    assert caught.value.message == "CO 桥响应无法解析"


def test_plain_error_is_bad_response() -> None:
    with _Running(400, b"nope") as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).call("/v1/login-check", _fields())
    assert caught.value.status == 502
    assert caught.value.code == "bad_response"


def test_health_plain_error_is_unavailable() -> None:
    with _Running(500, b"nope") as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).health()
    assert caught.value.status == 503
    assert caught.value.code == "unavailable"


def test_oversized_success_is_outcome_unknown() -> None:
    with _Running(200, json.dumps({"ok": True, "pad": "x" * 8 * 1024 * 1024}).encode()) as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).call("/v1/login-check", _fields())
    assert caught.value.status == 504
    assert caught.value.code == "outcome_unknown"


def test_oversized_error_is_bad_response() -> None:
    with _Running(500, b"x" * 70000) as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).call("/v1/login-check", _fields())
    assert caught.value.status == 502
    assert caught.value.code == "bad_response"


def test_redirect_is_not_followed() -> None:
    with _Running(302, b"") as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).health()
    assert caught.value.status == 503
    assert caught.value.code == "unavailable"
    assert bridge.httpd.capture.path == "/u8co/v1/health"


def test_redirected_call_is_bad_response() -> None:
    with _Running(302, b"") as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).call("/v1/login-check", _fields())
    assert caught.value.status == 502
    assert caught.value.code == "bad_response"
    assert bridge.httpd.capture.path == "/u8co/v1/login-check"


def test_missing_password_is_local() -> None:
    client = CoBridge("http://127.0.0.1:1/u8co", SECRET, timeout=2)
    with pytest.raises(ApiError) as caught:
        client.call("/v1/login-check", {"acc": "803"})
    assert caught.value.status == 400
    assert caught.value.code == "bad_request"
    assert PASSWORD not in caught.value.message


def test_connect_refused() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    client = CoBridge(f"http://127.0.0.1:{port}/u8co", SECRET, timeout=2)
    with pytest.raises(ApiError) as caught:
        client.health()
    assert caught.value.status == 503
    assert caught.value.code == "unavailable"
    assert caught.value.message == "连不上 CO 桥"
    assert SECRET not in caught.value.message


@contextmanager
def _hanging():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    def _hold() -> None:
        try:
            conn, _addr = sock.accept()
        except OSError:
            return
        time.sleep(1.5)
        conn.close()

    thread = threading.Thread(target=_hold, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        sock.close()


def test_health_timeout_is_unavailable() -> None:
    with _hanging() as port:
        client = CoBridge(f"http://127.0.0.1:{port}/u8co", SECRET, timeout=0.4)
        with pytest.raises(ApiError) as caught:
            client.health()
    assert caught.value.status == 503
    assert caught.value.code == "unavailable"
    assert caught.value.message == "已送出请求但没有收到结果"


def test_call_timeout_is_outcome_unknown() -> None:
    with _hanging() as port:
        client = CoBridge(f"http://127.0.0.1:{port}/u8co", SECRET, timeout=0.4)
        with pytest.raises(ApiError) as caught:
            client.call("/v1/login-check", _fields())
    assert caught.value.status == 504
    assert caught.value.code == "outcome_unknown"
    assert caught.value.message == "已送出请求但没有收到结果"


def test_proxy_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    with _Running(200, b'{"ok":true,"version":"1"}') as bridge:
        payload = CoBridge(bridge.base_url, SECRET, timeout=5).health()
    assert payload["ok"] is True


def test_settings_default_leaves_co_off() -> None:
    loaded = settings()
    assert loaded.enabled is True
    assert loaded.configured is False
    assert loaded.bridge_url == ""
    assert loaded.bridge_timeout == 90
    assert loaded.concurrency == 8
    assert loaded.caller_concurrency == 4
    assert loaded.rpm == 30
    assert loaded.accounts == ()
    assert build_bridge(loaded) is None


def test_build_bridge_when_configured() -> None:
    loaded = settings(bridge_secret=SECRET, bridge_url="http://127.0.0.1:9/u8co/")
    assert loaded.configured is True
    assert isinstance(build_bridge(loaded), CoBridge)
    assert SECRET not in repr(loaded)
    assert build_bridge(settings(enabled=False, bridge_secret=SECRET, bridge_url="http://127.0.0.1:9/u8co")) is None


def test_bad_secret_stays_unconfigured() -> None:
    loaded = settings(bridge_secret=SECRET.upper(), bridge_url="http://127.0.0.1:9/u8co")
    assert loaded.configured is False
    assert build_bridge(loaded) is None
    assert SECRET.upper() not in repr(loaded)


def test_bad_port_is_unavailable() -> None:
    client = CoBridge("http://127.0.0.1:99999/u8co", SECRET, timeout=80)
    with pytest.raises(ApiError) as caught:
        client.health()
    assert caught.value.status == 503
    assert caught.value.code == "unavailable"


def test_read_timeout_follows_connect() -> None:
    with _Running(200, b'{"ok":true,"version":"1"}') as bridge:
        parts = urllib.parse.urlsplit(bridge.base_url)
        conn = _connection(parts)
        try:
            assert conn.timeout == 5
            conn.connect()
            assert conn.sock is not None
            _arm_read(conn, 12.5)
            assert conn.sock.gettimeout() == 12.5
        finally:
            conn.close()


def test_error_helpers_keep_codes() -> None:
    assert bad_request("账套号无效").code == "bad_request"
    assert bad_request("账套号无效").status == 400
    assert unavailable().code == "unavailable"
    assert unavailable().message == "服务暂时不可用"
    assert timeout_error().code == "timeout"
    assert timeout_error("已送出", "outcome_unknown").code == "outcome_unknown"
    assert conflict("已审核", "state_mismatch").status == 409
    assert conflict("已审核", "state_mismatch").code == "state_mismatch"
    assert unprocessable("口令错误", "login_failed").status == 422
    assert bad_gateway("内部错误", "internal").status == 502
    assert bad_gateway("内部错误", "internal").code == "internal"
    assert forbidden("无权").code == "forbidden"
    assert rate_limited().code == "rate_limited"
    assert _HTTP[409] == ("conflict", "请求与当前状态冲突")
    assert _HTTP[422] == ("unprocessable", "请求无法处理")
    assert _HTTP[502] == ("bad_gateway", "上游服务错误")
