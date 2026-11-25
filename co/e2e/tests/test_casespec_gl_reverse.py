"""总账红字冲销用例 `gl-voucher-reverse-*` 的登记、标签和离线回放（假桥按 U8 规则应答）。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run, gl_reverse
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.gl_reverse import GL_REVERSE_OPS, gl_reverse_cases
from co.e2e.casespec.gl_voucher_sql import gl_key
from co.e2e.tests import support

_NAMES = ("gl-voucher-reverse-cycle", "gl-voucher-reverse-bad-body")
_SRC = gl_key(9, "转", 3)
_OUT = "GL0000000900001"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _line(account: str, debit: float, credit: float, digest: str) -> dict:
    return {"account": account, "debit": debit, "credit": credit, "digest": digest}


class _FakeGl:
    """原凭证 9 期 转-3（已审核）；冲销生成 9 期 转-4。按桥的规则应答，可用 broken 打乱红字凭证的一行。"""

    def __init__(self, broken: bool = False) -> None:
        self.broken = broken
        self.calls: list[tuple[str, dict]] = []
        self.posted = False
        self.red: dict | None = None
        self.red_void = False

    def __call__(self, env: object, op: str, body: dict) -> tuple[int, dict]:
        del env
        self.calls.append((op, dict(body)))
        handler = getattr(self, "_" + op, None)
        return handler(body) if handler else (200, {"ok": True})

    def _source(self) -> dict:
        head = {"date": "2026-09-28", "out_no": _OUT, "posted": self.posted, "checker": "甲"}
        lines = [_line("660201", 100.0, 0.0, "办公费"), _line("1001", 0.0, 100.0, "办公费")]
        return {"ok": True, "voucher": head, "lines": lines}

    def _post(self, body: dict) -> tuple[int, dict]:
        del body
        self.posted = True
        return 200, {"ok": True}

    def _load(self, body: dict) -> tuple[int, dict]:
        if body["no"] == _SRC.no:
            return 200, self._source()
        if self.red is None or body["no"] != 4:
            return 404, {"code": "not_found"}
        return 200, self.red

    def _reverse(self, body: dict) -> tuple[int, dict]:
        if body.get("dry_run") is True:
            return 200, {"ok": True, "dry_run": True, "mode": "validate"}
        if body["no"] != _SRC.no or not self.posted or self.red is not None:
            return 409, {"code": "state_mismatch", "message": "拒绝"}
        prefix = "[冲销2026.09.28 转-0003号凭证]"
        lines = [_line("660201", -100.0, 0.0, prefix + "办公费"), _line("1001", 0.0, -100.0, prefix + "办公费")]
        if self.broken:
            lines[1]["credit"] = 100.0
        head = {"posted": False, "checker": "", "blue_out_no": _OUT, "out_no": "GL0000000900002"}
        self.red = {"ok": True, "voucher": head, "lines": lines}
        source = {"period": 9, "sign": "转", "no": 3, "out_no": _OUT}
        return 200, {"ok": True, "period": 9, "sign": "转", "no": 4, "out_no": "GL0000000900002", "reversal_of": source}

    def _void(self, body: dict) -> tuple[int, dict]:
        self.red_void = body["no"] == 4
        return 200, {"ok": True}

    def _delete(self, body: dict) -> tuple[int, dict]:
        if body["no"] != 4 or not self.red_void:
            return 409, {"code": "state_mismatch"}
        self.red = None
        return 200, {"ok": True, "deleted": True}


def _run(fake: _FakeGl, first_open: int = 9, cells: str = "0") -> tuple[str, str]:
    env = SimpleNamespace(today="2026-09-28", operator_name="甲", bag={})
    with mock.patch.object(gl_reverse, "_send", fake), \
            mock.patch.object(gl_reverse, "_first_open", return_value=first_open), \
            mock.patch.object(gl_reverse, "_cell", return_value=cells), \
            mock.patch.object(gl_reverse, "_create", return_value=(_SRC, "")):
        return gl_reverse.gl_reverse_cycle(env, CaseSpec("c", "c"), Step("custom"))


class GlReverseCatalogTests(unittest.TestCase):
    def test_cases_registered_with_tags(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in gl_reverse_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        for name in _NAMES:
            self.assertIn(name, names)
        cycle = cases["gl-voucher-reverse-cycle"]
        self.assertTrue({"gl", "new:gl:转"} <= cycle.tags)
        self.assertTrue(cycle.requires_login)
        self.assertIn("/v1/gl/vouchers/reverse", cycle.routes)
        self.assertTrue(ops_run.has_op(cycle.steps[0].sql_name))
        self.assertEqual(set(GL_REVERSE_OPS), {"gl_reverse_cycle"})
        bad = cases["gl-voucher-reverse-bad-body"]
        self.assertEqual(bad.tags, frozenset(("probe",)))
        self.assertFalse(bad.requires_login)
        self.assertEqual((bad.steps[0].status, bad.steps[0].code), (400, "bad_request"))

    def test_bridge_and_api_files_pick_the_case(self) -> None:
        catalog = list(known_names())
        for path in ("co/bridge/src/GlReverse.cs", "api/u8co_api/co_models_gl_reverse.py"):
            self.assertIn("gl-voucher-reverse-cycle", select_changed([path], catalog), path)


class GlReverseReplayTests(unittest.TestCase):
    def test_full_cycle_passes_and_drops_the_red_voucher(self) -> None:
        fake = _FakeGl()
        verdict, note = _run(fake)
        self.assertEqual(verdict, "PASS", note)
        ops = [op for op, _body in fake.calls]
        self.assertEqual(ops[:4], ["verify", "post", "load", "reverse"])
        self.assertEqual(ops.count("reverse"), 4)  # 预演、冲销、再冲销原凭证、冲销红字凭证
        self.assertEqual(ops[-3:], ["void", "delete", "load"])
        self.assertIsNone(fake.red)
        real = [body for op, body in fake.calls if op == "reverse" and "dry_run" not in body]
        self.assertEqual(real[0]["voucher_date"], "2026-09-28")
        self.assertEqual((real[1]["no"], real[2]["no"]), (3, 4))
        self.assertNotIn("unverify", ops)

    def test_mismatched_red_line_fails_and_still_cleans_up(self) -> None:
        fake = _FakeGl(broken=True)
        verdict, note = _run(fake)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("第 2 行", note)
        self.assertIn("红字凭证已删除", note)
        self.assertIsNone(fake.red)

    def test_na_when_first_open_period_is_after_login_month(self) -> None:
        fake = _FakeGl()
        verdict, _note = _run(fake, first_open=10)
        self.assertEqual(verdict, "N/A")
        self.assertEqual(fake.calls, [])

    def test_na_when_master_sign_required(self) -> None:
        verdict, note = _run(_FakeGl(), cells="1")
        self.assertEqual(verdict, "N/A")
        self.assertIn("主管签字", note)

    def test_post_failure_fails_before_reversing(self) -> None:
        fake = _FakeGl()
        with mock.patch.object(fake, "_post", return_value=(409, {"code": "state_mismatch", "message": "未审核"})), \
                mock.patch.object(gl_reverse, "_drop", return_value="清理：unverify 200"):
            verdict, note = _run(fake)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("记账", note)
        self.assertNotIn("reverse", [op for op, _body in fake.calls])


if __name__ == "__main__":
    unittest.main()
