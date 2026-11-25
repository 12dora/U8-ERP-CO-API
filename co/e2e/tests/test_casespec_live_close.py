"""存货核算记账与月末结账真实写入用例（live_close）的登记、标签、回读 SQL、取消结账计划和回放核对。不连接服务器。

回放用两个小模拟账套：桥调用改模拟状态，SQL 回读按语句里的表名从模拟状态出数。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.live_close import (
    LIVE_CLOSE_OPS,
    CLOSE_ORDER,
    REOPEN_ORDER,
    ia_post_period_end_live,
    last_closed,
    live_close_cases,
    periods_unclose_reclose,
    reopen_plan,
)
from co.e2e.casespec.live_close_sql import ia_close_sql, ia_extra_sql, live_close_samples, stock_month_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts, is_exclusive
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("ia-post-period-end-live", "periods-unclose-reclose")
_POST = "/v1/ia/post"
_END = "/v1/ia/period_end"
_CLOSE = "/v1/periods/close"
_SPEC = CaseSpec("ia-post-period-end-live", "s")
_STEP = Step("custom")
_SQL = "co.e2e.casespec.live_close.run_guarded"
_NOT_TEST = U8CoError(403, "test_account_only", "存货核算只对配置为测试账套的账套开放")
_REJECTED = U8CoError(409, "u8_rejected", "U8 拒绝：单据不平衡")
_INTERNAL = U8CoError(500, "internal", "内部错误")
_MODULES = ("pu", "sa", "st", "ia", "ar", "ap", "gl")
_KEYS = ("account", "accounts", "v", "vs", "check")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(client: object, today: str = "2026-10-01") -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today=today, bag={})


def _row(*values: object) -> str:
    return "ROW:" + "|".join(str(value) for value in values) + "\n"


class _IaSim:
    """一个月的存货核算：记账加明细账行和已记账单据行，期末处理标记汇总表，恢复记账退回。errors 按 action 抛错（用一次）。"""

    def __init__(self, sub: int = 0, closed: int = 0, area: int = 5, uncosted: int = 0) -> None:
        self.base = (sub, 10)
        self.sub, self.summary, self.ended, self.closed = sub, 0, 0, closed
        self.posted, self.area, self.uncosted = 10, area, uncosted
        self.errors: dict[str, list[U8CoError]] = {}
        self.calls: list[tuple[str, dict]] = []

    def actions(self) -> list[str]:
        return [fields["action"] for _route, fields in self.calls]

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        action = fields["action"]
        queue = self.errors.get(action) or []
        if queue:
            raise queue.pop(0)
        if action == "post":
            self.sub += self.area
            self.posted += self.area
            self.summary = 3 if self.area else self.summary
            return {"ok": True, "action": "post", "counts": {"area": self.area, "subsidiary_month": self.sub}}
        if action in ("run", "cancel"):
            self.ended = self.summary if action == "run" else 0
            return {"ok": True, "action": action}
        self.sub, self.posted = self.base
        return {"ok": True, "action": "unpost", "counts": {"restore_rows": 5}}

    def sql(self, _env: object, query: str, _label: str) -> str:
        if "cbaccounter" in query:
            return _row(self.posted, 2 if self.sub else 0, self.uncosted if self.ended else 0)
        return _row(self.sub, self.summary, self.ended, self.closed)


class _CloseSim:
    """GL_mend 2026 年各期的七个标志、目标月份的库存快照行数和存货核算结转计数。errors 按 (模块, action) 抛错。"""

    def __init__(self, closed_to: int = 9, stock: tuple = (4, 4, 3, 3, 9), ia: tuple = (5, 5, 4, 4)) -> None:
        self.flags = {p: dict.fromkeys(_MODULES, 1 if p <= closed_to else 0) for p in range(1, 13)}
        self.stock, self.ia, self.ia_before = stock, ia, ia
        self.written = stock
        self.errors: dict[tuple[str, str], list[U8CoError]] = {}
        self.persistent: dict[tuple[str, str], U8CoError] = {}
        self.deleted_reply: tuple | None = None
        self.calls: list[tuple[str, str]] = []

    def call(self, route: str, fields: dict) -> dict:
        assert route == _CLOSE
        key = (fields["module"], fields["action"])
        self.calls.append(key)
        if key in self.persistent:
            raise self.persistent[key]
        queue = self.errors.get(key) or []
        if queue:
            raise queue.pop(0)
        module, close = key[0], key[1] == "close"
        self.flags[fields["period"]][module] = 1 if close else 0
        reply: dict = {"ok": True, "module": module, "closed": close}
        if module == "st":
            rows = self.written if close else (self.deleted_reply or self.stock)
            self.stock = self.written if close else (0, 0, 0, 0, 0)
            reply["stock_rows"] = dict(zip(_KEYS, rows))
        if module == "ia":
            self.ia = self.ia_before if close else (self.ia_before[0], self.ia_before[1], 0, 0)
        return reply

    def sql(self, _env: object, query: str, _label: str) -> str:
        if "GL_mend m WHERE m.iperiod BETWEEN" in query:
            return "".join(_row(2026, p, *[flags[m] for m in _MODULES]) for p, flags in sorted(self.flags.items()))
        if "ST_MonthAccountCheck" in query:
            return _row(*self.stock)
        return _row(*self.ia)


def _flag_rows(closed_to: int, later: dict[str, int] | None = None) -> list[tuple[int, ...]]:
    """2026 年 1..closed_to 期全部结账；later 给出某些模块另外在哪一期也结了账。"""
    rows = []
    for period in range(1, 13):
        flags = [1 if period <= closed_to else 0 for _ in _MODULES]
        for module, at in (later or {}).items():
            if period == at:
                flags[_MODULES.index(module)] = 1
        rows.append((2026, period, *flags))
    return rows


class LiveCloseCatalogTests(unittest.TestCase):
    def test_cases_registered_last_exclusive(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in live_close_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        # 存货核算制单的一整轮 `ia-voucher-post-cycle` 排在这两条之后：periods-unclose-reclose 结束时结账标志已复原，
        # 它只在本月存货核算未结账、没有明细账时才写，自己恢复记账，互不影响。
        self.assertEqual(names[-3:-1], _NAMES)
        self.assertEqual(names[-1], "ia-voucher-post-cycle")
        self.assertEqual(set(LIVE_CLOSE_OPS), {"ia_post_period_end_live", "periods_unclose_reclose"})
        for name in _NAMES:
            spec = cases[name]
            self.assertTrue(spec.requires_login)
            self.assertTrue(is_exclusive(spec))
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
            self.assertLessEqual({"ia:post", "period:close", "opening:pu"}, set(auto_tags(spec)))
            self.assertTrue(conflicts(auto_tags(spec), frozenset(("health",)), True, False))
        self.assertEqual(cases[_NAMES[0]].routes, (_POST, _END))
        self.assertEqual(cases[_NAMES[1]].routes, (_CLOSE,))


class LiveCloseSqlTests(unittest.TestCase):
    def test_reads_pass_the_guard_and_are_in_samples(self) -> None:
        samples = live_close_samples()
        for name, query in samples:
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)
        for row in samples:
            self.assertIn(row, sample_sql())

    def test_sql_reads_the_month_and_rejects_bad_values(self) -> None:
        stock = stock_month_sql(2026, 9)
        for table in ("ST_MonthAccount t", "ST_MonthAccounts", "ST_MonthAccountV t", "ST_MonthAccountVs", "ST_MonthAccountCheck"):
            self.assertIn(table, stock)
        self.assertIn("iMonth = 9", stock)
        close = ia_close_sql(2026, 12)
        self.assertIn("iMonth = 13", close)
        self.assertIn("cVouType = N'24'", close)
        extra = ia_extra_sql(2026, 10)
        for text in ("rdrecords32", "SaleBillVouchs", "iOutCost IS NULL", "iMonth = 10"):
            self.assertIn(text, extra)
        for build in (ia_extra_sql, stock_month_sql, ia_close_sql):
            for year, month in ((26, 1), (2026, 0), (2026, 13), ("2026", 1)):
                with self.subTest(build=build.__name__, year=year, month=month), self.assertRaises(ValueError):
                    build(year, month)  # type: ignore[arg-type]


class ReopenPlanTests(unittest.TestCase):
    def test_last_closed_and_full_plan(self) -> None:
        rows = _flag_rows(9)
        self.assertEqual(last_closed(rows), (2026, 9))
        self.assertIsNone(last_closed(_flag_rows(0)))
        self.assertEqual(reopen_plan(rows, (2026, 9)), REOPEN_ORDER)
        self.assertEqual(REOPEN_ORDER, ("gl", "ar", "ap", "ia", "st", "sa", "pu"))

    def test_later_closed_module_is_kept_and_blocks_what_it_needs(self) -> None:
        # 采购以后还结了账：不取消，也不挡别的模块（没有模块要求采购先取消）。
        self.assertEqual(reopen_plan(_flag_rows(9, {"pu": 10}), (2026, 9)), ("gl", "ar", "ap", "ia", "st", "sa"))
        # 库存以后还结了账：库存要求采购、销售已结账，二者也不能取消。
        self.assertEqual(reopen_plan(_flag_rows(9, {"st": 10}), (2026, 9)), ("gl", "ar", "ap", "ia"))

    def test_open_module_is_skipped(self) -> None:
        rows = _flag_rows(9)
        rows[8] = (2026, 9, 1, 1, 1, 0, 1, 1, 1)  # 9 期存货核算没结账
        self.assertEqual(reopen_plan(rows, (2026, 9)), ("gl", "ar", "ap", "st", "sa", "pu"))


class IaLiveReplayTests(unittest.TestCase):
    def _run(self, sim: _IaSim) -> tuple[str, str]:
        with mock.patch(_SQL, side_effect=sim.sql):
            return ia_post_period_end_live(_env(sim), _SPEC, _STEP)

    def test_full_cycle_passes_and_restores(self) -> None:
        sim = _IaSim()
        status, detail = self._run(sim)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(sim.actions(), ["post", "run", "cancel", "unpost"])
        route, sent = sim.calls[0]
        self.assertEqual(route, _POST)
        self.assertEqual((sent["fiscal_year"], sent["period"], sent["on_uncosted"]), (2026, 10, "skip"))
        self.assertNotIn("dry_run", sent)
        self.assertEqual([route for route, _ in sim.calls], [_POST, _END, _END, _POST])
        self.assertEqual((sim.sub, sim.posted, sim.ended), (0, 10, 0))

    def test_na_without_calls_when_closed_or_already_posted(self) -> None:
        for sim in (_IaSim(closed=1), _IaSim(sub=7)):
            status, detail = self._run(sim)
            self.assertEqual(status, "N/A", detail)
            self.assertEqual(sim.calls, [])

    def test_na_on_not_test_account_env_refusal_or_nothing_to_post(self) -> None:
        refusals = (_NOT_TEST, U8CoError(409, "state_mismatch", "仓库 02 的计价方式不是全月平均法，接口暂不支持"))
        for error in refusals:
            sim = _IaSim()
            sim.errors["post"] = [error]
            status, detail = self._run(sim)
            self.assertEqual(status, "N/A", detail)
            self.assertEqual(sim.actions(), ["post"])
        sim = _IaSim(area=0)
        status, detail = self._run(sim)
        self.assertEqual(status, "N/A", detail)
        self.assertIn("counts.area", detail)
        self.assertEqual(sim.actions(), ["post"])

    def test_unknown_post_refusal_fails(self) -> None:
        sim = _IaSim()
        sim.errors["post"] = [_REJECTED]
        status, detail = self._run(sim)
        self.assertEqual(status, "FAIL", detail)
        self.assertEqual(sim.actions(), ["post"])

    def test_period_end_failure_unposts(self) -> None:
        sim = _IaSim()
        sim.errors["run"] = [_REJECTED]
        status, detail = self._run(sim)
        self.assertEqual(status, "FAIL", detail)
        self.assertIn("已还原", detail)
        self.assertEqual(sim.actions(), ["post", "run", "unpost"])

    def test_uncosted_out_rows_fail_and_restore_cancels_then_unposts(self) -> None:
        sim = _IaSim(uncosted=2)
        status, detail = self._run(sim)
        self.assertEqual(status, "FAIL", detail)
        self.assertIn("没有成本", detail)
        self.assertIn("已还原", detail)
        self.assertEqual(sim.actions(), ["post", "run", "cancel", "unpost"])
        self.assertEqual((sim.sub, sim.ended), (0, 0))

    def test_restore_that_cannot_unpost_reports_leftover(self) -> None:
        sim = _IaSim()
        sim.errors["unpost"] = [_INTERNAL, _INTERNAL]
        status, detail = self._run(sim)
        self.assertEqual(status, "FAIL", detail)
        self.assertIn("快照还原", detail)
        self.assertEqual(sim.actions(), ["post", "run", "cancel", "unpost", "unpost"])

    def test_bad_sql_reply_fails(self) -> None:
        sim = _IaSim()
        with mock.patch(_SQL, side_effect=["no rows"]):
            status, _detail = ia_post_period_end_live(_env(sim), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        self.assertEqual(sim.calls, [])


class PeriodsLiveReplayTests(unittest.TestCase):
    def _run(self, sim: _CloseSim) -> tuple[str, str]:
        with mock.patch(_SQL, side_effect=sim.sql):
            return periods_unclose_reclose(_env(sim), _SPEC, _STEP)

    def test_full_cycle_passes_in_u8_order(self) -> None:
        sim = _CloseSim()
        status, detail = self._run(sim)
        self.assertEqual(status, "PASS", detail)
        reopen = [(module, "reopen") for module in REOPEN_ORDER]
        close = [(module, "close") for module in CLOSE_ORDER]
        self.assertEqual(sim.calls, reopen + close)
        self.assertTrue(all(value == 1 for value in sim.flags[9].values()))
        self.assertIn("库存快照", detail)

    def test_na_when_nothing_closed_or_not_test_account(self) -> None:
        sim = _CloseSim(closed_to=0)
        self.assertEqual(self._run(sim)[0], "N/A")
        self.assertEqual(sim.calls, [])
        sim = _CloseSim()
        sim.errors[("gl", "reopen")] = [_NOT_TEST]
        status, detail = self._run(sim)
        self.assertEqual(status, "N/A", detail)
        self.assertEqual(sim.calls, [("gl", "reopen")])

    def test_env_refusal_skips_module_and_what_it_blocks(self) -> None:
        sim = _CloseSim()
        text = "存货核算 2026 年 9 期：本月没有存货核算数据，但该月有出入库单据，接口无法自动取消；请在 U8 里取消存货核算结账"
        sim.errors[("ia", "reopen")] = [U8CoError(409, "state_mismatch", text)]
        status, detail = self._run(sim)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("存货核算跳过", detail)
        want = [("gl", "reopen"), ("ar", "reopen"), ("ap", "reopen"), ("ia", "reopen"),
                ("ar", "close"), ("ap", "close"), ("gl", "close")]
        self.assertEqual(sim.calls, want)

    def test_close_failure_recloses_the_rest_and_reports_leftover(self) -> None:
        sim = _CloseSim()
        sim.persistent[("st", "close")] = U8CoError(409, "state_mismatch", "库存 2026 年 9 期：接口暂不支持")
        status, detail = self._run(sim)
        self.assertEqual(status, "FAIL", detail)
        self.assertIn("仍未结账：库存", detail)
        self.assertIn("已重新结账", detail)
        closes = [call for call in sim.calls if call[1] == "close"]
        self.assertEqual(closes[:3], [("pu", "close"), ("sa", "close"), ("st", "close")])
        self.assertEqual(closes[3:], [("st", "close"), ("ia", "close"), ("ar", "close"), ("ap", "close"), ("gl", "close")])

    def test_wrong_stock_rows_fail_and_reclose_everything(self) -> None:
        sim = _CloseSim()
        sim.deleted_reply = (1, 1, 1, 1, 1)
        status, detail = self._run(sim)
        self.assertEqual(status, "FAIL", detail)
        self.assertIn("stock_rows", detail)
        self.assertNotIn("仍未结账", detail)
        self.assertTrue(all(value == 1 for value in sim.flags[9].values()))

    def test_rewritten_snapshot_must_match_before(self) -> None:
        sim = _CloseSim()
        sim.written = (4, 4, 3, 3, 8)
        status, detail = self._run(sim)
        self.assertEqual(status, "FAIL", detail)
        self.assertIn("与结账前", detail)


if __name__ == "__main__":
    unittest.main()
