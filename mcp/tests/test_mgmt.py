"""经营管理查询工具：配置、令牌声明门控、工具定义和请求体。"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from u8co_mcp import mgmt
from u8co_mcp.config import ConfigError, parse_config

from tests.support import BASE, TOKEN_ENV, config_dict, make_app

MGMT_TOOLS = {"u8_mgmt_overview", "u8_mgmt_pnl", "u8_mgmt_sales", "u8_mgmt_arap", "u8_mgmt_cash_stock"}


def jwt(payload: dict) -> str:
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode("utf-8")).decode("ascii").rstrip("=")

    return part({"alg": "RS256"}) + "." + part(payload) + ".signature"


class MgmtCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.passwords = {}
        for acc in ("801", "802", "803", "804"):
            path = self.dir / f"pw-{acc}"
            path.write_text(f"pass-{acc}-secret\n", encoding="utf-8")
            os.chmod(path, 0o600)
            self.passwords[acc] = f"pass-{acc}-secret"
        self.token(jwt({"u8co_mgmt": True, "sub": "mcp-client"}))

    def token(self, value: str):
        patcher = mock.patch.dict(os.environ, {TOKEN_ENV: value}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def section(self, accs=("801", "802"), **over) -> dict:
        accounts = [{"acc": a, "operator": "zhangsan", "password_file": str(self.dir / f"pw-{a}")} for a in accs]
        data = {"accounts": accounts}
        data.update(over)
        return data

    def app(self, **over):
        return make_app(mgmt=self.section(**over))

    def names(self, app) -> set[str]:
        return {tool["name"] for tool in app.list_tools()}


class ConfigTests(MgmtCase):
    def assertBad(self, section, fragment: str):
        with self.assertRaises(ConfigError) as ctx:
            parse_config(config_dict(mgmt=section))
        self.assertIn(fragment, str(ctx.exception))

    def test_defaults(self):
        cfg = parse_config(config_dict(mgmt=self.section())).mgmt
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.claim, "u8co_mgmt")
        self.assertEqual([a.acc for a in cfg.accounts], ["801", "802"])
        self.assertEqual(cfg.account("802").password_file, self.dir / "pw-802")
        self.assertIsNone(cfg.account("809"))
        self.assertIsNone(parse_config(config_dict()).mgmt)
        self.assertEqual(cfg.scope, "")
        self.assertEqual(parse_config(config_dict(mgmt=self.section(scope="u8co.mgmt"))).mgmt.scope, "u8co.mgmt")

    def test_invalid_sections(self):
        self.assertBad({"accounts": []}, "1 到 12")
        self.assertBad(self.section(extra=1), "未知的键")
        self.assertBad(self.section(enabled="yes"), "enabled")
        self.assertBad(self.section(claim="bad claim"), "声明名")
        self.assertBad(self.section(scope="a b"), "scope")
        self.assertBad(self.section(scope=""), "mgmt.scope")
        self.assertBad(self.section(accs=("801", "801")), "不能重复")
        self.assertBad({"accounts": [{"acc": "8a1", "operator": "x", "password_file": "/p"}]}, "三位数字")
        self.assertBad({"accounts": [{"acc": "801", "operator": "x"}]}, "缺少 password_file")
        self.assertBad({"accounts": [{"acc": "801", "operator": "x", "password_file": "/p", "password": "x"}]}, "未知")


class GateTests(MgmtCase):
    def test_listed_with_claim(self):
        app, _send = self.app()
        self.assertTrue(MGMT_TOOLS <= self.names(app))

    def test_custom_claim_name(self):
        self.token(jwt({"erp/mgmt": True}))
        app, _send = self.app(claim="erp/mgmt")
        self.assertTrue(MGMT_TOOLS <= self.names(app))

    def test_listed_with_configured_scope(self):
        for payload in ({"scope": "openid u8co.mgmt"}, {"scp": ["u8co.mgmt"]}):
            self.token(jwt(payload))
            app, _send = self.app(scope="u8co.mgmt")
            self.assertTrue(MGMT_TOOLS <= self.names(app), payload)
        # 没配 scope 时令牌的 scope 不起作用；配了但令牌 scope 不含它也不列。
        self.token(jwt({"scope": "u8co.mgmt"}))
        app, _send = self.app()
        self.assertFalse(MGMT_TOOLS & self.names(app))
        self.token(jwt({"scope": "openid u8co.read"}))
        app, _send = self.app(scope="u8co.mgmt")
        self.assertFalse(MGMT_TOOLS & self.names(app))

    def test_hidden_without_claim_or_config(self):
        for payload in ({}, {"u8co_mgmt": "true"}, {"u8co_mgmt": False}):
            self.token(jwt(payload))
            app, _send = self.app()
            self.assertFalse(MGMT_TOOLS & self.names(app), payload)
        self.token(jwt({"u8co_mgmt": True}))
        app, _send = make_app()
        self.assertFalse(MGMT_TOOLS & self.names(app))
        app, _send = self.app(enabled=False)
        self.assertFalse(MGMT_TOOLS & self.names(app))

    def test_opaque_or_missing_token_hides_tools(self):
        self.token("opaque-token-value")
        app, _send = self.app()
        self.assertFalse(MGMT_TOOLS & self.names(app))
        self.token("")
        app, _send = self.app()
        self.assertFalse(MGMT_TOOLS & self.names(app))
        result = app.call("u8_mgmt_pnl", {"fiscal_year": 2026, "period_to": 3})
        self.assertEqual(result["structuredContent"]["error"]["code"], "token_failed")

    def test_call_without_claim_is_forbidden(self):
        self.token(jwt({}))
        app, send = self.app()
        result = app.call("u8_mgmt_pnl", {"fiscal_year": 2026, "period_to": 3})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "mgmt_forbidden")
        self.assertEqual(send.calls, [])

    def test_claims_decoder(self):
        self.assertEqual(mgmt.token_claims("a.b"), {})
        self.assertEqual(mgmt.token_claims("a.!!!.c"), {})
        self.assertEqual(mgmt.token_claims("a." + base64.urlsafe_b64encode(b"[1]").decode() + ".c"), {})
        self.assertTrue(mgmt.has_claim(jwt({"x": True}), "x"))


class SchemaTests(MgmtCase):
    def tool(self, app, name: str) -> dict:
        return next(t for t in app.list_tools() if t["name"] == name)

    def test_schemas(self):
        app, _send = self.app()
        for name in MGMT_TOOLS:
            tool = self.tool(app, name)
            schema = tool["inputSchema"]
            self.assertEqual(schema["required"], ["fiscal_year", "period_to"])
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(schema["properties"]["accounts"]["items"]["enum"], ["801", "802"])
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertTrue(tool["description"].startswith("经营管理"))
        pnl_props = self.tool(app, "u8_mgmt_pnl")["inputSchema"]["properties"]
        self.assertIn("dims", pnl_props)
        self.assertNotIn("detail", pnl_props)
        self.assertIn("group_by", self.tool(app, "u8_mgmt_sales")["inputSchema"]["properties"])
        self.assertIn("side", self.tool(app, "u8_mgmt_arap")["inputSchema"]["properties"])
        self.assertNotIn("side", self.tool(app, "u8_mgmt_overview")["inputSchema"]["properties"])


class CallTests(MgmtCase):
    def call(self, app, name: str, args: dict) -> dict:
        return app.call(name, args)

    def test_pnl_body_and_passwords(self):
        app, send = self.app()
        send.add("POST", "/v1/co/mgmt/pnl", body={"ok": True, "report": "pnl"})
        result = self.call(
            app, "u8_mgmt_pnl", {"fiscal_year": 2026, "period_from": 1, "period_to": 3, "dims": ["dept"]}
        )
        self.assertFalse(result["isError"], result)
        call = send.calls[-1]
        self.assertEqual(call.url, BASE + "/v1/co/mgmt/pnl")
        body = call.body()
        self.assertEqual(
            body["logins"],
            [
                {
                    "acc": "801",
                    "operator": "zhangsan",
                    "password": self.passwords["801"],
                    "year": "2026",
                    "date": "2026-06-01",
                },
                {
                    "acc": "802",
                    "operator": "zhangsan",
                    "password": self.passwords["802"],
                    "year": "2026",
                    "date": "2026-06-01",
                },
            ],
        )
        self.assertEqual(
            {k: v for k, v in body.items() if k != "logins"},
            {"fiscal_year": 2026, "period_from": 1, "period_to": 3, "dims": ["dept"]},
        )

    def test_subset_period_default_and_past_year_login_date(self):
        app, send = self.app(accs=("801", "802", "803"))
        send.add("POST", "/v1/co/mgmt/overview")
        args = {"accounts": ["803"], "fiscal_year": 2025, "period_to": 2, "consolidate": False}
        self.assertFalse(self.call(app, "u8_mgmt_overview", args)["isError"])
        body = send.calls[-1].body()
        self.assertEqual([login["acc"] for login in body["logins"]], ["803"])
        self.assertEqual(body["logins"][0]["date"], "2025-02-28")
        self.assertEqual((body["period_from"], body["period_to"], body["consolidate"]), (2, 2, False))

    def test_arap_params_passed_through(self):
        app, send = self.app()
        send.add("POST", "/v1/co/mgmt/arap")
        args = {"fiscal_year": 2026, "period_to": 5, "side": "ap", "as_of": "2026-05-31", "buckets": [30, 90]}
        self.assertFalse(self.call(app, "u8_mgmt_arap", args)["isError"])
        body = send.calls[-1].body()
        self.assertEqual((body["side"], body["as_of"], body["buckets"]), ("ap", "2026-05-31", [30, 90]))

    def test_bad_arguments(self):
        app, send = self.app(accs=("801", "802", "803", "804"))
        cases = [
            ("u8_mgmt_pnl", {"fiscal_year": 2026, "period_from": 4, "period_to": 3}, "period_from"),
            ("u8_mgmt_pnl", {"fiscal_year": 2026, "period_to": 3}, "accounts"),
            ("u8_mgmt_pnl", {"accounts": ["801", "801"], "fiscal_year": 2026, "period_to": 3}, "accounts"),
            (
                "u8_mgmt_arap",
                {"accounts": ["801"], "fiscal_year": 2026, "period_to": 3, "as_of": "2026-02-30"},
                "as_of",
            ),
            (
                "u8_mgmt_arap",
                {"accounts": ["801"], "fiscal_year": 2026, "period_to": 3, "buckets": [60, 30]},
                "buckets",
            ),
        ]
        for name, args, field in cases:
            error = self.call(app, name, args)["structuredContent"]["error"]
            self.assertEqual((error["code"], error.get("field")), ("bad_arguments", field), args)
        for args in (
            {"fiscal_year": 2026},
            {"fiscal_year": 2026, "period_to": 13},
            {"accounts": ["809"], "fiscal_year": 2026, "period_to": 1},
        ):
            error = self.call(app, "u8_mgmt_sales", args)["structuredContent"]["error"]
            self.assertEqual(error["code"], "bad_arguments", args)
        self.assertEqual(send.calls, [])

    def test_password_scrubbed_from_api_errors(self):
        app, send = self.app()
        message = "登录失败 " + self.passwords["801"]
        send.add(
            "POST",
            "/v1/co/mgmt/sales",
            status=403,
            body={"error": {"code": "mgmt_forbidden", "message": message, "retryable": False}},
        )
        result = self.call(app, "u8_mgmt_sales", {"fiscal_year": 2026, "period_to": 1})
        self.assertTrue(result["isError"])
        self.assertNotIn(self.passwords["801"], result["content"][0]["text"])
        self.assertEqual(result["structuredContent"]["status"], 403)

    def test_default_accounts_follow_token_claim(self):
        # 没给 accounts：配置的账套与令牌账套声明取交集（按配置的顺序）；声明不是三位数字的项忽略。
        cases = [
            ({"u8co_accs": ["803", "801", "x"]}, {}, ["801", "803"]),
            ({"u8co_accs": "802, 804"}, {}, ["802", "804"]),
            ({}, {"accs": ("801", "802")}, ["801", "802"]),
            ({"accs": ["802"]}, {"accounts_claim": "accs"}, ["802"]),
            ({"u8co_accs": ["801"]}, {"accounts_claim": "", "accs": ("801", "802")}, ["801", "802"]),
        ]
        for claims, over, expected in cases:
            with self.subTest(claims=claims, over=over):
                self.token(jwt(dict(claims, u8co_mgmt=True)))
                over = dict(over)
                app, send = self.app(accs=over.pop("accs", ("801", "802", "803", "804")), **over)
                send.add("POST", "/v1/co/mgmt/overview")
                result = self.call(app, "u8_mgmt_overview", {"fiscal_year": 2026, "period_to": 1})
                self.assertFalse(result["isError"], result)
                self.assertEqual([login["acc"] for login in send.calls[-1].body()["logins"]], expected)

    def test_default_accounts_outside_claim(self):
        self.token(jwt({"u8co_mgmt": True, "u8co_accs": ["909"]}))
        app, send = self.app()
        error = self.call(app, "u8_mgmt_overview", {"fiscal_year": 2026, "period_to": 1})["structuredContent"]["error"]
        self.assertEqual((error["code"], error["field"]), ("bad_arguments", "accounts"))
        # 显式给的账套不按声明过滤，交给 API 判断。
        send.add("POST", "/v1/co/mgmt/overview")
        args = {"fiscal_year": 2026, "period_to": 1, "accounts": ["801"]}
        self.assertFalse(self.call(app, "u8_mgmt_overview", args)["isError"])
        self.assertEqual(len(send.calls), 1)

    def test_accounts_claim_config(self):
        self.assertEqual(parse_config(config_dict(mgmt=self.section())).mgmt.accounts_claim, "u8co_accs")
        self.assertEqual(parse_config(config_dict(mgmt=self.section(accounts_claim=""))).mgmt.accounts_claim, "")
        for bad in ("bad claim", 3, None):
            with self.subTest(bad=bad), self.assertRaises(ConfigError) as ctx:
                parse_config(config_dict(mgmt=self.section(accounts_claim=bad)))
            self.assertIn("accounts_claim", str(ctx.exception))

    def test_missing_password_file_is_config_error(self):
        os.remove(self.dir / "pw-802")
        app, send = self.app()
        result = self.call(app, "u8_mgmt_cash_stock", {"fiscal_year": 2026, "period_to": 1})
        self.assertEqual(result["structuredContent"]["error"]["code"], "config_error")
        self.assertEqual(send.calls, [])


if __name__ == "__main__":
    unittest.main()
