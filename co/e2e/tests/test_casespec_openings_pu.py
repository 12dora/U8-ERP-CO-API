"""采购期初记账用例（openings_pu）的登记、标签、挑数 SQL 和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.openings_pu import (
    OPENINGS_OPS,
    openings_cases,
    openings_post_dry,
    openings_post_test_only,
    openings_refuse_posted,
)
from co.e2e.casespec.openings_pu_sql import openings_samples, pu_opening_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("openings-post-dry", "openings-post-refuse-posted", "openings-post-test-only", "openings-bad-module")
_ROUTE = "/v1/openings/post"
_SPEC = CaseSpec("openings-post-dry", "s")
_STEP = Step("custom")
_SQL = "co.e2e.casespec.openings_pu.run_guarded"
_REFUSED = U8CoError(409, "state_mismatch", "采购期初已记账")
_NOT_TEST = U8CoError(403, "test_account_only", "期初记账只对配置为测试账套的账套开放")
_DISABLED = U8CoError(403, "feature_disabled", "第二级写入未打开")


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


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag={})


def _rows(*cells: str) -> str:
    return "".join(f"ROW:{cell}\n" for cell in cells)


def _dry_reply(opening: dict | None = None, **extra: object) -> dict:
    state = {"module": "pu", "posted": True, "opening_year": 2024, "start_date": "2024-01-01"}
    state.update(opening or {})
    body = {"ok": True, "dry_run": True, "mode": "rollback", "action": "opening_post", "docs": [],
            "detail": {"opening": state}}
    body.update(extra)
    return body


class OpeningsCatalogTests(unittest.TestCase):
    def test_cases_registered_with_ops_and_tags(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in openings_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
            self.assertEqual(cases[name].routes, (_ROUTE,))
        for name in _NAMES[:3]:
            spec = cases[name]
            self.assertTrue(spec.requires_login)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
            self.assertLessEqual({"opening:pu", "chain:pu"}, set(auto_tags(spec)))
        self.assertEqual(set(OPENINGS_OPS), {"openings_post_dry", "openings_refuse_posted", "openings_post_test_only"})
        bad = cases["openings-bad-module"]
        self.assertFalse(bad.requires_login)
        self.assertEqual(bad.tags, frozenset(("probe",)))
        self.assertEqual((bad.steps[0].status, bad.steps[0].code), (400, "bad_request"))
        self.assertEqual(dict(bad.steps[0].fields)["module"], "st")

    def test_openings_cases_conflict_with_each_other(self) -> None:
        cases = {spec.name: spec for spec in openings_cases()}
        dry, refuse = cases["openings-post-dry"], cases["openings-post-refuse-posted"]
        self.assertTrue(conflicts(auto_tags(dry), auto_tags(refuse)))
        self.assertTrue(conflicts(auto_tags(cases["openings-post-test-only"]), auto_tags(dry)))

    def test_changed_files_pick_the_openings_family(self) -> None:
        catalog = list(known_names())
        for path in (
            "co/bridge/src/OpeningPostReq.cs",
            "co/bridge/src/TestAccountGate.cs",
            "co/client/u8co_openings.py",
            "api/u8co_api/co_models_openings.py",
            "api/u8co_api/co_routes_openings.py",
        ):
            picked = select_changed([path], catalog)
            for name in _NAMES:
                self.assertIn(name, picked, path)


class OpeningsSqlTests(unittest.TestCase):
    def test_reads_pass_the_guard_and_are_in_samples(self) -> None:
        for name, query in openings_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)
        self.assertIn(("openings-pu-state", pu_opening_sql()), sample_sql())

    def test_reads_the_pu_start_and_period_zero_flag(self) -> None:
        query = pu_opening_sql()
        self.assertIn("cSysID = N'PU' AND cName = N'dPUStartDate'", query)
        self.assertIn("bflag_PU", query)
        self.assertIn("m.iperiod = 0", query)


class OpeningsReplayTests(unittest.TestCase):
    def test_dry_run_passes_and_flag_stays_unposted(self) -> None:
        client = _Replay([_dry_reply()])
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|0"), _rows("2024-01-01|0")]):
            status, detail = openings_post_dry(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        route, sent = client.calls[0]
        self.assertEqual(route, _ROUTE)
        self.assertEqual((sent["module"], sent["action"], sent["dry_run"]), ("pu", "post", True))

    def test_dry_run_fails_when_flag_changed_or_reply_wrong(self) -> None:
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|0"), _rows("2024-01-01|1")]):
            status, detail = openings_post_dry(_env(_Replay([_dry_reply()])), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        self.assertIn("仍为 0", detail)
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|0")]):
            status, _detail = openings_post_dry(_env(_Replay([_dry_reply({"opening_year": 2025})])), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|0")]):
            status, _detail = openings_post_dry(_env(_Replay([_dry_reply(dry_run=None)])), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        # 旧形状（状态在顶层、没有 detail.opening）不算通过。
        flat = {"ok": True, "dry_run": True, "mode": "rollback", "action": "opening_post", "posted": True}
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|0")]):
            status, _detail = openings_post_dry(_env(_Replay([flat])), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|0")]):
            status, _detail = openings_post_dry(_env(_Replay([_REFUSED])), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")

    def test_dry_run_na_when_posted_or_not_enabled(self) -> None:
        for cell in ("2024-01-01|1", "|0", "x|0"):
            client = _Replay([])
            with self.subTest(cell=cell), mock.patch(_SQL, side_effect=[_rows(cell)]):
                self.assertEqual(openings_post_dry(_env(client), _SPEC, _STEP)[0], "N/A")
            self.assertEqual(client.calls, [])

    def test_refuse_posted_passes_on_two_409(self) -> None:
        client = _Replay([_REFUSED, _REFUSED])
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|1"), _rows("2024-01-01|1")]):
            status, detail = openings_refuse_posted(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        self.assertIs(client.calls[0][1]["dry_run"], True)
        self.assertNotIn("dry_run", client.calls[1][1])

    def test_refuse_posted_fails_on_success_or_other_message(self) -> None:
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|1")]):
            status, _detail = openings_refuse_posted(_env(_Replay([_dry_reply()])), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        other = U8CoError(409, "state_mismatch", "采购管理未启用")
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|1")]):
            status, _detail = openings_refuse_posted(_env(_Replay([other])), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")

    def test_refuse_posted_na_when_unposted(self) -> None:
        client = _Replay([])
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|0")]):
            self.assertEqual(openings_refuse_posted(_env(client), _SPEC, _STEP)[0], "N/A")
        self.assertEqual(client.calls, [])

    def test_non_test_account_marks_dry_and_refuse_na(self) -> None:
        client = _Replay([_NOT_TEST])
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|0")]):
            status, detail = openings_post_dry(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "N/A")
        self.assertIn("testAccounts", detail)
        client = _Replay([_NOT_TEST])
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|1")]):
            status, detail = openings_refuse_posted(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "N/A")
        self.assertEqual(len(client.calls), 1)


class OpeningsTestOnlyTests(unittest.TestCase):
    def test_passes_on_403_test_account_only_without_sql(self) -> None:
        client = _Replay([_NOT_TEST])
        with mock.patch(_SQL, side_effect=AssertionError("不应查库")):
            status, detail = openings_post_test_only(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        route, sent = client.calls[0]
        self.assertEqual(route, _ROUTE)
        self.assertEqual((sent["module"], sent["action"], sent["dry_run"]), ("pu", "post", True))

    def test_passes_on_403_feature_disabled_without_text_check(self) -> None:
        # 桥总开关 enableReplicatedWrites 未打开：登录前 403 feature_disabled，原文不同也 PASS。
        client = _Replay([_DISABLED])
        with mock.patch(_SQL, side_effect=AssertionError("不应查库")):
            status, detail = openings_post_test_only(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("feature_disabled", detail)
        self.assertEqual(len(client.calls), 1)

    def test_feature_disabled_marks_dry_and_refuse_na(self) -> None:
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|0")]):
            status, detail = openings_post_dry(_env(_Replay([_DISABLED])), _SPEC, _STEP)
        self.assertEqual(status, "N/A", detail)
        client = _Replay([_DISABLED])
        with mock.patch(_SQL, side_effect=[_rows("2024-01-01|1")]):
            status, detail = openings_refuse_posted(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "N/A", detail)
        self.assertEqual(len(client.calls), 1)

    def test_na_on_test_lane(self) -> None:
        for answer in (_dry_reply(), _REFUSED):
            with self.subTest(answer=answer):
                self.assertEqual(openings_post_test_only(_env(_Replay([answer])), _SPEC, _STEP)[0], "N/A")

    def test_fails_on_other_errors_or_wrong_text(self) -> None:
        for answer in (
            U8CoError(403, "no_permission", "没有权限"),
            U8CoError(500, "internal", "x"),
            U8CoError(403, "test_account_only", "月末结账只对配置为测试账套的账套开放"),
        ):
            with self.subTest(answer=answer):
                self.assertEqual(openings_post_test_only(_env(_Replay([answer])), _SPEC, _STEP)[0], "FAIL")


if __name__ == "__main__":
    unittest.main()
