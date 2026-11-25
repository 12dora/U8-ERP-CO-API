"""经营管理报表（总账口径）只读核对（mgmt_gl）的登记、标签、对照 SQL 和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import mgmt_gl as mg
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.mgmt_gl_sql import cash_sql, meta_sql, mgmt_gl_samples, pnl_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("report-mgmt-pnl", "report-mgmt-meta", "report-mgmt-cash-stock")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    """假桥：pnl 按 body 给行，close_status / meta / cash_stock 给固定响应。"""

    def __init__(self) -> None:
        self.pnl = [{"period": 8, "code": "6001", "debit": 0, "credit": 100.5},
                    {"period": 8, "code": "6401", "debit": 60.25, "credit": 0}]
        self.leaf = [{"period": 8, "code": "600101", "debit": 0, "credit": 100.5, "dept_code": "D1"},
                     {"period": 8, "code": "640101", "debit": 60.25, "credit": 0}]
        self.periods = [{"period": 8, "gl_closed": True, "unposted": 0, "pl_transferred": True}]
        self.closed = {8: True, 9: False}
        self.meta = {"fiscal_year": 2026, "modules": {"GL": {"enabled": True, "start_date": "2026-01-01"}},
                     "periods": [{"period": 8, "closed": {"GL": True}, "unposted": 0}],
                     "watermarks": {"gl_lines": 12, "gl_max_date": "2026-09-30"}}
        self.cash = {"period_start": "2026-08-01", "period_end": "2026-08-31", "cash": {"total": 10},
                     "inventory": {"amount": 20}, "production": {"total_qty": 3, "items": [{"qty": 2}], "others": {"qty": 1}},
                     "purchases": {"source": "invoice", "total_amount": 5, "items": [{"amount": 5}], "others": {"amount": 0}}}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, _env: object, route: str, body: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(body)))
        if route == mg.PNL:
            return 200, {"ok": True, "items": self.leaf if body.get("detail") == "leaf" else self.pnl, "periods": self.periods}
        if route.endswith("/close_status"):
            return 200, {"ok": True, "periods": [{"period": p, "closed": {"GL": f}} for p, f in self.closed.items()]}
        if route == mg.META:
            return 200, dict(self.meta, ok=True)
        return 200, dict(self.cash, ok=True)


def _run(replay: _Replay, op: str, sql_rows: list[list[str]]) -> tuple[str, str]:
    env = SimpleNamespace(client=object(), bag={}, today="2026-09-28")
    with mock.patch.object(mg, "send", replay), mock.patch.object(mg, "rows", lambda *_a: sql_rows):
        return mg.MGMT_GL_OPS[op](env, _SPEC, _STEP)  # type: ignore[arg-type]


_PNL_SQL = [["8", "6001", "100.50"], ["8", "6401", "-60.25"]]


class RegistryTests(unittest.TestCase):
    def test_registered_once_with_ops(self) -> None:
        names = known_names()
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
        for spec in mg.mgmt_gl_cases():
            self.assertTrue(spec.requires_login and spec.optional and spec.routes, spec.name)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name), spec.name)

    def test_tags(self) -> None:
        cases = {spec.name: spec for spec in mg.mgmt_gl_cases()}
        self.assertTrue({"wave:2", "rpt:gl"} <= auto_tags(cases["report-mgmt-pnl"]))
        self.assertTrue({"wave:2", "rpt:close"} <= auto_tags(cases["report-mgmt-meta"]))
        self.assertTrue({"rpt:gl", "rpt:stock", "rpt:arap"} <= auto_tags(cases["report-mgmt-cash-stock"]))


class SqlTests(unittest.TestCase):
    def test_samples_pass_guard(self) -> None:
        names = [name for name, _sql in sample_sql()]
        for name, text in mgmt_gl_samples():
            self.assertIn(name, names)
            self.assertEqual(sql_block_reason(text), "", name)

    def test_shapes_and_refusals(self) -> None:
        self.assertIn("ISNULL(v.ibook, 0) = 1", pnl_sql(2026, 8, False))
        self.assertNotIn("v.ibook", pnl_sql(2026, 8, True))
        self.assertIn("coutsign = N'期间损益'", pnl_sql(2026, 8, True))
        self.assertIn("xc.cclass = N'损益'", pnl_sql(2026, 8, True))
        self.assertIn("v.iyear = 2025", meta_sql(2025))
        self.assertIn("CONVERT(date, '2026-08-31', 23)", cash_sql(2026, 8, "2026-08-01", "2026-08-31"))
        with self.assertRaises(ValueError):
            pnl_sql(2026, 13, False)
        with self.assertRaises(ValueError):
            cash_sql(2026, 8, "2026-08-01'--", "2026-08-31")


class OpsTests(unittest.TestCase):
    def test_pnl_pass_and_mismatch(self) -> None:
        status, detail = _run(_Replay(), "mgmt_pnl_read", _PNL_SQL)
        self.assertEqual(status, "PASS", detail)
        status, detail = _run(_Replay(), "mgmt_pnl_read", [["8", "6001", "100.00"], ["8", "6401", "-60.25"]])
        self.assertEqual(status, "FAIL")
        self.assertIn("6001", detail)

    def test_pnl_na_and_leaf_mismatch(self) -> None:
        replay = _Replay()
        replay.pnl, replay.leaf = [], []
        self.assertEqual(_run(replay, "mgmt_pnl_read", [])[0], "N/A")
        replay = _Replay()
        replay.leaf = replay.leaf[:1]
        status, detail = _run(replay, "mgmt_pnl_read", _PNL_SQL)
        self.assertEqual(status, "FAIL")
        self.assertIn("leaf", detail)

    def test_pnl_period_flag_mismatch(self) -> None:
        replay = _Replay()
        replay.closed = {8: False}
        self.assertEqual(_run(replay, "mgmt_pnl_read", _PNL_SQL)[0], "FAIL")

    def test_meta(self) -> None:
        self.assertEqual(_run(_Replay(), "mgmt_meta_read", [["12", "2026-09-30"]])[0], "PASS")
        status, detail = _run(_Replay(), "mgmt_meta_read", [["13", "2026-09-30"]])
        self.assertEqual(status, "FAIL")
        self.assertIn("水位", detail)

    def test_cash(self) -> None:
        replay = _Replay()
        status, detail = _run(replay, "mgmt_cash_read", [["10", "20", "3", "5", "999"]])
        self.assertEqual(status, "PASS", detail)
        self.assertIn((mg.CASH, {"period": 8}), replay.calls)
        status, detail = _run(_Replay(), "mgmt_cash_read", [["10", "20", "3", "6", "5"]])
        self.assertEqual(status, "FAIL")
        self.assertIn("采购", detail)

    def test_cash_rank_mismatch(self) -> None:
        replay = _Replay()
        replay.cash["production"] = {"total_qty": 3, "items": [{"qty": 1}], "others": {"qty": 1}}
        status, detail = _run(replay, "mgmt_cash_read", [["10", "20", "3", "5", "0"]])
        self.assertEqual(status, "FAIL")
        self.assertIn("产量", detail)


if __name__ == "__main__":
    unittest.main()
