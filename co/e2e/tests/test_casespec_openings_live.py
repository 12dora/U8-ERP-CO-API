"""已结账月份临时打开后的期初一整轮（openings_live）：登记、标签、SQL 形状和回放核对。不连接服务器。

重点核对恢复设计：结账标志清掉之后，无论步骤成败、抛不抛异常，都先删本用例的单据、再按同一张清单改回标志。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import openings_arap as arap
from co.e2e.casespec import openings_live as live
from co.e2e.casespec import stock_opening as stock
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.openings_arap_sql import doc_sql, pick_sql, state_sql
from co.e2e.casespec.openings_live_sql import (
    arap_flag_sql,
    opening_doc_sql,
    openings_live_samples,
    parse_periods,
    st_flag_sql,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("openings-arap-live-cycle", "openings-arap-live-cycle-ap", "stock-opening-live-cycle")
_SPEC = CaseSpec("openings-arap-live-cycle", "s")
_STEP = Step("custom")
_ROUTE = "/v1/openings/arap"
_NOT_TEST = U8CoError(403, "test_account_only", "期初单据只对配置为测试账套的账套开放")
_CLOSED = U8CoError(409, "state_mismatch", "应收款管理启用的第一个月已经结账，不能修改期初单据")
_SHAPE = "1|2025-12-31|2025-12-31|1|1"


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
        got = self.answers.pop(0) if self.answers else {"ok": True}
        if isinstance(got, BaseException):
            raise got
        return got


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列）；记下每条命中的键，供核对清理、恢复的先后。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]
        self.hits: list[str] = []

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                self.hits.append(key)
                got = rows.pop(0)
                if isinstance(got, BaseException):
                    raise got
                return got
        raise AssertionError("没有准备回答：" + query[:120])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag={})


def _rows(*cells: str) -> str:
    return "".join(f"ROW:{cell}\n" for cell in cells)


def _run(op, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(live, "run_guarded", sql), mock.patch.object(arap, "run_guarded", sql), \
            mock.patch.object(stock, "run_guarded", sql):
        return op(_env(client), _SPEC, _STEP)


# ---- 应收 / 应付 ----

_AR_OPEN = "UPDATE dbo.GL_mend SET bflag_AR = 0"
_AR_BACK = "UPDATE dbo.GL_mend SET bflag_AR = 1"
_AP_OPEN = "UPDATE dbo.GL_mend SET bflag_AP = 0"
_DRY = {"ok": True, "dry_run": True, "mode": "rollback"}


def _created(side: str) -> dict:
    kind = side + "_bill"
    return {"ok": True, "type": kind, "id": 41, "side": side, "opening": True, "start_date": "2026-01-01",
            "date": "2025-12-31"}


def _verified(flag: bool) -> dict:
    return {"ok": True, "id": 41, "opening": True, "state": {"verified": flag}}


_DELETED = {"ok": True, "id": 41, "deleted": True, "opening": True}


def _good(side: str) -> list:
    return [_CLOSED, _DRY, _created(side), _verified(True), _verified(False), _DELETED]


def _ar_sql(state: list[str], opened: str = "1", shape: str = _SHAPE, back: str = "1") -> _Sql:
    return _Sql([
        ("dARStartDate", [_rows(cell) for cell in state]),
        (_AR_OPEN, [_rows(opened)]),
        (_AR_BACK, [_rows(back)]),
        ("dbo.Customer", [_rows("C001|112201")]),
        ("Ap_Vouch AS h", [_rows(shape), ""]),
    ])


class LiveCatalogTests(unittest.TestCase):
    def test_cases_registered_and_exclusive(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in live.openings_live_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        for name, spec in cases.items():
            self.assertEqual(names.count(name), 1, name)
            self.assertTrue(spec.exclusive, name)
            self.assertTrue(spec.requires_login)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
            self.assertIn("period:close", auto_tags(spec))
        self.assertEqual(set(live.OPENINGS_LIVE_OPS), {spec.steps[0].sql_name for spec in cases.values()})

    def test_conflicts_with_other_openings_cases(self) -> None:
        mine = auto_tags(live.openings_live_cases()[0])
        for spec in arap.openings_arap_cases() + stock.stock_opening_cases()[:2]:
            self.assertTrue(conflicts(mine, auto_tags(spec)), spec.name)


class LiveSqlTests(unittest.TestCase):
    def test_samples_pass_the_guard_and_are_in_samples(self) -> None:
        rows = sample_sql()
        for name, query in openings_live_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)
            self.assertIn((name, query), rows)

    def test_flag_writes_touch_only_named_rows(self) -> None:
        sql = arap_flag_sql("ap", 2026, 1, 0)
        for part in ("SET bflag_AP = 0", "iyear = 2026 AND iperiod = 1", "ISNULL(bflag_AP, 0) = 1", "@@ROWCOUNT"):
            self.assertIn(part, sql)
        sql = st_flag_sql([(2025, 12), (2026, 1)], 1)
        for part in ("m.bflag_ST = 1", "(VALUES (2025, 12), (2026, 1))", "ISNULL(m.bflag_ST, 0) = 0", "m.iperiod > 0"):
            self.assertIn(part, sql)
        self.assertIn("h.ID = 900000041", opening_doc_sql(900000041))

    def test_side_sql_for_ap(self) -> None:
        self.assertIn("cSysID = N'AP' AND cName = N'dAPStartDate'", state_sql("ap"))
        pick = pick_sql("ap", 2026)
        for part in ("dbo.Vendor", "c.cVenCode", "k.bsup = 1", "N'AP'", "N'2202%'"):
            self.assertIn(part, pick)
        self.assertIn("dbo.Ap_Detail", doc_sql("ap", 41))
        self.assertIn("h.cFlag = N'AP'", doc_sql("ap", 41))

    def test_rejects_bad_values(self) -> None:
        bad_calls = (
            lambda: arap_flag_sql("gl", 2026, 1, 0),
            lambda: arap_flag_sql("ar", 2026, 1, 2),
            lambda: arap_flag_sql("ar", 99, 1, 0),
            lambda: st_flag_sql([], 0),
            lambda: st_flag_sql([(2026, 300)], 0),
            lambda: st_flag_sql([(2026, 1)] * 241, 0),
            lambda: opening_doc_sql(0),
            lambda: state_sql("xx"),
            lambda: parse_periods(["2026|x"]),
        )
        for call in bad_calls:
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(parse_periods(["2026|1", " 2026|12 "]), [(2026, 1), (2026, 12)])


class ArapLiveTests(unittest.TestCase):
    def test_closed_start_month_opened_cycled_and_restored(self) -> None:
        client = _Replay(_good("ar"))
        sql = _ar_sql(["2026-01-01|1", "2026-01-01|1"])
        status, detail = _run(live.openings_arap_live_cycle, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("临时打开", detail)
        self.assertEqual(sql.hits[1], _AR_OPEN)
        self.assertEqual(sql.hits[-2:], [_AR_BACK, "dARStartDate"])
        actions = [sent["action"] for _route, sent in client.calls]
        self.assertEqual(actions, ["verify", "create", "create", "verify", "unverify", "delete"])
        self.assertTrue(client.calls[0][1]["dry_run"])

    def test_cycle_failure_still_restores(self) -> None:
        client = _Replay(_good("ar"))
        sql = _ar_sql(["2026-01-01|1", "2026-01-01|1"], shape="0|2025-12-31|2025-12-31|1|1")
        status, detail = _run(live.openings_arap_live_cycle, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn(_AR_BACK, sql.hits)
        self.assertEqual([sent["action"] for _r, sent in client.calls[-2:]], ["unverify", "delete"])
        self.assertNotIn("恢复", detail)

    def test_exception_mid_cycle_still_restores(self) -> None:
        client = _Replay([_CLOSED, _DRY, OSError("连接断了")])
        sql = _ar_sql(["2026-01-01|1", "2026-01-01|1"])
        status, detail = _run(live.openings_arap_live_cycle, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("连接断了", detail)
        self.assertIn(_AR_BACK, sql.hits)

    def test_open_failure_restores_and_fails(self) -> None:
        client = _Replay([_CLOSED])
        sql = _ar_sql(["2026-01-01|1", "2026-01-01|1"], opened="0", back="0")
        status, detail = _run(live.openings_arap_live_cycle, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("应改 1 行", detail)
        self.assertIn(_AR_BACK, sql.hits)
        self.assertEqual(len(client.calls), 1)

    def test_restore_not_effective_fails(self) -> None:
        client = _Replay(_good("ar"))
        sql = _ar_sql(["2026-01-01|1", "2026-01-01|0"])
        status, detail = _run(live.openings_arap_live_cycle, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("仍是 0", detail)

    def test_not_test_account_never_writes(self) -> None:
        client = _Replay([_NOT_TEST])
        sql = _ar_sql(["2026-01-01|1"])
        status, detail = _run(live.openings_arap_live_cycle, client, sql)
        self.assertEqual(status, "N/A")
        self.assertIn("testAccounts", detail)
        self.assertEqual(sql.hits, ["dARStartDate"])

    def test_ar_open_month_is_left_to_the_plain_cycle(self) -> None:
        for cell in ("2026-01-01|0", "|0"):
            client = _Replay([])
            sql = _ar_sql([cell])
            with self.subTest(cell=cell):
                self.assertEqual(_run(live.openings_arap_live_cycle, client, sql)[0], "N/A")
                self.assertEqual(client.calls, [])

    def test_ap_open_month_runs_cycle_without_flags(self) -> None:
        client = _Replay(_good("ap")[1:])
        sql = _Sql([
            ("dAPStartDate", [_rows("2026-01-01|0")]),
            ("dbo.Vendor", [_rows("S001|220201")]),
            ("Ap_Vouch AS h", [_rows(_SHAPE), ""]),
        ])
        status, detail = _run(live.openings_arap_live_cycle_ap, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("期初应付单", detail)
        create = client.calls[1][1]
        self.assertEqual((create["side"], create["partner"], create["account"]), ("ap", "S001", "220201"))
        self.assertNotIn(_AP_OPEN, sql.hits)

    def test_ap_closed_month_flips_bflag_ap(self) -> None:
        client = _Replay(_good("ap"))
        sql = _Sql([
            ("dAPStartDate", [_rows("2026-01-01|1"), _rows("2026-01-01|1")]),
            (_AP_OPEN, [_rows("1")]),
            ("UPDATE dbo.GL_mend SET bflag_AP = 1", [_rows("1")]),
            ("dbo.Vendor", [_rows("S001|220201")]),
            ("Ap_Vouch AS h", [_rows(_SHAPE), ""]),
        ])
        status, detail = _run(live.openings_arap_live_cycle_ap, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[0], (_ROUTE, client.calls[0][1]))
        self.assertEqual(client.calls[0][1]["side"], "ap")


# ---- 库存期初结存单 ----

_ST_STATE = "dSTStartDate"
_ST_CLOSED = "ORDER BY m.iyear, m.iperiod"
_ST_OPEN = "UPDATE m SET m.bflag_ST = 0"
_ST_BACK = "UPDATE m SET m.bflag_ST = 1"
_DOC = "rdrecord34 AS h WHERE h.ID"
_IDS = (900000041, 900000042)
_MADE = {"ok": True, "type": "stock_opening", "id": _IDS[0], "count": 2,
         "docs": [{"id": _IDS[0], "code": "-41"}, {"id": _IDS[1], "code": "-42"}]}


def _doc(doc_id: int, veri: str = "") -> str:
    return _rows(f"{doc_id}|-{doc_id - 900000000}|2025-12-31|{veri}|1")


def _stock_sql(closed: list[str], docs: list[str] | None = None, counts: tuple = ("12", "14", "12")) -> _Sql:
    states = [_rows(f"2026-01-01|{1 if closed else 0}|0|{counts[0]}")] + [_rows(f"2026-01-01|0|0|{n}") for n in counts[1:]]
    default_docs = [_doc(_IDS[0]), _doc(_IDS[1]), _doc(_IDS[0], "2025-12-31"), _doc(_IDS[0], "2025-12-31"), "", ""]
    return _Sql([
        (_ST_STATE, states),
        ("TOP (2)", [_rows("01|A001", "01|A002")]),
        (_ST_CLOSED, [_rows(*closed), _rows(*closed)]),
        (_ST_OPEN, [_rows(str(len(closed)))]),
        (_ST_BACK, [_rows(str(len(closed)))]),
        (_DOC, default_docs if docs is None else docs),
    ])


_REFUSED = U8CoError(409, "state_mismatch", "单据已审核，不能删除")


def _stock_good() -> list:
    return [U8CoError(404, "not_found", "单据不存在"), _MADE, {"ok": True}, _REFUSED, {"ok": True},
            {"ok": True, "deleted": True}, {"ok": True, "deleted": True}]


class StockLiveTests(unittest.TestCase):
    def test_full_cycle_with_flags_cleared_and_restored(self) -> None:
        client = _Replay(_stock_good())
        sql = _stock_sql(["2026|1", "2026|2"])
        status, detail = _run(live.stock_opening_live_cycle, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("临时清 2 个", detail)
        self.assertLess(sql.hits.index(_ST_OPEN), sql.hits.index(_DOC))
        self.assertEqual(sql.hits[-2:], [_ST_BACK, _ST_CLOSED])
        create = client.calls[1][1]
        self.assertEqual(create["lines"], [{"cinvcode": "A001", "iquantity": 1}, {"cinvcode": "A002", "iquantity": 1}])
        steps = [(route.rsplit("/", 1)[1], sent.get("action"), sent.get("id")) for route, sent in client.calls[2:]]
        self.assertEqual(steps, [
            ("verify", "verify", _IDS[0]), ("delete", None, _IDS[0]), ("verify", "unverify", _IDS[0]),
            ("delete", None, _IDS[0]), ("delete", None, _IDS[1]),
        ])

    def test_no_closed_months_runs_without_writes(self) -> None:
        client = _Replay(_stock_good())
        sql = _stock_sql([])
        status, detail = _run(live.stock_opening_live_cycle, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertNotIn(_ST_OPEN, sql.hits)
        self.assertNotIn(_ST_BACK, sql.hits)

    def test_failure_cleans_docs_before_restoring_flags(self) -> None:
        answers = [U8CoError(404, "not_found", "x"), _MADE, {"ok": True}, {"ok": True, "deleted": True}]
        client = _Replay(answers)
        sql = _stock_sql(["2026|1"])
        status, detail = _run(live.stock_opening_live_cycle, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("409", detail)
        cleanup = [(route.rsplit("/", 1)[1], sent.get("action"), sent.get("id")) for route, sent in client.calls[4:]]
        self.assertEqual(cleanup, [("verify", "unverify", _IDS[0]), ("delete", None, _IDS[0]),
                                   ("verify", "unverify", _IDS[1]), ("delete", None, _IDS[1])])
        self.assertEqual(sql.hits[-2:], [_ST_BACK, _ST_CLOSED])

    def test_wrong_code_fails_and_restores(self) -> None:
        client = _Replay(_stock_good()[:2])
        sql = _stock_sql(["2026|1"], docs=[_rows(f"{_IDS[0]}|0000000001|2025-12-31||1")])
        status, detail = _run(live.stock_opening_live_cycle, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("单号", detail)
        self.assertIn(_ST_BACK, sql.hits)

    def test_restore_leaving_open_month_fails(self) -> None:
        client = _Replay(_stock_good())
        sql = _stock_sql(["2026|1", "2026|2"])
        sql.answers[2] = (_ST_CLOSED, [_rows("2026|1", "2026|2"), _rows("2026|1")])
        status, detail = _run(live.stock_opening_live_cycle, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("(2026, 2)", detail)

    def test_na_cases_do_not_write(self) -> None:
        for state, answers in (("|1|0|12", []), ("2026-01-01|1|1|12", []),
                               ("2026-01-01|1|0|12", [U8CoError(403, "test_account_only", "x")])):
            client = _Replay(answers)
            sql = _Sql([(_ST_STATE, [_rows(state)])])
            with self.subTest(state=state):
                self.assertEqual(_run(live.stock_opening_live_cycle, client, sql)[0], "N/A")
                self.assertEqual(sql.hits, [_ST_STATE])

    def test_one_inventory_is_na(self) -> None:
        client = _Replay([U8CoError(404, "not_found", "x")])
        sql = _Sql([(_ST_STATE, [_rows("2026-01-01|1|0|12")]), ("TOP (2)", [_rows("01|A001")])])
        self.assertEqual(_run(live.stock_opening_live_cycle, client, sql)[0], "N/A")


if __name__ == "__main__":
    unittest.main()
