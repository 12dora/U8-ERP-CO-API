"""经营管理报表（销售分析、往来账期）只读核对（mgmt_sa）的登记、标签、对照 SQL 和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import mgmt_sa as ms
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.mgmt_sa_sql import arap_sql, mgmt_sa_samples, sales_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("report-mgmt-sales", "report-mgmt-arap-terms")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _sales(items: list[dict], others: dict | None, groups: int) -> dict:
    revenue = sum(i["revenue"] for i in items) + (others or {}).get("revenue", 0)
    cogs = sum(i["cogs"] for i in items) + (others or {}).get("cogs", 0)
    return {"ok": True, "fiscal_year": 2026, "date_from": "2026-01-01", "date_to": "2026-09-30", "top": 200,
            "sa_enabled": True, "items": items, "others": others,
            "totals": {"groups": groups, "revenue": revenue, "cogs": cogs}}


def _partner(code: str, balance: float) -> dict:
    return {"code": code, "balance": balance, "prepaid": 0, "overdue": 0, "aging": [balance, 0, 0],
            "invoice_count": 1, "terms": [{"days": 45, "count": 1, "amount": 10}], "open_notes": 0,
            "max_overdue_days": None, "oldest_overdue_date": None}


class _Replay:
    def __init__(self) -> None:
        self.sales = _sales([{"revenue": 30, "cogs": 10, "period": 1}, {"revenue": 20, "cogs": 5, "period": 2}], None, 2)
        self.arap = {"ok": True, "as_of": "2026-09-28", "enabled": True, "partners": [_partner("C1", 100.0)],
                     "others": None, "totals": {"partners": 1, "balance": 100.0, "open_notes": 0,
                                               "max_overdue_days": None, "oldest_overdue_date": None}}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, _env: object, route: str, body: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(body)))
        if route == ms.SALES:
            return 200, dict(self.sales)
        return 200, dict(self.arap, side=body["side"])


def _run(replay: _Replay, op: str, sql_rows: list[list[str]]) -> tuple[str, str]:
    env = SimpleNamespace(client=object(), bag={}, today="2026-09-28")
    with mock.patch.object(ms, "send", replay), mock.patch.object(ms, "rows", lambda *_a: sql_rows):
        return ms.MGMT_SA_OPS[op](env, _SPEC, _STEP)  # type: ignore[arg-type]


class RegistryTests(unittest.TestCase):
    def test_registered_once_with_ops(self) -> None:
        names = known_names()
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
        for spec in ms.mgmt_sa_cases():
            self.assertTrue(spec.requires_login and spec.optional and spec.routes, spec.name)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name), spec.name)

    def test_tags(self) -> None:
        cases = {spec.name: spec for spec in ms.mgmt_sa_cases()}
        self.assertTrue({"wave:2", "rpt:arap", "rpt:stock"} <= auto_tags(cases["report-mgmt-sales"]))
        self.assertTrue({"wave:2", "rpt:arap"} <= auto_tags(cases["report-mgmt-arap-terms"]))


class SqlTests(unittest.TestCase):
    def test_samples_pass_guard(self) -> None:
        names = [name for name, _sql in sample_sql()]
        for name, text in mgmt_sa_samples():
            self.assertIn(name, names)
            self.assertEqual(sql_block_reason(text), "", name)

    def test_shapes_and_refusals(self) -> None:
        text = sales_sql(("2026-01-01", "2026-10-01"), 2026, (1, 9))
        self.assertIn("s.bSale = 1 AND s.bRdFlag = 0", text)
        self.assertIn("h.cChecker", text)
        self.assertNotIn("cVerifier", text)
        self.assertIn("CONVERT(date, '2026-10-01', 23)", text)
        self.assertIn("N'1122%'", arap_sql("ar", "2026-09-28"))
        self.assertIn("dbo.Ap_Detail", arap_sql("ap", "2026-09-28"))
        with self.assertRaises(ValueError):
            sales_sql(("2026-01-01'--", "2026-10-01"), 2026, (1, 9))
        with self.assertRaises(ValueError):
            sales_sql(("2026-01-01", "2026-10-01"), 2026, (1, 13))
        with self.assertRaises(KeyError):
            arap_sql("both", "2026-09-28")


class SalesTests(unittest.TestCase):
    def test_pass_and_regroup_calls(self) -> None:
        replay = _Replay()
        status, detail = _run(replay, "mgmt_sales_read", [["50.00", "15.00"]])
        self.assertEqual(status, "PASS", detail)
        groups = [body.get("group_by") for route, body in replay.calls]
        self.assertEqual(groups, [["customer", "inventory"], ["period"], []])
        self.assertEqual(replay.calls[0][1]["period_to"], 9)

    def test_sql_mismatch_and_order(self) -> None:
        status, detail = _run(_Replay(), "mgmt_sales_read", [["49.00", "15.00"]])
        self.assertEqual(status, "FAIL")
        self.assertIn("SQL", detail)
        replay = _Replay()
        replay.sales = _sales([{"revenue": 20, "cogs": 5}, {"revenue": 30, "cogs": 10}], None, 2)
        status, detail = _run(replay, "mgmt_sales_read", [["50.00", "15.00"]])
        self.assertEqual(status, "FAIL")
        self.assertIn("降序", detail)

    def test_sa_disabled(self) -> None:
        replay = _Replay()
        replay.sales = dict(_sales([], None, 0), sa_enabled=False)
        self.assertEqual(_run(replay, "mgmt_sales_read", [])[0], "PASS")
        replay.sales = dict(_sales([{"revenue": 1, "cogs": 0}], None, 1), sa_enabled=False)
        self.assertEqual(_run(replay, "mgmt_sales_read", [])[0], "FAIL")


class ArapTests(unittest.TestCase):
    def test_pass_and_na(self) -> None:
        status, detail = _run(_Replay(), "mgmt_arap_read", [["2024-12-01", "100.00", "0.00"]])
        self.assertEqual(status, "PASS", detail)
        replay = _Replay()
        replay.arap = {"ok": True, "as_of": "2026-09-28", "enabled": False, "partners": [], "others": None,
                       "totals": {"partners": 0, "balance": 0, "open_notes": 0,
                                  "max_overdue_days": None, "oldest_overdue_date": None}}
        self.assertEqual(_run(replay, "mgmt_arap_read", [["", "0.00", "0.00"]])[0], "N/A")

    def test_mismatches(self) -> None:
        status, detail = _run(_Replay(), "mgmt_arap_read", [["2024-12-01", "99.00", "0.00"]])
        self.assertEqual(status, "FAIL")
        self.assertIn("余额", detail)
        self.assertEqual(_run(_Replay(), "mgmt_arap_read", [["", "100.00", "0.00"]])[0], "FAIL")
        replay = _Replay()
        replay.arap["partners"] = [dict(_partner("C1", 100.0), aging=[90, 0, 0])]
        status, detail = _run(replay, "mgmt_arap_read", [["2024-12-01", "100.00", "0.00"]])
        self.assertEqual(status, "FAIL")
        self.assertIn("账龄", detail)
        replay = _Replay()
        replay.arap["partners"] = [dict(_partner("C1", 100.0), aging=[90, 10, 0], overdue=10,
                                        max_overdue_days=None, oldest_overdue_date=None)]
        self.assertEqual(_run(replay, "mgmt_arap_read", [["2024-12-01", "100.00", "0.00"]])[0], "PASS")
        replay = _Replay()
        replay.arap["partners"] = [dict(_partner("C1", 100.0), aging=[90, 10, 0], overdue=10,
                                        max_overdue_days=0, oldest_overdue_date="2026-09-28")]
        status, detail = _run(replay, "mgmt_arap_read", [["2024-12-01", "100.00", "0.00"]])
        self.assertEqual(status, "FAIL")
        self.assertIn("正整数", detail)
        replay = _Replay()
        replay.arap["partners"] = [dict(_partner("C1", 100.0), aging=[90, 10, 0], overdue=10,
                                        max_overdue_days=10, oldest_overdue_date="2026-09-01")]
        status, detail = _run(replay, "mgmt_arap_read", [["2024-12-01", "100.00", "0.00"]])
        self.assertEqual(status, "FAIL")
        self.assertIn("报表日期", detail)
        replay = _Replay()
        replay.arap["partners"] = [dict(_partner("C1", 100.0), aging=[90, 10, 0], overdue=10,
                                        max_overdue_days=10, oldest_overdue_date="2026-09-18")]
        replay.arap["totals"] = dict(replay.arap["totals"], max_overdue_days=10, oldest_overdue_date="2026-09-18")
        self.assertEqual(_run(replay, "mgmt_arap_read", [["2024-12-01", "100.00", "0.00"]])[0], "PASS")


if __name__ == "__main__":
    unittest.main()
