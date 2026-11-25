"""库存期初结存单用例（stock_opening）的登记、标签、SQL 和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_opening import (
    STOCK_OPENING_OPS,
    stock_opening_cases,
    stock_opening_closed,
    stock_opening_test_only,
    stock_opening_update_refused,
)
from co.e2e.casespec.stock_opening_sql import stock_opening_samples, stock_pick_sql, stock_state_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("stock-opening-test-only", "stock-opening-closed", "stock-opening-update-refused")
_SPEC = CaseSpec("stock-opening-closed", "s")
_STEP = Step("custom")
_SQL = "co.e2e.casespec.stock_opening.run_guarded"
_NOT_TEST = U8CoError(403, "test_account_only", "期初结存单只对配置为测试账套的账套开放")
_DISABLED = U8CoError(403, "feature_disabled", "第二级写入未打开")
_CLOSED = U8CoError(409, "u8_rejected", "库存启用的第一个月已月结,不可以增加删除期初!")
_NO_UPDATE = U8CoError(400, "bad_request", "期初结存单不能修改，请删除后重新录入")
_STATE = "2026-01-01|1|0|12"
_PICK = "01|A001"


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
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag={})


def _rows(*cells: str) -> str:
    return "".join(f"ROW:{cell}\n" for cell in cells)


_MADE = {"ok": True, "type": "stock_opening", "id": 41, "code": "0000000041", "count": 1, "date": "2025-12-31",
         "docs": [{"id": 41, "code": "0000000041", "line": 0, "wh": "01", "inv": "A001", "qty": 1}]}


class StockOpeningCatalogTests(unittest.TestCase):
    def test_cases_registered_with_ops_and_tags(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in stock_opening_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        for name in _NAMES:
            spec = cases[name]
            self.assertEqual(names.count(name), 1, name)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertEqual(set(STOCK_OPENING_OPS), {spec.steps[0].sql_name for spec in cases.values()})
        closed = auto_tags(cases["stock-opening-closed"])
        self.assertLessEqual({"new:stock_opening", "stock", "period:close"}, set(closed))
        self.assertTrue(conflicts(closed, auto_tags(cases["stock-opening-test-only"])))
        self.assertIn("probe", auto_tags(cases["stock-opening-update-refused"]))

    def test_changed_files_pick_the_family(self) -> None:
        catalog = list(known_names())
        for path in (
            "co/bridge/src/StockOpening.cs",
            "co/bridge/src/StockOpeningAdd.cs",
            "co/bridge/src/StockOpeningEai.cs",
            "api/u8co_api/co_models_stock_opening.py",
        ):
            picked = select_changed([path], catalog)
            for name in _NAMES:
                self.assertIn(name, picked, path)


class StockOpeningSqlTests(unittest.TestCase):
    def test_reads_pass_the_guard_and_are_in_samples(self) -> None:
        rows = sample_sql()
        for name, query in stock_opening_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)
            self.assertIn((name, query), rows)

    def test_shapes(self) -> None:
        state = stock_state_sql()
        for part in ("cSysID = N'ST' AND cName = N'dSTStartDate'", "bflag_ST", "m.iperiod > 0", "bflag_IA",
                     "dbo.rdrecord34", "N'34'"):
            self.assertIn(part, state)
        pick = stock_pick_sql()
        for part in ("dbo.Warehouse", "bWhPos", "dbo.Inventory", "bInvBatch", "iGroupType"):
            self.assertIn(part, pick)


class StockOpeningTestOnlyTests(unittest.TestCase):
    def test_passes_on_403_without_sql(self) -> None:
        client = _Replay([_NOT_TEST])
        with mock.patch(_SQL, side_effect=AssertionError("不应查库")):
            status, detail = stock_opening_test_only(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        route, sent = client.calls[0]
        self.assertEqual(route, "/v1/vouchers/verify")
        self.assertEqual((sent["type"], sent["action"], sent["dry_run"]), ("stock_opening", "verify", True))

    def test_passes_on_403_feature_disabled(self) -> None:
        client = _Replay([_DISABLED])
        with mock.patch(_SQL, side_effect=AssertionError("不应查库")):
            status, detail = stock_opening_test_only(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("feature_disabled", detail)

    def test_na_on_test_lane(self) -> None:
        for answer in (U8CoError(404, "not_found", "单据不存在"), U8CoError(409, "state_mismatch", "x"), {"ok": True}):
            with self.subTest(answer=answer):
                self.assertEqual(stock_opening_test_only(_env(_Replay([answer])), _SPEC, _STEP)[0], "N/A")

    def test_fails_on_other_errors_or_wrong_text(self) -> None:
        for answer in (U8CoError(500, "internal", "x"), U8CoError(403, "test_account_only", "期初单据只对……")):
            with self.subTest(answer=answer):
                self.assertEqual(stock_opening_test_only(_env(_Replay([answer])), _SPEC, _STEP)[0], "FAIL")


class StockOpeningUpdateTests(unittest.TestCase):
    def test_passes_on_400(self) -> None:
        client = _Replay([_NO_UPDATE])
        status, detail = stock_opening_update_refused(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[0][0], "/v1/vouchers/update")

    def test_fails_otherwise(self) -> None:
        for answer in ({"ok": True}, U8CoError(400, "bad_request", "不能设置字段 cmemo"), _NOT_TEST):
            with self.subTest(answer=answer):
                self.assertEqual(stock_opening_update_refused(_env(_Replay([answer])), _SPEC, _STEP)[0], "FAIL")


class StockOpeningClosedTests(unittest.TestCase):
    def _run(self, answers: list, rows: list) -> tuple[tuple[str, str], _Replay]:
        client = _Replay(answers)
        with mock.patch(_SQL, side_effect=rows):
            return stock_opening_closed(_env(client), _SPEC, _STEP), client

    def test_passes_when_u8_refuses_and_nothing_is_left(self) -> None:
        (status, detail), client = self._run([_CLOSED], [_rows(_STATE), _rows(_PICK), _rows(_STATE)])
        self.assertEqual(status, "PASS", detail)
        route, sent = client.calls[0]
        self.assertEqual(route, "/v1/vouchers/create")
        self.assertNotIn("dry_run", sent)
        self.assertEqual((sent["head"]["cwhcode"], sent["lines"]), ("01", [{"cinvcode": "A001", "iquantity": 1}]))

    def test_na_cases_do_not_call(self) -> None:
        for rows in ([_rows("|1|0|0")], [_rows("2026-01-01|0|0|3")], [_rows("2026-01-01|1|1|3")],
                     [_rows(_STATE), ""]):
            with self.subTest(rows=rows):
                (status, _detail), client = self._run([], rows)
                self.assertEqual(status, "N/A")
                self.assertEqual(client.calls, [])

    def test_not_test_account_is_na(self) -> None:
        (status, detail), _client = self._run([_NOT_TEST], [_rows(_STATE), _rows(_PICK), _rows(_STATE)])
        self.assertEqual(status, "N/A")
        self.assertIn("testAccounts", detail)

    def test_count_change_fails(self) -> None:
        (status, detail), _client = self._run([_CLOSED], [_rows(_STATE), _rows(_PICK), _rows("2026-01-01|1|0|13")])
        self.assertEqual(status, "FAIL")
        self.assertIn("张数", detail)

    def test_created_fails_and_cleans_up(self) -> None:
        (status, _detail), client = self._run([_MADE], [_rows(_STATE), _rows(_PICK), _rows("2026-01-01|1|0|13")])
        self.assertEqual(status, "FAIL")
        route, sent = client.calls[-1]
        self.assertEqual((route, sent["type"], sent["id"]), ("/v1/vouchers/delete", "stock_opening", 41))

    def test_other_refusal_fails(self) -> None:
        other = U8CoError(409, "u8_rejected", "存货 A001 不存在")
        (status, _detail), _client = self._run([other], [_rows(_STATE), _rows(_PICK), _rows(_STATE)])
        self.assertEqual(status, "FAIL")


if __name__ == "__main__":
    unittest.main()
