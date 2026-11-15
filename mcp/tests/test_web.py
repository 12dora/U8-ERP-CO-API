"""HTTP 方式（Streamable HTTP）和 incoming 令牌：401、按请求令牌列工具、转发 Authorization 和用户头、只读。

连接层（长连接复用、411、并发上限、SIGTERM）的测试在 test_web_conn.py。"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from u8co_mcp.auth import TokenError, with_incoming
from u8co_mcp.config import ConfigError, HttpConfig, parse_config
from u8co_mcp.protocol import Protocol
from u8co_mcp.server import run_http
from u8co_mcp.web import make_server

from tests.support import config_dict, make_app

USER = "3f2b8c1e-5d4a-4b6c-9e7f-0a1b2c3d4e5f"
MGMT_TOOLS = {"u8_mgmt_overview", "u8_mgmt_pnl", "u8_mgmt_sales", "u8_mgmt_arap", "u8_mgmt_cash_stock"}
INCOMING_TOOLS = {"u8_guide"}


def jwt(payload: dict) -> str:
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode("utf-8")).decode("ascii").rstrip("=")

    return part({"alg": "RS256"}) + "." + part(payload) + ".signature"


MGMT_TOKEN = jwt({"sub": USER, "u8co_mgmt": True, "exp": int(time.time()) + 3600})
PROXY_URL = "https://u8a.example.com/v1/bindings/as-person"
PROXY_PATH = "/v1/bindings/as-person"
PROXY_TOKEN = "proxy-service-token-0123456789abcdef"
OVERVIEW = {"name": "u8_mgmt_overview", "arguments": {"fiscal_year": 2026, "period_to": 3, "accounts": ["801"]}}
PLAIN_TOKEN = jwt({"sub": USER, "exp": int(time.time()) + 3600})
_OPENER = build_opener(ProxyHandler({}))


class WebCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        path = self.dir / "proxy-token"
        path.write_text(PROXY_TOKEN + "\n", encoding="utf-8")
        os.chmod(path, 0o600)

    def mgmt_section(self) -> dict:
        proxy = {"url": PROXY_URL, "token_file": str(self.dir / "proxy-token")}
        return {"accounts": [{"acc": "801"}, {"acc": "802"}], "person_proxy": proxy}

    def serve(self, http: dict | None = None, **over):
        over.setdefault("mgmt", self.mgmt_section())
        app, send = make_app(token={"type": "incoming"}, http=http or {}, **over)
        server = make_server(Protocol(app), app.config.http, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = "http://127.0.0.1:%d" % server.server_address[1]
        self.app = app
        return send

    def post(self, message: object, token: str | None = MGMT_TOKEN, path: str = "/mcp", headers: dict | None = None):
        all_headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token is not None:
            all_headers["Authorization"] = "Bearer " + token
        all_headers.update(headers or {})
        request = Request(self.url + path, data=json.dumps(message).encode("utf-8"), headers=all_headers, method="POST")
        return self.open(request)

    def open(self, request: Request):
        try:
            with _OPENER.open(request, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except HTTPError as exc:
            with exc:
                return exc.code, dict(exc.headers), exc.read()

    def rpc(self, method: str, params: dict | None = None, token: str | None = MGMT_TOKEN) -> dict:
        status, _, body = self.post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, token)
        self.assertEqual(status, 200)
        return json.loads(body)

    def tool_names(self, token: str) -> set[str]:
        return {tool["name"] for tool in self.rpc("tools/list", token=token)["result"]["tools"]}


class AuthTests(WebCase):
    def test_missing_token_is_401(self):
        send = self.serve()
        status, headers, body = self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=None)
        self.assertEqual(status, 401)
        self.assertIn("Bearer", headers["WWW-Authenticate"])
        self.assertEqual(json.loads(body)["error"]["code"], -32001)
        status, _, _ = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Authorization": "Basic eA=="})
        self.assertEqual(status, 401)
        self.assertEqual(send.calls, [])

    def test_expired_jwt_is_401(self):
        self.serve()
        expired = jwt({"sub": USER, "u8co_mgmt": True, "exp": int(time.time()) - 10})
        status, headers, _ = self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=expired)
        self.assertEqual(status, 401)
        self.assertIn("invalid_token", headers["WWW-Authenticate"])

    def test_opaque_token_passes_to_api(self):
        self.serve()
        self.assertEqual(self.tool_names("opaque-token-value"), INCOMING_TOOLS)

    def test_origin_check(self):
        self.serve(http={"allowed_origins": ["https://chat.example.com"]})
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.post(ping, headers={"Origin": "https://evil.example.net"})[0], 403)
        self.assertEqual(self.post(ping, headers={"Origin": "https://chat.example.com"})[0], 200)
        self.assertEqual(self.post(ping)[0], 200)

    def test_content_type_and_routes(self):
        self.serve()
        request = Request(
            self.url + "/mcp",
            data=b"{}",
            headers={"Authorization": "Bearer " + MGMT_TOKEN, "Content-Type": "text/plain"},
            method="POST",
        )
        self.assertEqual(self.open(request)[0], 415)
        self.assertEqual(self.open(Request(self.url + "/healthz"))[0], 200)
        self.assertEqual(self.open(Request(self.url + "/mcp"))[0], 405)
        self.assertEqual(self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, path="/other")[0], 404)


class ProtocolTests(WebCase):
    def test_initialize_and_notification(self):
        self.serve()
        result = self.rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        status, _, body = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((status, body), (202, b""))

    def test_listing_follows_request_token(self):
        self.serve()
        self.assertEqual(self.tool_names(MGMT_TOKEN), INCOMING_TOOLS | MGMT_TOOLS)
        self.assertEqual(self.tool_names(PLAIN_TOKEN), INCOMING_TOOLS)
        self.assertEqual(self.tool_names(MGMT_TOKEN), INCOMING_TOOLS | MGMT_TOOLS)

    def test_mgmt_call_refused_without_claim(self):
        send = self.serve()
        params = {"name": "u8_mgmt_overview", "arguments": {"fiscal_year": 2026, "period_to": 3}}
        result = self.rpc("tools/call", params, token=PLAIN_TOKEN)["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "mgmt_forbidden")
        self.assertEqual(send.calls, [])

    def test_incoming_lists_only_guide_and_mgmt(self):
        # u8 段的登录是共用的：incoming 方式下不提供 u8_read 等通用工具，无论 read_only 怎么配。
        for read_only in (True, False):
            with self.subTest(read_only=read_only):
                send = self.serve(read_only=read_only)
                self.assertEqual(self.tool_names(MGMT_TOKEN), INCOMING_TOOLS | MGMT_TOOLS)
                self.assertEqual(self.tool_names(PLAIN_TOKEN), INCOMING_TOOLS)
                for name, args in (
                    ("u8_read", {"route": "health"}),
                    ("u8_write", {"route": "vouchers/create", "body": {}}),
                    ("u8_describe", {}),
                    ("u8_resolve", {"items": [{"archive": "customer", "q": "x"}]}),
                    ("u8_idempotency_get", {"route": "vouchers/create", "key": "k"}),
                ):
                    reply = self.rpc("tools/call", {"name": name, "arguments": args})
                    self.assertEqual(reply["error"]["code"], -32602, name)
                guide = self.rpc("tools/call", {"name": "u8_guide", "arguments": {}})["result"]
                self.assertFalse(guide["isError"])
                self.assertEqual(send.calls, [])

    def test_u8_section_optional(self):
        send = self.serve(u8=None, read_only=True)
        self.assertIsNone(self.app.config.u8)
        send.add("POST", PROXY_PATH, body={"ok": True, "data": {"ok": True}})
        self.assertFalse(self.rpc("tools/call", OVERVIEW)["result"]["isError"])


class ForwardTests(WebCase):
    def test_mgmt_goes_to_person_proxy_with_caller_token(self):
        # 经营管理调用只发给身份绑定服务：服务令牌在 Authorization，调用者令牌原样在 X-U8co-Caller-Token。
        send = self.serve()
        send.add("POST", PROXY_PATH, body={"ok": True, "data": {"ok": True, "rows": []}})
        result = self.rpc("tools/call", OVERVIEW)["result"]
        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"], {"ok": True, "rows": []})
        self.assertEqual(len(send.calls), 1)
        call = send.calls[-1]
        self.assertEqual(call.url, PROXY_URL)
        self.assertEqual(call.headers["Authorization"], "Bearer " + PROXY_TOKEN)
        self.assertEqual(call.headers["X-U8co-Caller-Token"], MGMT_TOKEN)
        other = jwt({"sub": "zhangsan", "u8co_mgmt": True, "exp": int(time.time()) + 600})
        self.rpc("tools/call", OVERVIEW, token=other)
        self.assertEqual(send.calls[-1].headers["X-U8co-Caller-Token"], other)
        self.assertEqual(send.calls[-1].headers["Authorization"], "Bearer " + PROXY_TOKEN)

    def test_proxy_body_has_no_logins(self):
        send = self.serve(http={"user_header": "X-Audit-User"})
        send.add("POST", PROXY_PATH, body={"ok": True, "data": {"ok": True}})
        self.assertFalse(self.rpc("tools/call", OVERVIEW)["result"]["isError"])
        call = send.calls[-1]
        self.assertEqual(
            call.body(),
            {
                "acc": "801",
                "route": "reports/mgmt/overview",
                "body": {"fiscal_year": 2026, "period_from": 3, "period_to": 3},
            },
        )
        self.assertNotIn(b"password", call.data)
        self.assertNotIn(b"logins", call.data)
        self.assertNotIn(b"operator", call.data)

    def test_tokens_not_kept_after_request(self):
        send = self.serve()
        send.add("POST", PROXY_PATH, body={"ok": True, "data": {"ok": True}})
        self.rpc("tools/call", OVERVIEW)
        self.assertEqual(self.app.tokens.secrets(), set())
        with self.assertRaises(TokenError):
            self.app.tokens.token()
        with with_incoming("abc-token"):
            self.assertEqual(self.app.tokens.secrets(), {"abc-token"})


class ConfigTests(unittest.TestCase):
    def test_incoming_and_http_section(self):
        cfg = parse_config(config_dict(token={"type": "incoming"}, http={"port": 9000, "enabled": True}))
        self.assertEqual(cfg.token.type, "incoming")
        self.assertEqual(cfg.http, HttpConfig(enabled=True, port=9000))
        self.assertIsNone(parse_config(config_dict()).http)

    def test_invalid(self):
        bad = [
            ({"token": {"type": "incoming", "path": "/x"}}, "未知的键"),
            ({"http": {"port": 0}}, "http.port"),
            ({"http": {"path": "mcp"}}, "http.path"),
            ({"http": {"user_header": "bad header"}}, "user_header"),
            ({"http": {"allowed_origins": ["*"]}}, "allowed_origins"),
            ({"base_url": "http://u8co-api:8080"}, "allow_insecure_http"),
            ({"allow_insecure_http": "yes"}, "allow_insecure_http"),
        ]
        for over, fragment in bad:
            with self.subTest(over=over), self.assertRaises(ConfigError) as ctx:
                parse_config(config_dict(**over))
            self.assertIn(fragment, str(ctx.exception))

    def test_insecure_http_base_url_only_with_incoming(self):
        incoming = {"type": "incoming"}
        cfg = parse_config(config_dict(base_url="http://u8co-api:8080", allow_insecure_http=True, token=incoming))
        self.assertEqual(cfg.base_url, "http://u8co-api:8080")
        with self.assertRaises(ConfigError) as ctx:
            parse_config(config_dict(base_url="http://u8co-api:8080", allow_insecure_http=True))
        self.assertIn("incoming", str(ctx.exception))
        with self.assertRaises(ConfigError):
            parse_config(config_dict(allow_insecure_http=True))

    def test_u8_section_optional_only_with_incoming(self):
        data = config_dict(token={"type": "incoming"})
        del data["u8"]
        self.assertIsNone(parse_config(data).u8)
        data = config_dict()
        del data["u8"]
        with self.assertRaises(ConfigError) as ctx:
            parse_config(data)
        self.assertIn("缺少 u8", str(ctx.exception))

    def test_example_file_parses(self):
        example = Path(__file__).resolve().parents[1] / "mcp.http.example.json"
        cfg = parse_config(json.loads(example.read_text(encoding="utf-8")))
        self.assertEqual((cfg.token.type, cfg.read_only, cfg.u8), ("incoming", True, None))
        self.assertEqual(cfg.mgmt.accounts_claim, "u8co_accs")
        self.assertIsNotNone(cfg.mgmt.person_proxy)
        self.assertTrue(all(a.password_file is None and not a.operator for a in cfg.mgmt.accounts))

    def test_http_requires_incoming_token(self):
        app, _ = make_app(read_only=True)
        self.assertEqual(run_http(app, "127.0.0.1", 0), 2)

    def test_http_requires_read_only(self):
        app, _ = make_app(token={"type": "incoming"})
        with mock.patch("u8co_mcp.server.serve_http") as serve:
            self.assertEqual(run_http(app, "127.0.0.1", 0), 2)
        serve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
