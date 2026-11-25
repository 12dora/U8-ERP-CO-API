"""写入策略用例（write_policy）的登记、改动挑选和回放核对，以及健康检查的 signatures。不连接服务器。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run, write_policy, steps_sql
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.write_policy import WRITE_POLICY_OPS
from co.e2e.casespec.tags import is_exclusive
from co.e2e.tests import support
from co.e2e.tests.wpolicy_fake import PERMISSIVE_BYTES, FakeBridge, env_for, error

_PREVIEW = (
    "wp-health",
    "wp-deny-unlisted-type",
    "wp-freeze-account",
    "wp-window",
    "wp-operator-deny",
    "wp-max-lines",
    "wp-invalid-keeps-last",
    "wp-tier2-still-test-only",
)
_ALL = _PREVIEW + ("wp-quota-minute",)
_SPEC = CaseSpec("wp-x", "t")
_STEP = Step("custom")
_SALE_MASTER = "C001|01|01|P01|INV01|1|10|13|"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _run(op: str, bridge: FakeBridge, bag: dict | None = None) -> tuple[str, str]:
    return WRITE_POLICY_OPS[op](env_for(bridge, bag), _SPEC, _STEP)


def _routes(bridge: FakeBridge) -> list[str]:
    return [route for route, _fields in bridge.calls]


class RegistrationTests(unittest.TestCase):
    def test_catalog_ops_and_exclusive(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in build_cases({})}
        for name in _ALL:
            with self.subTest(name=name):
                self.assertEqual(names.count(name), 1)
                spec = cases[name]
                self.assertTrue(spec.exclusive and is_exclusive(spec))
                self.assertTrue(spec.routes)
                self.assertTrue(all(ops_run.has_op(step.sql_name) for step in spec.steps))
        self.assertFalse(cases["wp-health"].requires_login)
        self.assertEqual(cases["wp-quota-minute"].needs, ("sale_master",))

    def test_policy_sources_select_cases(self) -> None:
        picked = select_changed(["co/bridge/src/WritePolicyEval.cs"], list(known_names()))
        for name in _ALL:
            self.assertIn(name, picked)


class GuardTests(unittest.TestCase):
    def test_no_policy_is_na(self) -> None:
        bridge = FakeBridge()
        bridge.with_policy = False
        for op in WRITE_POLICY_OPS:
            if op == "wp_quota_minute":
                continue
            with self.subTest(op=op):
                self.assertEqual(_run(op, bridge)[0], "N/A")
        self.assertEqual(bridge.calls, [])
        self.assertEqual(bridge.written(), [])

    def test_broken_policy_fails_without_swap(self) -> None:
        bridge = FakeBridge(state="invalid")
        status, detail = _run("wp_freeze", bridge)
        self.assertEqual(status, "FAIL")
        self.assertIn("state=invalid", detail)
        self.assertEqual(bridge.written(), [])


class HealthCaseTests(unittest.TestCase):
    def test_health_pass_and_bad_version(self) -> None:
        bridge = FakeBridge()
        self.assertEqual(_run("wp_health", bridge)[0], "PASS")
        bridge.snap = {"version": 2}
        status, detail = _run("wp_health", bridge)
        self.assertEqual(status, "FAIL")
        self.assertIn("version 应为 1", detail)


class PreviewCaseTests(unittest.TestCase):
    def _done(self, bridge: FakeBridge) -> None:
        # 退出时写回原文件，桥重载为 ok。
        self.assertEqual(bridge.content, PERMISSIVE_BYTES)
        self.assertEqual(bridge.state, "ok")

    def test_deny_type(self) -> None:
        answers = [
            error(403, "write_not_allowed", "该账套不允许此写入", detail={"type": "gl", "op": "create"}),
            error(400, "bad_request", "客户编码不存在"),
        ]
        bridge = FakeBridge(answers)
        status, detail = _run("wp_deny_type", bridge)
        self.assertEqual(status, "PASS", detail)
        policy = bridge.written()[0]
        self.assertEqual(policy["accounts"]["801"]["allow"], [{"type": "sale_order", "ops": ["create"]}])
        self.assertEqual(_routes(bridge), ["/v1/gl/vouchers/create", "/v1/vouchers/create"])
        self.assertTrue(all(fields.get("dry_run") is True for _route, fields in bridge.calls))
        self._done(bridge)

    def test_deny_type_real_body_needs_200(self) -> None:
        denied = {"type": "gl", "op": "create"}
        bridge = FakeBridge([error(403, "write_not_allowed", detail=denied), {"ok": True, "dry_run": True}])
        self.assertEqual(_run("wp_deny_type", bridge, {"sale_master": _SALE_MASTER})[0], "PASS")
        bridge = FakeBridge([error(403, "write_not_allowed", detail=denied), error(403, "write_not_allowed")])
        self.assertEqual(_run("wp_deny_type", bridge)[0], "FAIL")
        self._done(bridge)

    def test_freeze(self) -> None:
        bridge = FakeBridge([error(503, "write_frozen", "写入已冻结：回归冻结"), {"ok": True, "items": []}])
        status, detail = _run("wp_freeze", bridge)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(bridge.written()[0]["freeze"], {"accounts": ["801"], "reason": "回归冻结"})
        self.assertEqual(_routes(bridge), ["/v1/vouchers/create", "/v1/vouchers/list"])
        self._done(bridge)

    def test_freeze_without_reason_fails(self) -> None:
        bridge = FakeBridge([error(503, "write_frozen", "写入已冻结")])
        self.assertEqual(_run("wp_freeze", bridge)[0], "FAIL")
        self._done(bridge)

    def test_window_operator_lines(self) -> None:
        rows = (
            ("wp_window", error(503, "write_window", "当前时段不允许写入"), "denyDates", ["2026-10-03"]),
            ("wp_operator", error(403, "operator_not_allowed"), "accounts", {"801": None}),
            ("wp_max_lines", error(400, "write_limit", "行数超过上限", "lines"), "accounts", {"801": None}),
        )
        for op, answer, key, _want in rows:
            with self.subTest(op=op):
                bridge = FakeBridge([answer])
                status, detail = _run(op, bridge)
                self.assertEqual(status, "PASS", detail)
                self.assertIn(key, bridge.written()[0])
                self._done(bridge)
        bridge = FakeBridge([error(400, "write_limit", "行数超过上限", "lines")])
        _run("wp_max_lines", bridge)
        self.assertEqual(len(bridge.calls[0][1]["lines"]), 2)
        self.assertEqual(bridge.written()[0]["accounts"]["801"]["quotas"], {"maxLines": 1})

    def test_operator_deny_lists_lane_operator(self) -> None:
        bridge = FakeBridge([error(403, "operator_not_allowed")])
        _run("wp_operator", bridge)
        self.assertEqual(bridge.written()[0]["accounts"]["801"]["operators"], {"allow": [], "deny": ["op001"]})

    def test_wrong_code_fails(self) -> None:
        bridge = FakeBridge([{"ok": True, "dry_run": True}])
        status, detail = _run("wp_window", bridge)
        self.assertEqual(status, "FAIL")
        self.assertIn("503 write_window", detail)
        self._done(bridge)

    def test_invalid_keeps_last(self) -> None:
        bridge = FakeBridge([error(503, "write_frozen", "写入已冻结：回归冻结")])
        status, detail = _run("wp_invalid", bridge)
        self.assertEqual(status, "PASS", detail)
        written = bridge.written()
        self.assertEqual(len(written), 3)
        self.assertIn("e2eUnknownKey", written[1])
        self.assertEqual(written[2], json.loads(PERMISSIVE_BYTES))
        self._done(bridge)

    def test_tier2(self) -> None:
        bridge = FakeBridge([error(403, "test_account_only")])
        self.assertEqual(_run("wp_tier2", bridge)[0], "PASS")
        self.assertEqual(bridge.calls[0][0], "/v1/periods/close")
        self.assertIs(bridge.calls[0][1]["dry_run"], True)
        self.assertEqual(_run("wp_tier2", FakeBridge([error(409, "state_mismatch")]))[0], "N/A")
        self.assertEqual(_run("wp_tier2", FakeBridge([error(403, "write_not_allowed")]))[0], "FAIL")


class QuotaCaseTests(unittest.TestCase):
    def _seed(self, flag: bool):
        return mock.patch.object(write_policy, "conf", lambda: SimpleNamespace(seedable=flag))

    def test_not_seedable_is_na(self) -> None:
        bridge = FakeBridge()
        with self._seed(False):
            self.assertEqual(_run("wp_quota_minute", bridge, {"sale_master": _SALE_MASTER})[0], "N/A")
        self.assertEqual(bridge.calls, [])

    def test_quota_cycle_deletes_after_restore(self) -> None:
        answers = [
            error(429, "write_quota", "已超过账套写入限额", detail={"retry_after_seconds": 20}),
            {"ok": True, "dry_run": True},
            {"ok": True, "id": 77},
            error(429, "write_quota", "已超过账套写入限额", detail={"retry_after_seconds": 59}),
            {"ok": True, "deleted": True},
        ]
        bridge = FakeBridge(answers)
        with self._seed(True), mock.patch.object(write_policy.time, "sleep") as nap:
            status, detail = _run("wp_quota_minute", bridge, {"sale_master": _SALE_MASTER})
        self.assertEqual(status, "PASS", detail)
        nap.assert_called_once_with(21)
        self.assertEqual(bridge.written()[0]["defaults"], {"writesPerMinute": 1})
        dry = [fields.get("dry_run") for _route, fields in bridge.calls]
        self.assertEqual(dry, [True, True, None, True, None])
        # 删除排在写回原策略之后。
        kinds = [kind for kind, _ in bridge.log]
        self.assertEqual(kinds[-2:], ["write", "call"])
        self.assertEqual(bridge.calls[-1], ("/v1/vouchers/delete", bridge.calls[-1][1]))
        self.assertEqual(bridge.calls[-1][1]["id"], 77)

    def test_quota_not_hit_still_deletes(self) -> None:
        dry = {"ok": True, "dry_run": True}
        answers = [dry, {"ok": True, "id": 78}, dry, {"ok": True, "deleted": True}]
        bridge = FakeBridge(answers)
        with self._seed(True):
            status, _detail = _run("wp_quota_minute", bridge, {"sale_master": _SALE_MASTER})
        self.assertEqual(status, "FAIL")
        self.assertEqual(bridge.calls[-1][0], "/v1/vouchers/delete")


class StepHealthTests(unittest.TestCase):
    def _env(self, sigs: list[str]) -> SimpleNamespace:
        bridge = FakeBridge()
        bridge.signatures = sigs
        return env_for(bridge)

    def test_pending_then_ok(self) -> None:
        with mock.patch.object(steps_sql.time, "sleep") as nap:
            status, detail = steps_sql.step_health(self._env(["pending", "ok"]), _SPEC, Step("health"))
        self.assertEqual((status, detail), ("PASS", "ok，signatures ok"))
        nap.assert_called_once()

    def test_mismatch_fails(self) -> None:
        status, detail = steps_sql.step_health(self._env(["mismatch:2"]), _SPEC, Step("health"))
        self.assertEqual(status, "FAIL")
        self.assertIn("mismatch:2", detail)


if __name__ == "__main__":
    unittest.main()
