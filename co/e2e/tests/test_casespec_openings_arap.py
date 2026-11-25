"""应收应付期初单据用例（openings_arap）的登记、标签、挑数 SQL 和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.openings_arap import (
    OPENINGS_ARAP_OPS,
    openings_arap_cases,
    openings_arap_closed,
    openings_arap_cycle,
    openings_arap_test_only,
)
from co.e2e.casespec.openings_arap_sql import ar_doc_sql, ar_pick_sql, ar_state_sql, openings_arap_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("openings-arap-test-only", "openings-arap-closed", "openings-arap-cycle")
_ROUTE = "/v1/openings/arap"
_SPEC = CaseSpec("openings-arap-cycle", "s")
_STEP = Step("custom")
_SQL = "co.e2e.casespec.openings_arap.run_guarded"
_NOT_TEST = U8CoError(403, "test_account_only", "期初单据只对配置为测试账套的账套开放")
_DISABLED = U8CoError(403, "feature_disabled", "第二级写入未打开")
_CLOSED = U8CoError(409, "state_mismatch", "应收款管理启用的第一个月已经结账，不能修改期初单据")
_OPEN = "2026-01-01|0"
_PICK = "C001|112201"
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
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag={})


def _rows(*cells: str) -> str:
    return "".join(f"ROW:{cell}\n" for cell in cells)


_DRY = {"ok": True, "dry_run": True, "mode": "rollback", "action": "opening_arap_create", "docs": []}
_CREATED = {"ok": True, "type": "ar_bill", "id": 41, "code": "0000000041", "side": "ar", "opening": True,
            "start_date": "2026-01-01", "date": "2025-12-31", "state": {"verified": False}}


def _verified(flag: bool) -> dict:
    return {"ok": True, "type": "ar_bill", "id": 41, "side": "ar", "opening": True, "state": {"verified": flag}}


_DELETED = {"ok": True, "type": "ar_bill", "id": 41, "deleted": True, "side": "ar", "opening": True}
_GOOD = [_DRY, _CREATED, _verified(True), _verified(False), _DELETED]


class OpeningsArapCatalogTests(unittest.TestCase):
    def test_cases_registered_with_ops_and_tags(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in openings_arap_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        for name in _NAMES:
            spec = cases[name]
            self.assertEqual(names.count(name), 1, name)
            self.assertEqual(spec.routes, (_ROUTE,))
            self.assertTrue(spec.requires_login)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
            self.assertIn("period:close", auto_tags(spec))
        ops = {"openings_arap_test_only", "openings_arap_closed", "openings_arap_cycle"}
        self.assertEqual(set(OPENINGS_ARAP_OPS), ops)
        self.assertLessEqual({"new:ar_bill", "w:ar_bill"}, set(auto_tags(cases["openings-arap-cycle"])))

    def test_cycle_conflicts_with_ar_bills_and_month_end(self) -> None:
        cycle = auto_tags(next(spec for spec in openings_arap_cases() if spec.name == "openings-arap-cycle"))
        others = [spec for spec in openings_arap_cases() if spec.name != "openings-arap-cycle"]
        for spec in others:
            self.assertTrue(conflicts(cycle, auto_tags(spec)), spec.name)
        self.assertTrue(conflicts(cycle, frozenset(("rpt:arap",))))

    def test_changed_files_pick_the_family(self) -> None:
        catalog = list(known_names())
        for path in (
            "co/bridge/src/OpeningsArap.cs",
            "co/bridge/src/OpeningsArapReq.cs",
            "co/bridge/src/TestAccountGate.cs",
            "co/client/u8co_openings.py",
            "api/u8co_api/co_models_openings.py",
        ):
            picked = select_changed([path], catalog)
            for name in _NAMES:
                self.assertIn(name, picked, path)


class OpeningsArapSqlTests(unittest.TestCase):
    def test_reads_pass_the_guard_and_are_in_samples(self) -> None:
        rows = sample_sql()
        for name, query in openings_arap_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)
            self.assertIn((name, query), rows)

    def test_shapes(self) -> None:
        self.assertIn("cSysID = N'AR' AND cName = N'dARStartDate'", ar_state_sql())
        self.assertIn("bflag_AR", ar_state_sql())
        pick = ar_pick_sql(2026)
        for part in ("k.iyear = 2026", "k.bcus = 1", "N'AR'", "N'1122%'", "k.bend = 1", "c.dEndDate IS NULL"):
            self.assertIn(part, pick)
        doc = ar_doc_sql(41)
        for part in ("h.Auto_ID = 41", "bStartFlag", "dbo.Ar_Detail", "d.iPeriod = 0"):
            self.assertIn(part, doc)

    def test_rejects_bad_numbers(self) -> None:
        for bad in (0, -1, 2147483648, "41", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ar_doc_sql(bad)  # type: ignore[arg-type]
        for bad in (99, "2026", 10000):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ar_pick_sql(bad)  # type: ignore[arg-type]


class OpeningsArapTestOnlyTests(unittest.TestCase):
    def test_passes_on_403_without_sql(self) -> None:
        client = _Replay([_NOT_TEST])
        with mock.patch(_SQL, side_effect=AssertionError("不应查库")):
            status, detail = openings_arap_test_only(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        route, sent = client.calls[0]
        self.assertEqual(route, _ROUTE)
        self.assertEqual((sent["side"], sent["action"], sent["dry_run"]), ("ar", "verify", True))

    def test_passes_on_403_feature_disabled(self) -> None:
        client = _Replay([_DISABLED])
        with mock.patch(_SQL, side_effect=AssertionError("不应查库")):
            status, detail = openings_arap_test_only(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("feature_disabled", detail)

    def test_na_on_test_lane(self) -> None:
        for answer in (U8CoError(404, "not_found", "单据不存在"), _CLOSED, _verified(True)):
            with self.subTest(answer=answer):
                self.assertEqual(openings_arap_test_only(_env(_Replay([answer])), _SPEC, _STEP)[0], "N/A")

    def test_fails_on_other_errors_or_wrong_text(self) -> None:
        for answer in (
            U8CoError(403, "no_permission", "没有权限"),
            U8CoError(500, "internal", "x"),
            U8CoError(403, "test_account_only", "期初记账只对配置为测试账套的账套开放"),
        ):
            with self.subTest(answer=answer):
                self.assertEqual(openings_arap_test_only(_env(_Replay([answer])), _SPEC, _STEP)[0], "FAIL")


class OpeningsArapClosedTests(unittest.TestCase):
    def test_passes_on_409_first_month_closed(self) -> None:
        client = _Replay([_CLOSED])
        with mock.patch(_SQL, side_effect=[_rows("2026-01-01|1"), _rows(_PICK)]):
            status, detail = openings_arap_closed(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        sent = client.calls[0][1]
        got = (sent["action"], sent["partner"], sent["account"], sent["dry_run"])
        self.assertEqual(got, ("create", "C001", "112201", True))

    def test_uses_placeholders_when_nothing_to_pick(self) -> None:
        client = _Replay([_CLOSED])
        with mock.patch(_SQL, side_effect=[_rows("2026-01-01|1"), ""]):
            self.assertEqual(openings_arap_closed(_env(client), _SPEC, _STEP)[0], "PASS")
        self.assertEqual(client.calls[0][1]["partner"], "C001")

    def test_na_when_open_disabled_or_not_test(self) -> None:
        for cell in (_OPEN, "|0", "x|1"):
            client = _Replay([])
            with self.subTest(cell=cell), mock.patch(_SQL, side_effect=[_rows(cell)]):
                self.assertEqual(openings_arap_closed(_env(client), _SPEC, _STEP)[0], "N/A")
            self.assertEqual(client.calls, [])
        with mock.patch(_SQL, side_effect=[_rows("2026-01-01|1"), _rows(_PICK)]):
            self.assertEqual(openings_arap_closed(_env(_Replay([_NOT_TEST])), _SPEC, _STEP)[0], "N/A")

    def test_fails_on_success_or_other_conflict(self) -> None:
        for answer in (_DRY, U8CoError(409, "state_mismatch", "应收款管理未启用")):
            with self.subTest(answer=answer), mock.patch(_SQL, side_effect=[_rows("2026-01-01|1"), _rows(_PICK)]):
                self.assertEqual(openings_arap_closed(_env(_Replay([answer])), _SPEC, _STEP)[0], "FAIL")


class OpeningsArapCycleTests(unittest.TestCase):
    def _run(self, answers: list, rows: list) -> tuple[tuple[str, str], _Replay]:
        client = _Replay(answers)
        with mock.patch(_SQL, side_effect=rows):
            return openings_arap_cycle(_env(client), _SPEC, _STEP), client

    def test_full_cycle_passes(self) -> None:
        (status, detail), client = self._run(list(_GOOD), [_rows(_OPEN), _rows(_PICK), _rows(_SHAPE), ""])
        self.assertEqual(status, "PASS", detail)
        actions = [(sent["action"], sent.get("dry_run"), sent.get("id")) for _route, sent in client.calls]
        want = [("create", True, None), ("create", None, None), ("verify", None, 41), ("unverify", None, 41),
                ("delete", None, 41)]
        self.assertEqual(actions, want)
        create = client.calls[1][1]
        self.assertEqual((create["side"], create["partner"], create["account"], create["amount"]),
                         ("ar", "C001", "112201", 12.34))

    def test_na_cases_do_not_call(self) -> None:
        for rows in ([_rows("2026-01-01|1")], [_rows("|0")], [_rows(_OPEN), ""]):
            with self.subTest(rows=rows):
                (status, _detail), client = self._run([], rows)
                self.assertEqual(status, "N/A")
                self.assertEqual(client.calls, [])

    def test_not_test_account_is_na(self) -> None:
        (status, detail), client = self._run([_NOT_TEST], [_rows(_OPEN), _rows(_PICK)])
        self.assertEqual(status, "N/A")
        self.assertIn("testAccounts", detail)
        self.assertEqual(len(client.calls), 1)

    def test_wrong_shape_fails_and_cleans_up(self) -> None:
        for shape in ("0|2025-12-31|2025-12-31|1|1", "1|2026-09-28|2025-12-31|1|1", "1|2025-12-31|2025-12-31|1|0",
                      "1|2025-12-31|2025-12-31|0|0"):
            with self.subTest(shape=shape):
                answers = [_DRY, _CREATED, _verified(True)]
                (status, detail), client = self._run(answers, [_rows(_OPEN), _rows(_PICK), _rows(shape)])
                self.assertEqual(status, "FAIL", detail)
                self.assertEqual([sent["action"] for _r, sent in client.calls[-2:]], ["unverify", "delete"])

    def test_bad_create_reply_fails(self) -> None:
        for created in (dict(_CREATED, date="2026-01-01"), dict(_CREATED, opening=None), dict(_CREATED, id="41")):
            with self.subTest(created=created):
                (status, _detail), _client = self._run([_DRY, created], [_rows(_OPEN), _rows(_PICK)])
                self.assertEqual(status, "FAIL")

    def test_dry_run_must_be_a_preview(self) -> None:
        (status, _detail), client = self._run([_CREATED], [_rows(_OPEN), _rows(_PICK)])
        self.assertEqual(status, "FAIL")
        self.assertEqual(len(client.calls), 1)

    def test_still_present_after_delete_fails(self) -> None:
        rows = [_rows(_OPEN), _rows(_PICK), _rows(_SHAPE), _rows(_SHAPE)]
        (status, detail), _client = self._run(list(_GOOD), rows)
        self.assertEqual(status, "FAIL")
        self.assertIn("仍有", detail)


if __name__ == "__main__":
    unittest.main()
