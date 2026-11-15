"""工具执行：凭据注入、请求形状、幂等键、错误映射、脱敏。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from u8co_mcp.app import App
from u8co_mcp.http import TransportError

from tests.support import BASE, PASSWORD, TOKEN, env, make_app


class AppCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, env(), clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)


class ReadTests(AppCase):
    def test_read_injects_credentials_and_compact(self):
        app, send = make_app()
        send.add("POST", "/v1/co/vouchers/load", body={"ok": True, "head": {"ccode": "0001"}})
        result = app.call("u8_read", {"route": "vouchers/load", "body": {"type": "sale_order", "id": 5}})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], {"ok": True, "head": {"ccode": "0001"}})
        self.assertEqual(result["content"][0]["type"], "text")
        call = send.calls[0]
        self.assertEqual(call.url.split("?")[0], BASE + "/v1/co/vouchers/load")
        self.assertEqual(call.headers["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(call.query, {"compact": ["true"]})
        self.assertEqual(
            call.body(),
            {
                "type": "sale_order",
                "id": 5,
                "acc": "801",
                "operator": "op001",
                "password": PASSWORD,
                "year": "2026",
                "date": "2026-06-01",
            },
        )

    def test_read_fields_and_no_compact(self):
        app, send = make_app()
        send.add("POST", "/v1/co/vouchers/list")
        app.call("u8_read", {"route": "vouchers/list", "body": {}, "fields": "head.ccode", "compact": False})
        self.assertEqual(send.calls[0].query, {"fields": ["head.ccode"]})

    def test_body_may_override_year_and_date(self):
        app, send = make_app()
        send.add("POST", "/v1/co/reports/gl_balance")
        app.call("u8_read", {"route": "reports/gl_balance", "body": {"year": "2025", "date": "2025-01-31"}})
        body = send.calls[0].body()
        self.assertEqual((body["year"], body["date"]), ("2025", "2025-01-31"))

    def test_health_is_get_without_body(self):
        app, send = make_app()
        send.add("GET", "/v1/co/health", body={"ok": True})
        result = app.call("u8_read", {"route": "health"})
        self.assertFalse(result["isError"])
        self.assertEqual(send.calls[0].method, "GET")
        self.assertIsNone(send.calls[0].data)

    def test_get_route_refuses_body(self):
        app, send = make_app()
        result = app.call("u8_read", {"route": "meta", "body": {"x": 1}})
        self.assertTrue(result["isError"])
        self.assertEqual(send.calls, [])

    def test_body_injection_refused(self):
        app, send = make_app()
        for key in ("acc", "operator", "password", "password_enc", "Password"):
            result = app.call("u8_read", {"route": "vouchers/load", "body": {key: "x"}})
            self.assertTrue(result["isError"], key)
            self.assertEqual(result["structuredContent"]["error"]["code"], "bad_arguments")
            self.assertEqual(result["structuredContent"]["status"], 0)
        self.assertEqual(send.calls, [])

    def test_write_route_not_allowed_in_read(self):
        app, send = make_app()
        result = app.call("u8_read", {"route": "vouchers/create", "body": {}})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "bad_arguments")
        self.assertEqual(send.calls, [])

    def test_unknown_argument_refused(self):
        app, _ = make_app()
        result = app.call("u8_read", {"route": "vouchers/load", "extra": 1})
        self.assertTrue(result["isError"])

    def test_resolve_body(self):
        app, send = make_app()
        send.add("POST", "/v1/co/archives/resolve", body={"ok": True, "results": []})
        items = [{"archive": "customer", "q": "张三贸易"}]
        result = app.call("u8_resolve", {"items": items, "limit": 3})
        self.assertFalse(result["isError"])
        body = send.calls[0].body()
        self.assertEqual(body["items"], items)
        self.assertEqual(body["limit"], 3)
        self.assertNotIn("include_disabled", body)
        self.assertEqual(send.calls[0].query, {})

    def test_resolve_limits(self):
        app, send = make_app()
        result = app.call("u8_resolve", {"items": []})
        self.assertTrue(result["isError"])
        result = app.call("u8_resolve", {"items": [{"archive": "customer", "q": ""}]})
        self.assertTrue(result["isError"])
        self.assertEqual(send.calls, [])

    def test_idempotency_get_body(self):
        app, send = make_app()
        send.add("POST", "/v1/co/idempotency/get", body={"ok": True, "found": False})
        app.call("u8_idempotency_get", {"route": "vouchers/create", "key": "k-1"})
        body = send.calls[0].body()
        self.assertEqual((body["path"], body["key"]), ("/v1/co/vouchers/create", "k-1"))
        self.assertEqual(body["acc"], "801")
        result = app.call("u8_idempotency_get", {"route": "gl/vouchers/verify", "key": "k-2"})
        self.assertFalse(result["isError"])
        self.assertEqual(send.calls[1].body()["path"], "/v1/co/gl/vouchers/verify")
        self.assertTrue(app.call("u8_idempotency_get", {"route": "vouchers/load", "key": "k"})["isError"])


class WriteTests(AppCase):
    def test_generates_idempotency_key(self):
        app, send = make_app()
        send.add("POST", "/v1/co/vouchers/create", body={"ok": True, "id": 7})
        result = app.call("u8_write", {"route": "vouchers/create", "body": {"type": "sale_order"}})
        self.assertFalse(result["isError"])
        self.assertEqual(send.calls[0].headers["Idempotency-Key"], "generated-key-1")
        self.assertEqual(result["structuredContent"], {"ok": True, "id": 7, "idempotency_key": "generated-key-1"})
        self.assertNotIn("dry_run", send.calls[0].body())

    def test_given_key_used(self):
        app, send = make_app()
        send.add("POST", "/v1/co/archives/create")
        result = app.call("u8_write", {"route": "archives/create", "body": {}, "idempotency_key": "my-key"})
        self.assertEqual(send.calls[0].headers["Idempotency-Key"], "my-key")
        self.assertEqual(result["structuredContent"]["idempotency_key"], "my-key")

    def test_every_write_route_gets_key(self):
        # 所有写路由都支持幂等键，正式写入自动生成。
        app, send = make_app()
        send.add("POST", "/v1/co/vouchers/verify")
        result = app.call("u8_write", {"route": "vouchers/verify", "body": {"type": "sale_order", "id": 1}})
        self.assertEqual(send.calls[0].headers["Idempotency-Key"], "generated-key-1")
        self.assertEqual(result["structuredContent"]["idempotency_key"], "generated-key-1")

    def test_given_key_on_any_write_route(self):
        app, send = make_app()
        send.add("POST", "/v1/co/workflow/approve")
        result = app.call("u8_write", {"route": "workflow/approve", "body": {}, "idempotency_key": "k"})
        self.assertFalse(result["isError"])
        self.assertEqual(send.calls[0].headers["Idempotency-Key"], "k")

    def test_auto_writeoff_plan_mode_has_no_key(self):
        app, send = make_app()
        send.add("POST", "/v1/co/arap/writeoff/auto", body={"ok": True, "mode": "plan"})
        result = app.call("u8_write", {"route": "arap/writeoff/auto", "body": {"dry_run": True}})
        self.assertNotIn("Idempotency-Key", send.calls[0].headers)
        self.assertTrue(send.calls[0].body()["dry_run"])
        self.assertNotIn("idempotency_key", result["structuredContent"])
        args = {"route": "arap/writeoff/auto", "body": {"dry_run": True}, "idempotency_key": "k"}
        self.assertTrue(app.call("u8_write", args)["isError"])
        self.assertEqual(len(send.calls), 1)

    def test_auto_writeoff_real_run_gets_key(self):
        app, send = make_app()
        send.add("POST", "/v1/co/arap/writeoff/auto")
        result = app.call("u8_write", {"route": "arap/writeoff/auto", "body": {"dry_run": False}})
        self.assertEqual(send.calls[0].headers["Idempotency-Key"], "generated-key-1")
        self.assertNotIn("dry_run", send.calls[0].body())
        self.assertEqual(result["structuredContent"]["idempotency_key"], "generated-key-1")

    def test_bad_key_format_refused(self):
        app, send = make_app()
        result = app.call("u8_write", {"route": "vouchers/create", "body": {}, "idempotency_key": "a b"})
        self.assertTrue(result["isError"])
        self.assertEqual(send.calls, [])

    def test_dry_run_sends_flag_without_key(self):
        app, send = make_app()
        send.add("POST", "/v1/co/vouchers/create", body={"ok": True, "dry_run": True, "mode": "rollback"})
        result = app.call("u8_write", {"route": "vouchers/create", "body": {"type": "sale_order"}, "dry_run": True})
        self.assertFalse(result["isError"])
        self.assertTrue(send.calls[0].body()["dry_run"])
        self.assertNotIn("Idempotency-Key", send.calls[0].headers)
        self.assertNotIn("idempotency_key", result["structuredContent"])

    def test_dry_run_in_body_counts(self):
        app, send = make_app()
        send.add("POST", "/v1/co/vouchers/create")
        app.call("u8_write", {"route": "vouchers/create", "body": {"dry_run": True}})
        self.assertNotIn("Idempotency-Key", send.calls[0].headers)
        self.assertTrue(send.calls[0].body()["dry_run"])

    def test_dry_run_with_key_refused(self):
        app, send = make_app()
        args = {"route": "vouchers/create", "body": {}, "dry_run": True, "idempotency_key": "k"}
        self.assertTrue(app.call("u8_write", args)["isError"])
        self.assertEqual(send.calls, [])

    def test_read_only_hides_and_refuses_write(self):
        app, send = make_app(read_only=True)
        self.assertNotIn("u8_write", [t["name"] for t in app.list_tools()])
        result = app.call("u8_write", {"route": "vouchers/create", "body": {}})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "read_only")
        self.assertEqual(send.calls, [])


class ErrorTests(AppCase):
    def test_http_error_maps_to_is_error(self):
        app, send = make_app()
        error = {"code": "state_mismatch", "message": "单据已审核", "retryable": False, "hint": "先 load"}
        send.add("POST", "/v1/co/vouchers/verify", status=409, body={"error": error})
        result = app.call("u8_write", {"route": "vouchers/verify", "body": {}})
        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"], {"status": 409, "error": error, "idempotency_key": "generated-key-1"}
        )

    def test_retry_after_carried(self):
        app, send = make_app()
        error = {"code": "busy", "message": "队列满", "retryable": True}
        send.add("POST", "/v1/co/vouchers/load", status=429, body={"error": error}, headers={"retry-after": "5"})
        result = app.call("u8_read", {"route": "vouchers/load", "body": {}})
        self.assertEqual(result["structuredContent"]["retry_after"], 5)

    def test_outcome_unknown_keeps_key(self):
        app, send = make_app()
        error = {"code": "outcome_unknown", "message": "超时", "retryable": False}
        send.add("POST", "/v1/co/vouchers/create", status=504, body={"error": error})
        result = app.call("u8_write", {"route": "vouchers/create", "body": {}})
        self.assertEqual(result["structuredContent"]["status"], 504)
        self.assertEqual(result["structuredContent"]["idempotency_key"], "generated-key-1")

    def test_non_json_error_body(self):
        app, send = make_app()
        send.add("POST", "/v1/co/vouchers/load", status=502, raw=b"<html>bad gateway</html>")
        result = app.call("u8_read", {"route": "vouchers/load", "body": {}})
        error = result["structuredContent"]["error"]
        self.assertEqual((error["code"], error["retryable"]), ("bad_response", True))

    def test_unparsable_2xx_write_is_outcome_unknown(self):
        app, send = make_app()
        send.add("POST", "/v1/co/vouchers/verify", status=200, raw=b"not json")
        result = app.call("u8_write", {"route": "vouchers/verify", "body": {}})
        self.assertEqual(result["structuredContent"]["error"]["code"], "outcome_unknown")

    def test_transport_error_on_write_is_outcome_unknown(self):
        app, send = make_app()
        send.fail("POST", "/v1/co/vouchers/create", TransportError("请求超时", sent=True))
        result = app.call("u8_write", {"route": "vouchers/create", "body": {}})
        payload = result["structuredContent"]
        self.assertEqual(payload["error"]["code"], "outcome_unknown")
        self.assertFalse(payload["error"]["retryable"])
        self.assertEqual(payload["idempotency_key"], "generated-key-1")

    def test_transport_error_not_sent_is_unavailable(self):
        app, send = make_app()
        send.fail("POST", "/v1/co/vouchers/verify", TransportError("连接被拒绝", sent=False))
        result = app.call("u8_write", {"route": "vouchers/verify", "body": {}})
        error = result["structuredContent"]["error"]
        self.assertEqual((error["code"], error["retryable"]), ("unavailable", True))

    def test_transport_error_on_read_is_unavailable(self):
        app, send = make_app()
        send.fail("POST", "/v1/co/vouchers/load", TransportError("请求超时", sent=True))
        result = app.call("u8_read", {"route": "vouchers/load", "body": {}})
        self.assertEqual(result["structuredContent"]["error"]["code"], "unavailable")

    def test_secrets_scrubbed_from_errors(self):
        app, send = make_app()
        message = f"boom {PASSWORD} {TOKEN}"
        send.fail("POST", "/v1/co/vouchers/load", TransportError(message, sent=False))
        result = app.call("u8_read", {"route": "vouchers/load", "body": {}})
        text = result["content"][0]["text"]
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(TOKEN, text)
        self.assertIn("***", text)

    def test_internal_error_hides_details(self):
        app, send = make_app()
        send.fail("POST", "/v1/co/vouchers/load", ValueError(PASSWORD))
        with self.assertLogs("u8co_mcp", level="ERROR"):
            result = app.call("u8_read", {"route": "vouchers/load", "body": {}})
        self.assertEqual(result["structuredContent"]["error"]["code"], "internal_error")
        self.assertNotIn(PASSWORD, result["content"][0]["text"])

    def test_missing_password(self):
        app, send = make_app()
        with mock.patch.dict(os.environ, {"U8CO_MCP_PASSWORD": ""}):
            result = app.call("u8_read", {"route": "vouchers/load", "body": {}})
        self.assertEqual(result["structuredContent"]["error"]["code"], "config_error")
        self.assertEqual(send.calls, [])

    def test_missing_token(self):
        app, send = make_app()
        with mock.patch.dict(os.environ, {"U8CO_MCP_TEST_TOKEN": ""}):
            result = app.call("u8_read", {"route": "vouchers/load", "body": {}})
        self.assertEqual(result["structuredContent"]["error"]["code"], "token_failed")
        self.assertEqual(send.calls, [])


class ConfigErrorAppTests(AppCase):
    def test_broken_config_still_serves_catalog(self):
        app = App(None, config_error="找不到配置文件")
        self.assertNotIn("u8_write", [t["name"] for t in app.list_tools()])
        catalog = app.call("u8_describe", {})
        self.assertFalse(catalog["isError"])
        self.assertTrue(catalog["structuredContent"]["routes"])
        result = app.call("u8_read", {"route": "health"})
        error = result["structuredContent"]["error"]
        self.assertEqual((error["code"], error["message"]), ("config_error", "找不到配置文件"))
        self.assertEqual(app.call("u8_write", {"route": "vouchers/create", "body": {}})["isError"], True)


class ClientCredentialsTests(AppCase):
    TOKEN_URL = "https://auth.example.com/oauth/token"

    def _app(self, tmp_secret: str):
        cfg = {
            "type": "client_credentials",
            "token_url": self.TOKEN_URL,
            "client_id": "mcp-client",
            "client_secret_file": tmp_secret,
            "scope": "openid",
        }
        return make_app(token=cfg)

    def _secret_file(self) -> str:
        import tempfile

        handle = tempfile.NamedTemporaryFile("w", delete=False, suffix=".secret")
        handle.write("client-secret-value\n")
        handle.close()
        os.chmod(handle.name, 0o600)
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_token_cached_and_refreshed_on_401(self):
        app, send = self._app(self._secret_file())
        send.add("POST", "/oauth/token", body={"access_token": "tok-1", "expires_in": 3600})
        send.add("POST", "/oauth/token", body={"access_token": "tok-2", "expires_in": 3600})
        send.add("POST", "/v1/co/vouchers/load", body={"ok": True})
        send.add("POST", "/v1/co/vouchers/load", status=401, body={"error": {"code": "unauthorized"}})
        send.add("POST", "/v1/co/vouchers/load", body={"ok": True})
        app.call("u8_read", {"route": "vouchers/load", "body": {}})
        app.call("u8_read", {"route": "vouchers/load", "body": {}})
        token_calls = [c for c in send.calls if c.path == "/oauth/token"]
        api_calls = [c for c in send.calls if c.path == "/v1/co/vouchers/load"]
        self.assertEqual(len(token_calls), 2)
        self.assertIn(b"grant_type=client_credentials", token_calls[0].data)
        self.assertIn(b"scope=openid", token_calls[0].data)
        auth = [c.headers["Authorization"] for c in api_calls]
        self.assertEqual(auth, ["Bearer tok-1", "Bearer tok-1", "Bearer tok-2"])

    def test_token_endpoint_error_hides_secret(self):
        app, send = self._app(self._secret_file())
        body = {"error": "invalid_client", "x": "client-secret-value"}
        send.add("POST", "/oauth/token", status=401, body=body)
        result = app.call("u8_read", {"route": "vouchers/load", "body": {}})
        error = result["structuredContent"]["error"]
        self.assertEqual(error["code"], "token_failed")
        self.assertIn("invalid_client", error["message"])
        self.assertNotIn("client-secret-value", result["content"][0]["text"])



class LongTimeoutTests(AppCase):
    def test_ia_and_ia_period_close_use_the_long_timeout(self):
        app, send = make_app()
        seen: list[float] = []

        def recording(method, url, headers, data, timeout):
            seen.append(timeout)
            return send(method, url, headers, data, timeout)

        app.api.send = recording
        for route in ("ia/post", "ia/period_end", "periods/close", "vouchers/verify"):
            send.add("POST", "/v1/co/" + route)
        app.call("u8_write", {"route": "ia/post", "body": {"fiscal_year": 2026, "period": 5, "action": "post"}})
        app.call("u8_write", {"route": "ia/period_end", "body": {"fiscal_year": 2026, "period": 5, "action": "run"}})
        body = {"fiscal_year": 2026, "period": 5, "action": "close"}
        app.call("u8_write", {"route": "periods/close", "body": dict(body, module="ia")})
        app.call("u8_write", {"route": "periods/close", "body": dict(body, through=True)})
        app.call("u8_write", {"route": "periods/close", "body": dict(body, module="gl")})
        app.call("u8_write", {"route": "vouchers/verify", "body": {"type": "sale_order", "id": 1}})
        self.assertEqual(seen, [1000.0, 1000.0, 1000.0, 1000.0, 90.0, 90.0])


if __name__ == "__main__":
    unittest.main()
