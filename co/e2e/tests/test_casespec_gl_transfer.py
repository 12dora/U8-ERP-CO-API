"""期间损益结转、自定义转账用例（gl_transfer）的登记、分片、SQL 形状和离线回放。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.e2e.casespec import ops_run
from co.e2e.casespec import gl_transfer as gt
from co.e2e.casespec import gl_transfer_sql as q
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.gl_voucher_sql import gl_key
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("gl-transfer-pnl-parity", "gl-transfer-custom-parity", "gl-transfer-pnl-regen", "gl-transfer-test-only")
_U8 = ["6001|||||P1|800.00|0.00", "4103||||||0.00|800.00"]


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _FakeRun:
    """假 _Run：SQL 按查询里的关键字回答，桥按路由回答。"""

    def __init__(self, reply: tuple[int, dict], oracle: str = "2|0|") -> None:
        self.env = SimpleNamespace(today="2026-09-28")
        self.spec = CaseSpec("gl-transfer-pnl-parity", "t")
        self.reply = reply
        self.oracle = oracle
        self.sent: list[tuple[str, dict]] = []

    def send(self, route: str, extra: dict) -> tuple[int, dict]:
        self.sent.append((route, dict(extra)))
        return self.reply

    def cells(self, query: str) -> list[str]:
        if "ORDER BY v.inid" in query:
            return list(_U8)
        if "GROUP BY v.csign, v.ino_id" in query:
            return ["转|355|期间损益结转|2|1|1|张三"]
        if "FULL OUTER JOIN" in query:
            return [self.oracle]
        return ["2025|6|1"]

    def cell(self, query: str) -> str:
        cells = self.cells(query)
        return cells[0] if cells else ""

    def pick(self, query: str, count: int) -> list[str] | None:
        parts = self.cell(query).split("|")
        return parts if len(parts) == count else None


class _CustomRun(_FakeRun):
    """自定义转账：第 12 期有一张按年取数定义的凭证（结转本年利润）和一张按月的（结转制造费用）。"""

    def __init__(self, reply: tuple[int, dict], yearly: list[str], month: bool = True) -> None:
        super().__init__(reply)
        self.yearly = yearly
        self.month = month

    def cells(self, query: str) -> list[str]:
        if "ROW:' + COALESCE" in query:
            return list(self.yearly)
        if "NOT IN (" in query:
            return ["2025|12|1"] if self.month else []
        if "GROUP BY v.csign, v.ino_id" in query:
            return ["转|10|结转本年利润|2|1|1|张三", "转|11|结转制造费用|2|1|1|张三"]
        return super().cells(query)


def _dry(lines: list[dict]) -> tuple[int, dict]:
    vouchers = [{"sign": "转", "digest": "期间损益结转", "lines": lines}]
    return 200, {"ok": True, "dry_run": True, "mode": "validate", "detail": {"transfer": {"vouchers": vouchers}}}


_GOOD = [
    {"account": "6001", "item": "P1", "debit": 800, "credit": 0},
    {"account": "4103", "debit": 0, "credit": 800.0},
]


class RegistryTests(unittest.TestCase):
    def test_registered_once_with_ops(self) -> None:
        names = known_names()
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
        for spec in gt.gl_transfer_cases():
            self.assertTrue(spec.requires_login and spec.optional and spec.routes and "gl" in spec.tags)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))

    def test_changed_files_select_the_cases(self) -> None:
        catalog = list(known_names())
        for path in ("co/bridge/src/GlTransfer.cs", "co/bridge/src/GlTransferPnl.cs", "api/u8co_api/co_models_gl_transfer.py"):
            picked = select_changed([path], catalog)
            for name in _NAMES:
                self.assertIn(name, picked, path)


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, query in q.gl_transfer_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)

    def test_values_are_whitelisted(self) -> None:
        query = q.lines_sql("2025", gl_key(6, "转", 355))
        self.assertIn("v.iyear = 2025 AND v.iperiod = 6 AND v.csign = N'转' AND v.ino_id = 355", query)
        with self.assertRaises(ValueError):
            q.month_sql("2026; --", q.PNL_SIGN)
        with self.assertRaises(ValueError):
            q.vouchers_sql("2025", "13", q.PNL_SIGN)
        with self.assertRaises(ValueError):
            q.vouchers_sql("2025", "6", "x' OR 1=1")
        with self.assertRaises(ValueError):
            q.safe_digest("结转'; DROP")
        self.assertNotIn("NOT IN", q.month_sql("2026", q.CUSTOM_SIGN))
        self.assertIn("t.cformula LIKE N'%年%'", q.month_sql("2026", q.CUSTOM_SIGN, skip_year=True))
        self.assertEqual(q.safe_digest("结转示例费用分摊"), "结转示例费用分摊")

    def test_last_day(self) -> None:
        self.assertEqual(gt.last_day(2025, 6), "2025-06-30")
        self.assertEqual(gt.last_day(2024, 2), "2024-02-29")


class ReplayTests(unittest.TestCase):
    def test_pnl_parity_passes_and_sends_exclude(self) -> None:
        run = _FakeRun(_dry(_GOOD))
        status, detail = gt.pnl_parity_body(run)  # type: ignore[arg-type]
        self.assertEqual(status, "PASS", detail)
        route, body = run.sent[-1]
        self.assertEqual(route, gt.PNL)
        self.assertEqual(body, {"fiscal_year": 2025, "period": 6, "dry_run": True, "exclude_existing": True, "date": "2025-06-30"})

    def test_pnl_parity_mismatch_fails(self) -> None:
        bad = [dict(_GOOD[0], debit=799.99), dict(_GOOD[1], credit=799.99)]
        with self.assertRaises(gt._Fail):
            gt.pnl_parity_body(_FakeRun(_dry(bad)))  # type: ignore[arg-type]

    def test_test_only_bridge_falls_back_to_sql(self) -> None:
        refused = (403, {"code": gt.TEST_ONLY, "message": "期间损益结转只对配置为测试账套的账套开放"})
        with self.assertRaises(gt._NotApplicable):
            gt.pnl_parity_body(_FakeRun(refused))  # type: ignore[arg-type]
        with self.assertRaises(gt._Fail):
            gt.pnl_parity_body(_FakeRun(refused, oracle="2|1|6001|||||P1|800.00|700.00"))  # type: ignore[arg-type]
        with self.assertRaises(gt._NotApplicable):
            gt.pnl_parity_body(_FakeRun((403, {"code": "feature_disabled"})))  # type: ignore[arg-type]

    def test_custom_parity_skips_year_definitions(self) -> None:
        reply = 200, {"dry_run": True, "detail": {"transfer": {"vouchers": [{"digest": "结转制造费用", "lines": _GOOD}]}}}
        run = _CustomRun(reply, ["结转本年利润"])
        status, detail = gt.custom_parity_body(run)  # type: ignore[arg-type]
        self.assertEqual(status, "PASS", detail)
        self.assertIn("按年取数", detail)
        self.assertEqual(run.sent[-1][1]["period"], 12)
        with self.assertRaises(gt._Fail):
            gt.custom_parity_body(_CustomRun(reply, []))  # type: ignore[arg-type]

    def test_custom_parity_only_year_definitions_is_na(self) -> None:
        reply = 200, {"dry_run": True, "detail": {"transfer": {"vouchers": []}}}
        with self.assertRaises(gt._NotApplicable) as caught:
            gt.custom_parity_body(_CustomRun(reply, ["结转本年利润"], month=False))  # type: ignore[arg-type]
        self.assertIn("按年取数", str(caught.exception))
        with self.assertRaises(gt._NotApplicable):
            gt.custom_parity_body(_CustomRun(reply, ["结转本年利润", "结转制造费用"]))  # type: ignore[arg-type]

    def test_test_only_case(self) -> None:
        refused = (403, {"code": gt.TEST_ONLY})
        self.assertEqual(gt.test_only_body(_FakeRun(refused))[0], "PASS")  # type: ignore[arg-type]
        disabled = (403, {"code": "feature_disabled"})
        self.assertEqual(gt.test_only_body(_FakeRun(disabled))[0], "PASS")  # type: ignore[arg-type]
        with self.assertRaises(gt._NotApplicable):
            gt.test_only_body(_FakeRun((409, {"code": "state_mismatch"})))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
