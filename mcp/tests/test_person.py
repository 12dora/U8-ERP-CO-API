"""incoming 方式的经营管理查询：不用共用的 U8 登录，逐个账套经身份绑定服务（person_proxy）以调用者本人执行。

身份绑定服务用假的发送器代替，不连任何服务。"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from u8co_mcp import mgmt, person
from u8co_mcp.app import App
from u8co_mcp.auth import with_incoming
from u8co_mcp.config import CONFIG_ENV, ConfigError, PersonProxyConfig, parse_config
from u8co_mcp.http import TransportError
from u8co_mcp.server import main

from tests.support import PASSWORD, FakeSend, config_dict
from tests.test_mgmt import jwt

PROXY_URL = "https://u8a.example.com/v1/bindings/as-person"
PROXY_PATH = "/v1/bindings/as-person"
PROXY_TOKEN = "proxy-service-token-0123456789abcdef"
CALLER = jwt({"sub": "3f2b8c1e-5d4a-4b6c-9e7f-0a1b2c3d4e5f", "u8co_mgmt": True, "u8co_accs": ["801", "802", "803"]})
SECRET_FIGURE = 98765432.1


class PersonCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.token_file = self.dir / "proxy-token"
        self.token_file.write_text(PROXY_TOKEN + "\n", encoding="utf-8")
        os.chmod(self.token_file, 0o600)
        pw = self.dir / "pw-801"
        pw.write_text("shared-mgmt-pass\n", encoding="utf-8")
        os.chmod(pw, 0o600)
        self.pw_file = pw

    def section(self, accs=("801", "802", "803"), **proxy_over) -> dict:
        proxy = {"url": PROXY_URL, "token_file": str(self.token_file)}
        proxy.update(proxy_over)
        return {"accounts": [{"acc": a} for a in accs], "person_proxy": proxy}

    def config(self, section: dict | None = None, **over) -> dict:
        data = config_dict(token={"type": "incoming"}, read_only=True, mgmt=section or self.section(), **over)
        del data["u8"]
        return data

    def app(self, section: dict | None = None) -> tuple[App, FakeSend]:
        send = FakeSend()
        app = App(parse_config(self.config(section)), send, today=lambda: dt.date(2026, 6, 1))
        return app, send

    def call(self, app: App, args: dict, name: str = "u8_mgmt_pnl", token: str = CALLER) -> dict:
        with with_incoming(token):
            return app.call(name, args)


class ConfigTests(PersonCase):
    def assertBad(self, data: dict, fragment: str):
        with self.assertRaises(ConfigError) as ctx:
            parse_config(data)
        self.assertIn(fragment, str(ctx.exception))

    def test_proxy_section(self):
        cfg = parse_config(self.config()).mgmt
        self.assertEqual(cfg.person_proxy, PersonProxyConfig(url=PROXY_URL, token_file=self.token_file, timeout_s=90.0))
        self.assertEqual([(a.acc, a.operator, a.password_file) for a in cfg.accounts][0], ("801", "", None))
        cfg = parse_config(self.config(self.section(timeout_s=30))).mgmt
        self.assertEqual(cfg.person_proxy.timeout_s, 30.0)
        cfg = parse_config(self.config(timeout_s=45)).mgmt
        self.assertEqual(cfg.person_proxy.timeout_s, 45.0)

    def test_incoming_rejects_shared_logins(self):
        # 多人共用的服务不能持有共用的 U8 登录：配了口令文件或操作员就启动失败。
        for extra in ({"password_file": str(self.pw_file)}, {"operator": "op001"}):
            section = self.section()
            section["accounts"][0].update(extra)
            self.assertBad(self.config(section), "person_proxy")
        section = {"accounts": [{"acc": "801", "operator": "op001", "password_file": str(self.pw_file)}]}
        self.assertBad(self.config(section), "operator、password_file")

    def test_incoming_requires_proxy(self):
        section = self.section()
        del section["person_proxy"]
        self.assertBad(self.config(section), "person_proxy")

    def test_proxy_only_with_incoming(self):
        section = {
            "accounts": [{"acc": "801", "operator": "op001", "password_file": str(self.pw_file)}],
            "person_proxy": {"url": PROXY_URL, "token_file": str(self.token_file)},
        }
        self.assertBad(config_dict(mgmt=section), "只能在 token.type 为 incoming")

    def test_invalid_proxy(self):
        cases = [
            (self.section(timeout_s=0), "timeout_s"),
            (self.section(timeout_s=True), "timeout_s"),
            (self.section(extra=1), "未知的键"),
            (self.section(url="ftp://u8a.example.com/x"), "person_proxy.url"),
            (self.section(url="http://binding.example.com:8000/v1/bindings/as-person"), "https"),
            ({"accounts": [{"acc": "801"}], "person_proxy": {"url": PROXY_URL}}, "token_file"),
        ]
        for section, fragment in cases:
            with self.subTest(section=section):
                self.assertBad(self.config(section), fragment)
        # 与 API 同在一个容器网络时，allow_insecure_http 也放宽身份绑定服务的地址。
        section = self.section(url="http://binding.example.com:8000/v1/bindings/as-person")
        cfg = parse_config(self.config(section, allow_insecure_http=True))
        self.assertEqual(cfg.mgmt.person_proxy.url, "http://binding.example.com:8000/v1/bindings/as-person")

    def test_http_mode_refuses_to_start_with_shared_password(self):
        section = {"accounts": [{"acc": "801", "operator": "op001", "password_file": str(self.pw_file)}]}
        path = self.dir / "mcp.json"
        path.write_text(json.dumps(self.config(section)), encoding="utf-8")
        with mock.patch.dict(os.environ, {CONFIG_ENV: str(path)}), mock.patch("u8co_mcp.server.serve_http") as serve:
            self.assertEqual(main(["--http"]), 2)
        serve.assert_not_called()

    def test_incoming_rejects_shared_u8_password(self):
        data = self.config()
        data["u8"] = {"acc": "801", "operator": "op001", "password_file": str(self.pw_file)}
        self.assertBad(data, "u8.password_file")
        data["u8"] = {"acc": "801", "operator": "op001"}
        self.assertIsNotNone(parse_config(data).u8)
        with mock.patch.dict(os.environ, {"U8CO_MCP_PASSWORD": PASSWORD}):
            self.assertBad(self.config(), "U8CO_MCP_PASSWORD")
            # stdio 方式照旧可以用环境变量里的口令。
            self.assertIsNotNone(parse_config(config_dict()).u8)

    def test_stdio_keeps_shared_logins(self):
        section = {"accounts": [{"acc": "801", "operator": "op001", "password_file": str(self.pw_file)}]}
        cfg = parse_config(config_dict(mgmt=section)).mgmt
        self.assertIsNone(cfg.person_proxy)
        self.assertEqual(cfg.account("801").operator, "op001")


class CallTests(PersonCase):
    def test_single_account_goes_through_proxy(self):
        app, send = self.app()
        send.add("POST", PROXY_PATH, body={"ok": True, "data": {"ok": True, "report": "pnl"}})
        env = {"U8CO_MCP_PASSWORD": PASSWORD}
        args = {"accounts": ["801"], "fiscal_year": 2026, "period_from": 1, "period_to": 3, "dims": ["dept"]}
        with mock.patch.dict(os.environ, env):
            result = self.call(app, args)
        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"], {"ok": True, "report": "pnl"})
        self.assertEqual(len(send.calls), 1)
        call = send.calls[0]
        self.assertEqual((call.method, call.url), ("POST", PROXY_URL))
        self.assertEqual(call.headers["Authorization"], "Bearer " + PROXY_TOKEN)
        self.assertEqual(call.headers["X-U8co-Caller-Token"], CALLER)
        expected = {"fiscal_year": 2026, "period_from": 1, "period_to": 3, "dims": ["dept"]}
        self.assertEqual(call.body(), {"acc": "801", "route": "reports/mgmt/pnl", "body": expected})
        for word in (b"password", b"logins", b"operator", PASSWORD.encode(), b"shared-mgmt-pass"):
            self.assertNotIn(word, call.data)

    def test_routes_follow_contract(self):
        app, send = self.app()
        send.add("POST", PROXY_PATH, body={"ok": True, "data": {"ok": True}})
        for name, route in mgmt.PROXY_ROUTES.items():
            with self.subTest(name=name):
                self.assertFalse(
                    self.call(app, {"accounts": ["802"], "fiscal_year": 2026, "period_to": 1}, name)["isError"]
                )
                self.assertEqual(send.calls[-1].body()["route"], route)
        self.assertEqual(mgmt.PROXY_ROUTES["u8_mgmt_arap"], "reports/mgmt/arap_terms")
        self.assertEqual(set(mgmt.PROXY_ROUTES), set(mgmt.ROUTES))

    def test_multi_account_is_one_call(self):
        # 多账套（缺省合并）只发一次请求，用 accs；合并由身份绑定服务带齐各账套本人的登录后在 API 里做。
        app, send = self.app()
        data = {"ok": True, "complete": True, "consolidated": {"net_profit": 1}, "by_account": {}}
        send.add("POST", PROXY_PATH, body={"ok": True, "data": data})
        result = self.call(app, {"accounts": ["801", "802"], "fiscal_year": 2026, "period_to": 3})
        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"], data)
        self.assertEqual(len(send.calls), 1)
        body = send.calls[0].body()
        self.assertEqual((body["accs"], body["route"]), (["801", "802"], "reports/mgmt/pnl"))
        self.assertNotIn("acc", body)
        self.assertEqual(body["body"], {"fiscal_year": 2026, "period_from": 3, "period_to": 3})

    def test_default_accounts_and_consolidate_passed_through(self):
        app, send = self.app()
        send.add("POST", PROXY_PATH, body={"ok": True, "data": {"ok": True, "complete": True}})
        for consolidate in (True, False):
            args = {"fiscal_year": 2026, "period_to": 3, "consolidate": consolidate}
            self.assertFalse(self.call(app, args)["isError"])
            body = send.calls[-1].body()
            self.assertEqual(body["accs"], ["801", "802", "803"])
            self.assertIs(body["body"]["consolidate"], consolidate)
        self.assertEqual(len(send.calls), 2)

    def test_partial_result_is_withheld(self):
        # API 部分账套失败仍回 200（complete=false）：整体按失败处理，已取到的账套数字不能出现在结果里。
        app, send = self.app()
        data = {"ok": True, "complete": False, "by_account": {"801": {"net_profit": SECRET_FIGURE}}}
        send.add("POST", PROXY_PATH, body={"ok": True, "data": data})
        result = self.call(app, {"accounts": ["801", "802"], "fiscal_year": 2026, "period_to": 3})
        self.assertTrue(result["isError"])
        error = result["structuredContent"]["error"]
        self.assertEqual((error["code"], error["retryable"]), ("incomplete", True))
        self.assertNotIn(str(SECRET_FIGURE), result["content"][0]["text"])

    def test_binding_missing_lists_codes_only(self):
        app, send = self.app()
        missing = {"code": "binding_missing", "accs_missing": ["802", "999", "x"], "message": "王小明 未绑定"}
        send.add("POST", PROXY_PATH, status=409, body={"error": missing})
        result = self.call(app, {"accounts": ["801", "802"], "fiscal_year": 2026, "period_to": 3})
        payload = result["structuredContent"]
        self.assertEqual(payload["status"], 409)
        error = payload["error"]
        self.assertEqual((error["code"], error["accs_missing"]), ("binding_missing", ["802"]))
        self.assertEqual(error["accounts"], ["801", "802"])
        self.assertIn("802", error["message"])
        self.assertNotIn("王小明", result["content"][0]["text"])
        self.assertNotIn("999", result["content"][0]["text"])

    def test_forbidden_codes_mapped_without_echo(self):
        app, send = self.app()
        args = {"accounts": ["803"], "fiscal_year": 2026, "period_to": 3}
        cases = [
            (403, {"error": {"code": "mgmt_forbidden", "message": "客户甲 应收 12345.67"}}, "mgmt_forbidden"),
            (403, {"error": {"code": "no_permission", "message": "缺 GL030301"}}, "no_permission"),
            (403, {"error": {"code": "weird", "message": "客户甲 应收 12345.67"}}, "mgmt_forbidden"),
            (403, None, "mgmt_forbidden"),
            (409, {"error": {"code": "binding_missing", "message": "x"}}, "binding_missing"),
        ]
        for status, body, code in cases:
            with self.subTest(status=status, body=body):
                send.queue.clear()
                send.add("POST", PROXY_PATH, status=status, body=body, raw=b"<html>" if body is None else None)
                result = self.call(app, args)
                self.assertTrue(result["isError"])
                error = result["structuredContent"]["error"]
                self.assertEqual((result["structuredContent"]["status"], error["code"]), (status, code))
                self.assertEqual((error["retryable"], error["accounts"]), (False, ["803"]))
                self.assertIn("hint", error)
                self.assertNotIn("12345.67", result["content"][0]["text"])

    def test_other_failures(self):
        app, send = self.app()
        args = {"accounts": ["801"], "fiscal_year": 2026, "period_to": 3}
        cases = [
            (401, {"error": {"code": "unauthorized"}}, "proxy_unauthorized", False),
            (503, {"error": {"code": "unavailable"}}, "unavailable", True),
            (504, None, "bad_response", True),
            (502, {"error": {"code": "co failed!"}}, "bad_response", True),
            (200, {"ok": True}, "bad_response", False),
            (200, {"ok": False, "data": {}}, "bad_response", False),
        ]
        for status, body, code, retryable in cases:
            with self.subTest(status=status, body=body):
                send.queue.clear()
                send.add("POST", PROXY_PATH, status=status, body=body, raw=b"oops" if body is None else None)
                error = self.call(app, args)["structuredContent"]["error"]
                self.assertEqual((error["code"], error["retryable"]), (code, retryable))
        send.queue.clear()
        send.fail("POST", PROXY_PATH, TransportError("连接被拒绝", sent=False))
        result = self.call(app, args)
        self.assertEqual(result["structuredContent"]["error"]["code"], "unavailable")
        self.assertTrue(result["structuredContent"]["error"]["retryable"])

    def test_tokens_scrubbed_from_errors(self):
        app, send = self.app()
        send.fail("POST", PROXY_PATH, TransportError("失败 " + PROXY_TOKEN + " " + CALLER, sent=True))
        result = self.call(app, {"accounts": ["801"], "fiscal_year": 2026, "period_to": 3})
        text = result["content"][0]["text"]
        self.assertNotIn(PROXY_TOKEN, text)
        self.assertNotIn(CALLER, text)

    def test_missing_proxy_token_file(self):
        os.remove(self.token_file)
        app, send = self.app()
        result = self.call(app, {"accounts": ["801"], "fiscal_year": 2026, "period_to": 3})
        self.assertEqual(result["structuredContent"]["error"]["code"], "config_error")
        self.assertEqual(send.calls, [])

    def test_no_claim_no_call(self):
        app, send = self.app()
        result = self.call(app, {"accounts": ["801"], "fiscal_year": 2026, "period_to": 3}, token=jwt({"sub": "x"}))
        self.assertEqual(result["structuredContent"]["error"]["code"], "mgmt_forbidden")
        self.assertEqual(send.calls, [])

    def test_never_calls_api_directly(self):
        app, send = self.app()
        send.add("POST", PROXY_PATH, body={"ok": True, "data": {"ok": True}})
        self.call(app, {"accounts": ["801"], "fiscal_year": 2026, "period_to": 3})
        self.assertTrue(all(c.path == PROXY_PATH for c in send.calls))


class GuardTests(PersonCase):
    def test_client_refuses_login_keys(self):
        cfg = PersonProxyConfig(url=PROXY_URL, token_file=self.token_file)
        send = FakeSend()
        for key in ("logins", "password", "operator", "Password", "acc", "accs"):
            with self.subTest(key=key), self.assertRaises(AssertionError):
                person.call(cfg, send, (PROXY_TOKEN, CALLER), (["801"], "reports/mgmt/pnl", {key: "x"}))
        self.assertEqual(send.calls, [])

    def test_build_body_refuses_proxy_config(self):
        cfg = parse_config(self.config()).mgmt
        with self.assertRaises(AssertionError):
            mgmt.build_body(cfg, "u8_mgmt_pnl", {"fiscal_year": 2026, "period_to": 1}, dt.date(2026, 6, 1))


if __name__ == "__main__":
    unittest.main()
