"""存货核算期初记账用例（ia_opening）：登记、标签、分片、SQL 形状、纯函数和回放核对。不连接服务器。

重点核对安全设计：启用年度已结账的账套只预演、不真记账；取消必成功的账套真记账后取消；真记账后取消失败时，
清理先再取消一次，再删本用例的期初结存单，最后改回库存月结标志。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import ia_opening as iaop
from co.e2e.casespec import openings_live as live
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.ia_opening_sql import ia_opening_samples, summary_inv_sql, verified_inv_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "openings-ia-post-live"
_SPEC = CaseSpec(_NAME, "s")
_STEP = Step("custom")
_ROUTE = "/v1/openings/post"
_NOT_TEST = U8CoError(403, "test_account_only", "期初记账只对配置为测试账套的账套开放")
_NOT_POSTED = U8CoError(409, "state_mismatch", "存货核算期初未记账")
_CREATED = {"ok": True, "count": 1, "docs": [{"id": 900000051}]}
_OK = {"ok": True}
_STATE = "N'dIAStartDate'"
_PICK = "SELECT TOP (1) N'ROW:' + w.code"
_CLOSED = "ORDER BY m.iyear, m.iperiod"
_OPEN = "SET m.bflag_ST = 0"
_BACK = "SET m.bflag_ST = 1"
_VERIFIED = "SUM(b.iQuantity)"
_SUMMARY = "SUM(a.iNum)"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers.pop(0) if self.answers else _OK
        if isinstance(got, BaseException):
            raise got
        return got


class _Sql:
    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]
        self.hits: list[str] = []

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                self.hits.append(key)
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])


def _rows(*cells: str) -> str:
    return "".join(f"ROW:{cell}\n" for cell in cells)


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2024", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2024", today="2026-09-28", bag={})


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(iaop, "run_guarded", sql), mock.patch.object(live, "run_guarded", sql):
        return iaop.openings_ia_post_live(_env(client), _SPEC, _STEP)


def _dry(verified: int, rows: int = 2) -> dict:
    counts = {"st34_verified": verified, "subsidiary_m0_34": verified, "summary_m0": rows, "gl_mend_p0": 1,
              "fifo_lines_qcass_skipped": 0, "gl_mend_lt_start": 1}
    opening = {"module": "ia", "action": "post", "posted": True, "opening_year": 2024, "start_date": "2024-01-01",
               "counts": counts}
    return {"ok": True, "dry_run": True, "mode": "rollback", "action": "opening_post", "detail": {"opening": opening}}


def _state(start: str, closed: int, daily: int, verified: int, traces: str = "0") -> str:
    return _rows(f"{start}|{start}|0|{closed}|{daily}|0|{traces}|{verified}")


def _routes(client: _Replay) -> list[tuple[str, str]]:
    return [(route, str(fields.get("action"))) for route, fields in client.calls]


class CatalogTests(unittest.TestCase):
    def test_registered_exclusive(self) -> None:
        cases = iaop.ia_opening_cases()
        self.assertEqual([spec.name for spec in cases], [_NAME])
        spec = cases[0]
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(spec.exclusive)
        self.assertTrue(spec.requires_login)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        tags = auto_tags(spec)
        for tag in ("period:close", "ia:post", "opening:pu", "new:stock_opening"):
            self.assertIn(tag, tags)
        self.assertEqual(set(iaop.IA_OPENING_OPS), {spec.steps[0].sql_name})

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in ia_opening_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)
            self.assertIn((name, query), rows)

    def test_sql_only_takes_checked_values(self) -> None:
        self.assertIn("b.cInvCode = N'INV-001'", verified_inv_sql("INV-001"))
        self.assertIn("a.iYear = 2024 AND a.iMonth = 0", summary_inv_sql(2024, "INV-001"))
        for bad in ("x'y", "", "a b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                verified_inv_sql(bad)
        with self.assertRaises(ValueError):
            summary_inv_sql(24, "INV-001")


class PureTests(unittest.TestCase):
    def _state(self, **kw: object) -> iaop.IaState:
        base = dict(start="2024-01-01", st_start="2024-01-01", posted="0", closed=0, daily=0, opening_rows=0,
                    traces="0", verified=0)
        base.update(kw)
        return iaop.IaState(**base)

    def test_na_reason(self) -> None:
        self.assertEqual(iaop.na_reason(self._state()), "")
        self.assertIn("存货核算未启用", iaop.na_reason(self._state(start="")))
        self.assertIn("库存管理未启用", iaop.na_reason(self._state(st_start="")))
        self.assertIn("启用日期不同", iaop.na_reason(self._state(st_start="2024-02-01")))
        self.assertIn("已期初记账", iaop.na_reason(self._state(posted="1")))
        self.assertIn("读不到", iaop.na_reason(self._state(closed=-1)))

    def test_can_unpost_only_on_pristine_years(self) -> None:
        self.assertTrue(iaop.can_unpost(self._state()))
        for kw in ({"closed": 1}, {"daily": 3}, {"opening_rows": 2}, {"traces": "1"}):
            with self.subTest(kw=kw):
                self.assertFalse(iaop.can_unpost(self._state(**kw)))

    def test_check_dry(self) -> None:
        self.assertEqual(iaop.check_dry(_dry(3), 3), "")
        self.assertIn("计数不对", iaop.check_dry(_dry(2), 3))
        self.assertIn("计数不对", iaop.check_dry(_dry(3, rows=0), 3))
        bad = _dry(3)
        bad["detail"]["opening"]["posted"] = False
        self.assertIn("响应不对", iaop.check_dry(bad, 3))
        self.assertIn("响应不对", iaop.check_dry({"ok": True}, 3))


class ReplayTests(unittest.TestCase):
    def test_not_test_account_is_na_without_writes(self) -> None:
        client = _Replay([_NOT_TEST])
        sql = _Sql([(_STATE, [_state("2024-01-01", 33, 500, 2)])])
        self.assertEqual(_run(client, sql)[0], "N/A")
        self.assertEqual(sql.hits, [_STATE])
        self.assertEqual(client.calls[0][1]["date"], "2024-01-01")
        self.assertIs(client.calls[0][1]["dry_run"], True)

    def test_closed_year_only_previews_then_cleans_and_restores(self) -> None:
        client = _Replay([_NOT_POSTED, _CREATED, _OK, _dry(3), _NOT_POSTED, _OK, _OK])
        sql = _Sql([
            (_STATE, [_state("2024-01-01", 33, 500, 2), _state("2024-01-01", 33, 500, 3),
                      _state("2024-01-01", 33, 500, 3)]),
            (_PICK, [_rows("06|INV-001")]),
            (_CLOSED, [_rows("2024|1", "2024|2"), _rows("2024|1", "2024|2")]),
            (_OPEN, [_rows("2")]),
            (_BACK, [_rows("2")]),
            (_VERIFIED, [_rows("1|4.00|12.00")]),
        ])
        status, detail = _run(client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("只预演", detail)
        posts = [fields for route, fields in client.calls if route == _ROUTE]
        self.assertEqual([(f["action"], f.get("dry_run")) for f in posts], [("unpost", True), ("post", True), ("unpost", None)])
        self.assertTrue(all(f["module"] == "ia" and f["date"] == "2024-01-01" for f in posts))
        self.assertNotIn(_SUMMARY, sql.hits)
        self.assertLess(sql.hits.index(_OPEN), sql.hits.index(_BACK))
        self.assertEqual(_routes(client)[-2:], [("/v1/vouchers/verify", "unverify"), ("/v1/vouchers/delete", "None")])

    def test_pristine_year_posts_checks_and_unposts(self) -> None:
        posted = {"ok": True, "module": "ia", "posted": True, "counts": {"summary_m0": 1, "gl_mend_p0": 1}}
        unposted = {"ok": True, "module": "ia", "posted": False, "counts": {"summary_m0_left": 0, "gl_mend_p0": 0}}
        client = _Replay([_NOT_POSTED, _CREATED, _OK, _dry(1, rows=1), posted, unposted, _OK, _OK])
        sql = _Sql([
            (_STATE, [_state("2026-01-01", 0, 0, 0), _state("2026-01-01", 0, 0, 1), _state("2026-01-01", 0, 0, 1)]),
            (_PICK, [_rows("06|INV-001")]),
            (_CLOSED, [""]),
            (_VERIFIED, [_rows("1|4.00|12.00")]),
            (_SUMMARY, [_rows("1|4.00|12.00|1|1"), _rows("0|||0|0")]),
        ])
        status, detail = _run(client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("真记账", detail)
        self.assertEqual(sql.hits.count(_SUMMARY), 2)
        self.assertNotIn(_OPEN, sql.hits)

    def test_unpost_failure_is_retried_in_cleanup_before_deleting(self) -> None:
        posted = {"ok": True, "module": "ia", "posted": True, "counts": {"summary_m0": 1}}
        broken = U8CoError(500, "internal", "存货核算脚本出错")
        unposted = {"ok": True, "posted": False}
        client = _Replay([_NOT_POSTED, _CREATED, _OK, _dry(1, rows=1), posted, broken, unposted, _OK, _OK])
        sql = _Sql([
            (_STATE, [_state("2026-01-01", 0, 0, 0), _state("2026-01-01", 0, 0, 1), _state("2026-01-01", 0, 0, 1)]),
            (_PICK, [_rows("06|INV-001")]),
            (_CLOSED, [""]),
            (_VERIFIED, [_rows("1|4.00|12.00")]),
            (_SUMMARY, [_rows("1|4.00|12.00|1|1")]),
        ])
        status, detail = _run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("取消存货核算期初记账", detail)
        tail = _routes(client)[-4:]
        self.assertEqual(tail, [(_ROUTE, "unpost"), (_ROUTE, "unpost"), ("/v1/vouchers/verify", "unverify"),
                                ("/v1/vouchers/delete", "None")])

    def test_fifo_refusal_is_na_and_still_cleans(self) -> None:
        fifo = U8CoError(409, "state_mismatch", "有 1 行期初在先进先出 / 后进先出计价的仓库或存货上，接口暂不支持")
        client = _Replay([_NOT_POSTED, _CREATED, _OK, fifo, _OK, _OK])
        sql = _Sql([
            (_STATE, [_state("2026-01-01", 0, 0, 0), _state("2026-01-01", 0, 0, 1)]),
            (_PICK, [_rows("06|INV-001")]),
            (_CLOSED, [""]),
            (_VERIFIED, [_rows("1|4.00|12.00")]),
        ])
        status, detail = _run(client, sql)
        self.assertEqual(status, "N/A", detail)
        self.assertEqual(_routes(client)[-2:], [("/v1/vouchers/verify", "unverify"), ("/v1/vouchers/delete", "None")])


if __name__ == "__main__":
    unittest.main()
