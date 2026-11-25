"""存货核算用例（ia_post）的登记、标签、回读 SQL 和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoClient, U8CoError
from co.client.u8co_ia import is_long_call

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.ia_post import IA_OPS, ia_bad_action, ia_cases, ia_period_end_dry, ia_post_dry
from co.e2e.casespec.ia_post_sql import ia_month_sql, ia_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts, is_exclusive
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("ia-post-dry", "ia-period-end-dry", "ia-bad-action")
_POST = "/v1/ia/post"
_END = "/v1/ia/period_end"
_SPEC = CaseSpec("ia-post-dry", "s")
_STEP = Step("custom")
_SQL = "co.e2e.casespec.ia_post.run_guarded"
_NOT_TEST = U8CoError(403, "test_account_only", "存货核算只对配置为测试账套的账套开放")
_DISABLED = U8CoError(403, "feature_disabled", "第二级写入未打开")
_UNCOSTED = U8CoError(
    409, "state_mismatch", "有存货 U8 无法确定成本（U8 界面会要求手工输入单价），没有记账：共 2 个，例如 仓库 01 存货 A001"
)
_UNSUPPORTED = U8CoError(409, "state_mismatch", "仓库 02 的计价方式不是全月平均法，接口暂不支持；请在 U8 里处理")
_UNMAPPED = U8CoError(409, "state_mismatch", "存货核算拒绝本次操作（编号 50099）")
_REJECTED = U8CoError(409, "u8_rejected", "U8 拒绝：单据不平衡")


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


def _month(sub: int = 0, summary: int = 0, ended: int = 0, closed: int = 0) -> str:
    return f"ROW:{sub}|{summary}|{ended}|{closed}\n"


def _dry(action: str = "ia_post", counts: dict | None = None, **extra: object) -> dict:
    body = {"ok": True, "dry_run": True, "mode": "rollback", "action": action, "docs": [],
            "detail": {"counts": counts if counts is not None else {"subsidiary_month": 12, "summary_month": 4}}}
    body.update(extra)
    return body


class IaCatalogTests(unittest.TestCase):
    def test_cases_registered_with_ops_and_tags(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in ia_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
        self.assertEqual(cases["ia-post-dry"].routes, (_POST,))
        self.assertEqual(cases["ia-period-end-dry"].routes, (_END,))
        for name in _NAMES[:2]:
            spec = cases[name]
            self.assertTrue(spec.requires_login)
            self.assertTrue(is_exclusive(spec))
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
            self.assertLessEqual({"ia:post", "period:close", "opening:pu"}, set(auto_tags(spec)))
        self.assertEqual(set(IA_OPS), {"ia_post_dry", "ia_period_end_dry", "ia_bad_action"})
        bad = cases["ia-bad-action"]
        self.assertFalse(bad.requires_login)
        self.assertFalse(is_exclusive(bad))
        self.assertEqual(bad.tags, frozenset(("probe",)))
        self.assertEqual(bad.routes, (_POST,))
        self.assertEqual((bad.steps[0].kind, bad.steps[0].sql_name), ("custom", "ia_bad_action"))
        self.assertTrue(ops_run.has_op("ia_bad_action"))

    def test_cases_conflict_with_period_close(self) -> None:
        cases = {spec.name: spec for spec in ia_cases()}
        self.assertTrue(conflicts(auto_tags(cases["ia-post-dry"]), frozenset(("period:close",))))


class IaSqlTests(unittest.TestCase):
    def test_reads_pass_the_guard_and_are_in_samples(self) -> None:
        for name, query in ia_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)
        self.assertIn(("ia-month", ia_month_sql(2026, 9)), sample_sql())

    def test_month_sql_reads_the_month_and_rejects_bad_values(self) -> None:
        query = ia_month_sql(2026, 10)
        for text in ("IA_Subsidiary", "IA_Summary", "iPeriod, 0) = 1", "bflag_IA", "iYear = 2026", "iMonth = 10"):
            self.assertIn(text, query)
        for year, month in ((26, 1), (2026, 0), (2026, 13), ("2026", 1)):
            with self.subTest(year=year, month=month), self.assertRaises(ValueError):
                ia_month_sql(year, month)  # type: ignore[arg-type]


class IaPostReplayTests(unittest.TestCase):
    def test_dry_post_passes_and_month_unchanged(self) -> None:
        client = _Replay([_dry()])
        with mock.patch(_SQL, side_effect=[_month(5, 2), _month(5, 2)]):
            status, detail = ia_post_dry(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        route, sent = client.calls[0]
        self.assertEqual(route, _POST)
        self.assertEqual((sent["fiscal_year"], sent["period"], sent["action"], sent["dry_run"]), (2026, 10, "post", True))
        self.assertNotIn("on_uncosted", sent)

    def test_refused_post_retries_with_skip(self) -> None:
        client = _Replay([_UNCOSTED, _dry()])
        with mock.patch(_SQL, side_effect=[_month(), _month()]):
            status, detail = ia_post_dry(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("skip", detail)
        self.assertNotIn("on_uncosted", client.calls[0][1])
        self.assertEqual(client.calls[1][1]["on_uncosted"], "skip")

    def test_dry_post_fails_when_month_changes_or_reply_wrong(self) -> None:
        with mock.patch(_SQL, side_effect=[_month(5, 2), _month(9, 2)]):
            status, detail = ia_post_dry(_env(_Replay([_dry()])), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        self.assertIn("回滚", detail)
        wrong = (
            _dry(dry_run=None),
            _dry(mode="validate"),
            _dry(action="ia_period_end"),
            _dry(counts={}),
            {"ok": True, "action": "post", "fiscal_year": 2026, "period": 10, "counts": {}},
            U8CoError(400, "bad_request", "坏字段"),
            U8CoError(500, "internal", "内部错误"),
        )
        for reply in wrong:
            with self.subTest(reply=reply), mock.patch(_SQL, side_effect=[_month()]):
                self.assertEqual(ia_post_dry(_env(_Replay([reply])), _SPEC, _STEP)[0], "FAIL")
        with mock.patch(_SQL, side_effect=["no rows"]):
            self.assertEqual(ia_post_dry(_env(_Replay([])), _SPEC, _STEP)[0], "FAIL")

    def test_dry_post_na_cases(self) -> None:
        client = _Replay([])
        with mock.patch(_SQL, side_effect=[_month(closed=1)]):
            self.assertEqual(ia_post_dry(_env(client), _SPEC, _STEP)[0], "N/A")
        self.assertEqual(client.calls, [])
        for refusal in (_NOT_TEST, _DISABLED):
            with mock.patch(_SQL, side_effect=[_month()]):
                client = _Replay([refusal])
                self.assertEqual(ia_post_dry(_env(client), _SPEC, _STEP)[0], "N/A")
            self.assertEqual(len(client.calls), 1)
        with mock.patch(_SQL, side_effect=[_month()]):
            client = _Replay([_UNSUPPORTED])
            status, detail = ia_post_dry(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "N/A", detail)
        self.assertIn("接口暂不支持", detail)
        self.assertEqual(len(client.calls), 1)  # 环境原因不再用 skip 重试
        with mock.patch(_SQL, side_effect=[_month()]):
            status, detail = ia_post_dry(_env(_Replay([_UNCOSTED, _UNSUPPORTED])), _SPEC, _STEP)
        self.assertEqual(status, "N/A", detail)

    def test_unknown_refusals_fail(self) -> None:
        # 桥没对上号的编号、u8_rejected、skip 之后仍无法确定成本：都是回归失败，不能记 N/A。
        for answers in ([_UNMAPPED], [_REJECTED], [_UNCOSTED, _UNCOSTED], [_UNCOSTED, _REJECTED]):
            with self.subTest(answers=[a.message for a in answers]), mock.patch(_SQL, side_effect=[_month()]):
                status, detail = ia_post_dry(_env(_Replay(answers)), _SPEC, _STEP)
            self.assertEqual(status, "FAIL", detail)


class IaPeriodEndReplayTests(unittest.TestCase):
    def test_dry_period_end_passes_and_month_unchanged(self) -> None:
        client = _Replay([_dry("ia_period_end")])
        with mock.patch(_SQL, side_effect=[_month(5, 2), _month(5, 2)]):
            status, detail = ia_period_end_dry(_env(client), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        route, sent = client.calls[0]
        self.assertEqual(route, _END)
        self.assertEqual((sent["fiscal_year"], sent["period"], sent["action"], sent["dry_run"]), (2026, 10, "run", True))

    def test_dry_period_end_fails_on_change_or_wrong_action(self) -> None:
        with mock.patch(_SQL, side_effect=[_month(5, 2), _month(5, 2, 2)]):
            self.assertEqual(ia_period_end_dry(_env(_Replay([_dry("ia_period_end")])), _SPEC, _STEP)[0], "FAIL")
        with mock.patch(_SQL, side_effect=[_month(5, 2)]):
            self.assertEqual(ia_period_end_dry(_env(_Replay([_dry("ia_post")])), _SPEC, _STEP)[0], "FAIL")

    def test_dry_period_end_na_cases(self) -> None:
        # 本月没有明细账、已结账：不调桥。
        for state in (_month(0, 3), _month(5, 2, 0, 1)):
            client = _Replay([])
            with self.subTest(state=state), mock.patch(_SQL, side_effect=[state]):
                self.assertEqual(ia_period_end_dry(_env(client), _SPEC, _STEP)[0], "N/A")
            self.assertEqual(client.calls, [])
        for answer in (_NOT_TEST, _UNSUPPORTED):
            with self.subTest(answer=answer.code), mock.patch(_SQL, side_effect=[_month(5, 2)]):
                status, detail = ia_period_end_dry(_env(_Replay([answer])), _SPEC, _STEP)
            self.assertEqual(status, "N/A", detail)
        for answer in (_UNMAPPED, _REJECTED):
            with self.subTest(answer=answer.message), mock.patch(_SQL, side_effect=[_month(5, 2)]):
                self.assertEqual(ia_period_end_dry(_env(_Replay([answer])), _SPEC, _STEP)[0], "FAIL")


class IaBadActionTests(unittest.TestCase):
    def test_passes_only_on_400_with_field_action(self) -> None:
        good = U8CoError(400, "bad_request", "action 只能是 post 或 unpost", field="action")
        client = _Replay([good])
        self.assertEqual(ia_bad_action(_env(client), _SPEC, _STEP)[0], "PASS")
        self.assertEqual(client.calls[0][0], _POST)
        self.assertEqual(client.calls[0][1]["action"], "xx")
        wrong = (
            U8CoError(400, "bad_request", "period 必须是 1 到 12 的整数", field="period"),
            U8CoError(400, "bad_request", "坏请求"),
            U8CoError(403, "test_account_only", "只对测试账套开放"),
            {"ok": True},
        )
        for reply in wrong:
            with self.subTest(reply=reply):
                self.assertEqual(ia_bad_action(_env(_Replay([reply])), _SPEC, _STEP)[0], "FAIL")


class IaTimeoutTests(unittest.TestCase):
    def test_suite_client_waits_long_on_ia_and_periods_routes(self) -> None:
        # e2e 的客户端（suite / session_call）按 timeout=90 构造，长时操作走客户端缺省的 long_timeout。
        client = U8CoClient("http://127.0.0.1/u8co", "00" * 32, timeout=90)
        self.assertEqual(client._long_timeout, 1000)
        for route in (_POST, _END):
            self.assertTrue(is_long_call(route, {}))
        self.assertTrue(is_long_call("/v1/periods/close", {"through": True}))


if __name__ == "__main__":
    unittest.main()
