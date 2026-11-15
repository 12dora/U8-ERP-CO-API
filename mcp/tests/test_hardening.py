"""评审第一轮的加固：dry_run 严格布尔、写入遇网关错误页、脱敏、ca_file、代理、协议健壮性。"""

from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock
from urllib.request import ProxyHandler

from u8co_mcp.app import App
from u8co_mcp.config import ConfigError, parse_config
from u8co_mcp.http import UrllibTransport, make_transport
from u8co_mcp.protocol import Protocol
from u8co_mcp.server import serve

from tests.support import PASSWORD, TOKEN, config_dict, env, make_app


class Case(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, env())
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, app, args: dict) -> dict:
        return app.call("u8_write", dict({"route": "vouchers/create"}, **args))["structuredContent"]


class DryRunStrictTests(Case):
    def test_non_boolean_body_dry_run_refused(self):
        for value in ("true", 1, 0, None, "false"):
            app, send = make_app()
            out = self.write(app, {"body": {"dry_run": value}})
            self.assertEqual(out["error"]["code"], "bad_arguments", repr(value))
            self.assertEqual(out["error"]["field"], "body.dry_run")
            self.assertEqual(send.calls, [], repr(value))

    def test_non_boolean_argument_refused(self):
        for value in ("true", 1, 0, None):
            app, send = make_app()
            out = self.write(app, {"body": {}, "dry_run": value})
            self.assertEqual(out["error"]["code"], "bad_arguments", repr(value))
            self.assertEqual(send.calls, [])

    def test_conflicting_dry_run_refused(self):
        for arg, body in ((True, False), (False, True)):
            app, send = make_app()
            out = self.write(app, {"body": {"dry_run": body}, "dry_run": arg})
            self.assertEqual(out["error"]["code"], "bad_arguments")
            self.assertEqual(send.calls, [])

    def test_agreeing_values(self):
        app, send = make_app()
        send.add("POST", "/v1/co/vouchers/create")
        self.write(app, {"body": {"dry_run": True}, "dry_run": True})
        self.assertTrue(send.calls[0].body()["dry_run"])
        self.assertNotIn("Idempotency-Key", send.calls[0].headers)
        self.write(app, {"body": {"dry_run": False}, "dry_run": False})
        self.assertNotIn("dry_run", send.calls[1].body())
        self.assertIn("Idempotency-Key", send.calls[1].headers)


class GatewayTests(Case):
    def test_write_gateway_page_is_outcome_unknown(self):
        for status in (502, 503, 504):
            app, send = make_app()
            send.add("POST", "/v1/co/vouchers/create", status=status, raw=b"<html>proxy error</html>")
            out = self.write(app, {"body": {}})
            self.assertEqual(out["status"], status)
            self.assertEqual(out["error"]["code"], "outcome_unknown")
            self.assertFalse(out["error"]["retryable"])
            self.assertIn("u8_idempotency_get", out["error"]["hint"])
            self.assertEqual(out["idempotency_key"], "generated-key-1")

    def test_write_api_format_error_passes_through(self):
        app, send = make_app()
        error = {"code": "u8_license_full", "message": "许可已满", "retryable": True}
        send.add("POST", "/v1/co/vouchers/verify", status=503, body={"error": error})
        out = app.call("u8_write", {"route": "vouchers/verify", "body": {}})["structuredContent"]
        self.assertEqual(out["error"], error)

    def test_dry_run_and_read_stay_retryable(self):
        app, send = make_app()
        send.add("POST", "/v1/co/vouchers/create", status=503, raw=b"<html></html>")
        out = self.write(app, {"body": {}, "dry_run": True})
        self.assertEqual((out["error"]["code"], out["error"]["retryable"]), ("bad_response", True))
        send.add("POST", "/v1/co/vouchers/load", status=502, raw=b"<html></html>")
        out = app.call("u8_read", {"route": "vouchers/load", "body": {}})["structuredContent"]
        self.assertEqual((out["error"]["code"], out["error"]["retryable"]), ("bad_response", True))


class ScrubTests(Case):
    def test_api_error_text_scrubbed(self):
        app, send = make_app()
        error = {"code": "bad_request", "message": f"口令 {PASSWORD} 不对", "hint": f"令牌 {TOKEN}", "retryable": False}
        send.add("POST", "/v1/co/vouchers/load", status=400, body={"error": error})
        result = app.call("u8_read", {"route": "vouchers/load", "body": {}})
        text = result["content"][0]["text"]
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(TOKEN, text)
        self.assertEqual(result["structuredContent"]["error"]["message"], "口令 *** 不对")
        self.assertEqual(result["structuredContent"]["error"]["code"], "bad_request")


class CaFileTests(Case):
    def test_bad_ca_file_is_config_error(self):
        app = App(parse_config(config_dict(ca_file="/nonexistent/u8co-ca.pem")))
        self.assertNotIn("u8_write", [t["name"] for t in app.list_tools()])
        out = app.call("u8_read", {"route": "health"})["structuredContent"]
        self.assertEqual(out["error"]["code"], "config_error")
        self.assertIn("ca_file", out["error"]["message"])

    def test_make_transport_raises_config_error(self):
        with self.assertRaises(ConfigError):
            make_transport("/nonexistent/u8co-ca.pem")


class ProxyTests(unittest.TestCase):
    def test_env_proxies_ignored(self):
        proxies = {"HTTPS_PROXY": "http://203.0.113.9:3128", "HTTP_PROXY": "http://203.0.113.9:3128",
                   "https_proxy": "http://203.0.113.9:3128", "http_proxy": "http://203.0.113.9:3128"}
        with mock.patch.dict(os.environ, proxies):
            transport = UrllibTransport()
        handlers = transport._opener.handlers
        # ProxyHandler({}) 没有 *_open 方法，不会挂进 opener；
        # 关键是没有带代理地址的处理器（环境变量的缺省 ProxyHandler 也被替换掉了）。
        self.assertFalse([h for h in handlers if isinstance(h, ProxyHandler) and h.proxies])


class ProtocolRobustTests(Case):
    def setUp(self):
        super().setUp()
        self.proto = Protocol(make_app()[0])

    def test_deep_nesting_is_parse_error(self):
        line = "[" * 200000 + "]" * 200000
        reply = json.loads(self.proto.handle_line(line))
        self.assertEqual(reply["error"]["code"], -32700)

    def test_bad_id_type(self):
        reply = json.loads(self.proto.handle_line(json.dumps({"jsonrpc": "2.0", "id": {"a": 1}, "method": "ping"})))
        self.assertEqual((reply["id"], reply["error"]["code"]), (None, -32600))

    def test_oversized_line_rejected_and_serving_continues(self):
        ping = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        big = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"x": "y" * 500}})
        stdin = io.BytesIO((big + "\n" + ping + "\n").encode("utf-8"))
        stdout = io.BytesIO()
        self.assertEqual(serve(self.proto, stdin, stdout, max_line=200), 0)
        replies = [json.loads(x) for x in stdout.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1], {"jsonrpc": "2.0", "id": 7, "result": {}})

    def test_oversized_last_line_without_newline(self):
        stdout = io.BytesIO()
        self.assertEqual(serve(self.proto, io.BytesIO(b"x" * 1000), stdout, max_line=100), 0)
        self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], -32700)

    def test_handler_crash_keeps_serving(self):
        ping = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        stdin = io.BytesIO(("{}\n" + ping + "\n").encode("utf-8"))
        stdout = io.BytesIO()
        with mock.patch.object(Protocol, "handle", side_effect=[RuntimeError("x"), {"jsonrpc": "2.0", "id": 2}]):
            with self.assertLogs("u8co_mcp", level="ERROR"):
                serve(self.proto, stdin, stdout)
        replies = [json.loads(x) for x in stdout.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(replies[0]["error"]["code"], -32603)
        self.assertEqual(replies[1]["id"], 2)


if __name__ == "__main__":
    unittest.main()
