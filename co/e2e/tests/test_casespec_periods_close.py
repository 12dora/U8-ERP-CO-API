"""月末结账用例（periods_close）的登记、标签、挑数 SQL 和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.periods_close import PERIODS_OPS, periods_cases, periods_close_dry, periods_close_refuse_order
from co.e2e.casespec.periods_close_sql import period_flags_sql, periods_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("periods-close-dry", "periods-close-refuse-order", "periods-bad-module")
_ROUTE = "/v1/periods/close"
_SPEC = CaseSpec("periods-close-dry", "s")
_STEP = Step("custom")
_SQL = "co.e2e.casespec.periods_close.run_guarded"
_NOT_TEST = U8CoError(403, "test_account_only", "月末结账只对配置为测试账套的账套开放")
_DISABLED = U8CoError(403, "feature_disabled", "第二级写入未打开")
_ORDER = U8CoError(409, "state_mismatch", "总账 2026 年 10 期：上一期间还没结账（2026 年第一个未结账期间是 9 期）")


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
        got = self.answers.pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: object, today: str = "2026-10-01") -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today=today, bag={})


def _flags(closed_to: int, year: int = 2026) -> str:
    """year 年 1..closed_to 期全部结账，其余未结账；上一年全部结账。"""
    lines = []
    for y in (year - 1, year):
        for p in range(1, 13):
            on = "1" if y < year or p <= closed_to else "0"
            lines.append(f"ROW:{y}|{p}|" + "|".join([on] * 7) + "\n")
    return "".join(lines)


def _dry_reply(periods: list | None = None, **extra: object) -> dict:
    steps = periods if periods is not None else [{"module": "pu", "fiscal_year": 2026, "period": 9},
                                                 {"module": "gl", "fiscal_year": 2026, "period": 9}]
    body = {"ok": True, "dry_run": True, "mode": "rollback", "action": "period_close", "docs": [],
            "detail": {"periods": steps}}
    body.update(extra)
    return body


class PeriodsCatalogTests(unittest.TestCase):
    def test_cases_registered_with_ops_and_tags(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in periods_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
            self.assertEqual(cases[name].routes, (_ROUTE,))
        for name in _NAMES[:2]:
            spec = cases[name]
            self.assertTrue(spec.requires_login)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
            self.assertLessEqual({"period:close", "opening:pu"}, set(auto_tags(spec)))
        self.assertEqual(set(PERIODS_OPS), {"periods_close_dry", "periods_close_refuse_order"})
        bad = cases["periods-bad-module"]
        self.assertFalse(bad.requires_login)
        self.assertEqual(bad.tags, frozenset(("probe",)))
        self.assertEqual((bad.steps[0].status, bad.steps[0].code), (400, "bad_request"))
        self.assertEqual(dict(bad.steps[0].fields)["module"], "xx")

    def test_cases_conflict_with_each_other_and_openings(self) -> None:
        cases = {spec.name: spec for spec in periods_cases()}
        dry, refuse = cases["periods-close-dry"], cases["periods-close-refuse-order"]
        self.assertTrue(conflicts(auto_tags(dry), auto_tags(refuse)))
        self.assertTrue(conflicts(auto_tags(dry), frozenset(("opening:pu",))))

    def test_changed_files_pick_the_periods_family(self) -> None:
        catalog = list(known_names())
        for path in (
            "co/bridge/src/PeriodClose.cs",
            "co/bridge/src/PeriodMend.cs",
            "co/client/u8co_periods.py",
            "api/u8co_api/co_models_periods.py",
            "api/u8co_api/co_routes_periods.py",
        ):
            picked = select_changed([path], catalog)
            for name in _NAMES:
                self.assertIn(name, picked, path)


class PeriodsSqlTests(unittest.TestCase):
    def test_reads_pass_the_guard_and_are_in_samples(self) -> None:
        for name, query in periods_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)
        self.assertIn(("periods-flags", period_flags_sql()), sample_sql())

    def test_reads_every_module_flag_for_periods_one_to_twelve(self) -> None:
        query = period_flags_sql()
        for column in ("bflag_PU", "bflag_SA", "bflag_ST", "bflag_IA", "bflag_AR", "bflag_AP", "ISNULL(m.bflag, 0)"):
            self.assertIn(column, query)
        self.assertIn("m.iperiod BETWEEN 1 AND 12", query)


class PeriodsDryReplayTests(unittest.TestCase):
    def test_dry_run_passes_and_flags_unchanged(self) -> None:
        client = _Replay([_dry_reply()])
        with mock.patch(_SQL, side_effect=[_flags(8), _flags(8)]):
            status, detail = periods_close_dry(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        route, sent = client.calls[0]
        self.assertEqual(route, _ROUTE)
        want = (True, True, 2026, 9, "close")
        self.assertEqual((sent["dry_run"], sent["through"], sent["fiscal_year"], sent["period"], sent["action"]), want)
        self.assertNotIn("module", sent)

    def test_dry_run_fails_when_flags_change_or_reply_wrong(self) -> None:
        with mock.patch(_SQL, side_effect=[_flags(8), _flags(9)]):
            status, detail = periods_close_dry(_env(_Replay([_dry_reply()])), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        self.assertIn("回滚", detail)
        wrong = (
            _dry_reply(dry_run=None),
            _dry_reply(action="opening_post"),
            _dry_reply([]),
            _dry_reply([{"module": "gl", "fiscal_year": 2026, "period": 8}]),
            _dry_reply([{"module": "xx", "fiscal_year": 2026, "period": 9}]),
            {"ok": True, "action": "close", "through": [], "count": 0},
        )
        for reply in wrong:
            with self.subTest(reply=reply), mock.patch(_SQL, side_effect=[_flags(8)]):
                self.assertEqual(periods_close_dry(_env(_Replay([reply])), _SPEC, _STEP)[0], "FAIL")
        other = U8CoError(400, "bad_request", "坏字段")
        with mock.patch(_SQL, side_effect=[_flags(8)]):
            self.assertEqual(periods_close_dry(_env(_Replay([other])), _SPEC, _STEP)[0], "FAIL")

    def test_dry_run_na_cases(self) -> None:
        # 1 月；本月之前都已结账（不调桥）。
        for today, flags in (("2026-01-15", _flags(0)), ("2026-10-01", _flags(9))):
            client = _Replay([])
            with self.subTest(today=today), mock.patch(_SQL, side_effect=[flags]):
                self.assertEqual(periods_close_dry(_env(client, today), _SPEC, _STEP)[0], "N/A")
            self.assertEqual(client.calls, [])
        # 不是测试账套；账套数据不满足结账条件。
        busy = U8CoError(409, "state_mismatch", "库存管理 2026 年 9 期：该月有单据")
        for answer in (_NOT_TEST, busy):
            with self.subTest(answer=answer.code), mock.patch(_SQL, side_effect=[_flags(8)]):
                status, detail = periods_close_dry(_env(_Replay([answer])), _SPEC, _STEP)
            self.assertEqual(status, "N/A", detail)


    def test_dry_run_na_on_feature_disabled(self) -> None:
        # 桥总开关 enableReplicatedWrites 未打开：与不在 testAccounts 里一样记 N/A，只发一次预演。
        client = _Replay([_DISABLED])
        with mock.patch(_SQL, side_effect=[_flags(8)]):
            status, detail = periods_close_dry(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "N/A", detail)
        self.assertIn("feature_disabled", detail)
        self.assertEqual(len(client.calls), 1)
        client = _Replay([_DISABLED])
        with mock.patch(_SQL, side_effect=[_flags(8)]):
            self.assertEqual(periods_close_refuse_order(_env(client), _SPEC, _STEP)[0], "N/A")
        self.assertEqual(len(client.calls), 1)


class PeriodsRefuseReplayTests(unittest.TestCase):
    def test_refuse_passes_on_two_409(self) -> None:
        client = _Replay([_ORDER, _ORDER])
        with mock.patch(_SQL, side_effect=[_flags(8), _flags(8)]):
            status, detail = periods_close_refuse_order(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        first, second = client.calls[0][1], client.calls[1][1]
        self.assertEqual((first["module"], first["period"], first["dry_run"]), ("gl", 10, True))
        self.assertNotIn("dry_run", second)
        self.assertNotIn("through", second)

    def test_refuse_fails_on_success_other_message_or_changed_flags(self) -> None:
        ok = {"ok": True, "module": "gl", "action": "close", "fiscal_year": 2026, "period": 10, "closed": True}
        other = U8CoError(409, "state_mismatch", "总账 2026 年 10 期：请先做期间损益结转")
        for answers in ([ok], [other], [_ORDER, ok]):
            with self.subTest(answers=answers), mock.patch(_SQL, side_effect=[_flags(8)]):
                self.assertEqual(periods_close_refuse_order(_env(_Replay(answers)), _SPEC, _STEP)[0], "FAIL")
        with mock.patch(_SQL, side_effect=[_flags(8), _flags(9)]):
            status, detail = periods_close_refuse_order(_env(_Replay([_ORDER, _ORDER])), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        self.assertIn("变了", detail)

    def test_refuse_na_cases(self) -> None:
        for flags in (_flags(12), _flags(11)):
            client = _Replay([])
            with mock.patch(_SQL, side_effect=[flags]):
                self.assertEqual(periods_close_refuse_order(_env(client), _SPEC, _STEP)[0], "N/A")
            self.assertEqual(client.calls, [])
        client = _Replay([_NOT_TEST])
        with mock.patch(_SQL, side_effect=[_flags(8)]):
            self.assertEqual(periods_close_refuse_order(_env(client), _SPEC, _STEP)[0], "N/A")
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
