"""造数的逐月结账步骤（co/e2e/seed/steps_period_close）：排在最后、只结到上个月、按 SQL 幂等、非测试账套跳过。不连接服务器。"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from co.e2e.remote import RemoteResult
from co.e2e.seed import steps_period_close
from co.e2e.seed.context import Link, SeedContext, SeedError
from co.e2e.seed.runner import ALL_STEPS, DOC_STEPS, select
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

DAY = "2026-10-01"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class FakeCloseServer:
    """SQL 回总账各期的结账标志；periods/close 成功时把到目标期间为止的标志都改成 1（或按 reply 回）。"""

    def __init__(self, closed_to: int = 0, day: str = DAY) -> None:
        self.flags = {(y, p): (1 if y < 2026 or p <= closed_to else 0) for y in (2025, 2026) for p in range(1, 13)}
        self.day = day
        self.reply: dict | None = None
        self.posts: list[tuple[str, dict]] = []

    def post(self, path: str, body: dict) -> dict:
        self.posts.append((path, dict(body)))
        if self.reply is not None:
            return self.reply
        upto = (body["fiscal_year"], body["period"])
        opened = [key for key, flag in self.flags.items() if flag == 0 and key <= upto]
        for key in opened:
            self.flags[key] = 1
        return {"ok": True, "action": "close", "through": [], "count": len(opened)}

    def sql(self, query: str) -> RemoteResult:
        rows = "".join(f"{y}\t{p}\t{flag}\n" for (y, p), flag in sorted(self.flags.items()))
        return RemoteResult(0, "iyear\tiperiod\tgl\n-----\t-------\t--\n" + rows)

    def link(self) -> Link:
        return Link(self.post, self.sql, lambda: None, "op001", self.day, ["secret-x"])


class PeriodCloseOrderTests(unittest.TestCase):
    def test_step_runs_last_after_all_documents(self) -> None:
        names = [step.name for step in ALL_STEPS]
        self.assertEqual(names[-1], "close_prior_months")
        self.assertNotIn("close_prior_months", [step.name for step in DOC_STEPS])
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual([step.name for step in select("close_prior_months")], ["close_prior_months"])
        self.assertNotIn("close_prior_months", [step.name for step in select("", "close_prior_months")])

    def test_state_sql_passes_the_guard(self) -> None:
        text = "SET NOCOUNT ON;\n" + steps_period_close.STATE_SQL
        self.assertEqual(sql_block_reason(text), "")
        self.assertIn("ISNULL(m.bflag, 0)", text)
        self.assertIn("m.iperiod BETWEEN 1 AND 12", text)

    def test_target_is_the_month_before(self) -> None:
        self.assertEqual(steps_period_close.target("2026-10-01"), (2026, 9))
        self.assertEqual(steps_period_close.target("2026-01-31"), (2025, 12))


class PeriodCloseRunTests(unittest.TestCase):
    def _ctx(self, server: FakeCloseServer) -> SeedContext:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return SeedContext(Path(tmp.name), server.link())

    def test_open_months_are_closed_through_last_month(self) -> None:
        server = FakeCloseServer(closed_to=6)
        ctx = self._ctx(server)
        self.assertEqual(
            steps_period_close.probe(ctx).missing,
            ("close_prior_months:2026-07", "close_prior_months:2026-08", "close_prior_months:2026-09"),
        )
        self.assertEqual(steps_period_close.apply(ctx), (3, 0))
        body = {"action": "close", "through": True, "fiscal_year": 2026, "period": 9}
        self.assertEqual(server.posts, [("v1/periods/close", body)])
        self.assertEqual(server.flags[(2026, 10)], 0)
        self.assertEqual(steps_period_close.probe(ctx).existing, ("close_prior_months:2026-09",))

    def test_closed_months_are_skipped(self) -> None:
        server = FakeCloseServer(closed_to=9)
        self.assertEqual(steps_period_close.apply(self._ctx(server)), (0, 1))
        self.assertEqual(server.posts, [])

    def test_january_closes_the_previous_year(self) -> None:
        server = FakeCloseServer(day="2026-01-20")
        server.flags[(2025, 12)] = 0
        self.assertEqual(steps_period_close.apply(self._ctx(server)), (1, 0))
        self.assertEqual(server.posts[0][1]["fiscal_year"], 2025)
        self.assertEqual(server.posts[0][1]["period"], 12)

    def test_non_test_account_is_skipped_without_failing(self) -> None:
        server = FakeCloseServer(closed_to=6)
        server.reply = {"ok": False, "status": 403, "code": "test_account_only", "message": "月末结账只对配置为测试账套的账套开放"}
        self.assertEqual(steps_period_close.apply(self._ctx(server)), (0, 0))

    def test_feature_disabled_is_skipped_without_failing(self) -> None:
        server = FakeCloseServer(closed_to=6)
        server.reply = {"ok": False, "status": 403, "code": "feature_disabled", "message": "第二级写入未打开"}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(steps_period_close.apply(self._ctx(server)), (0, 0))
        self.assertIn("feature_disabled", out.getvalue())
        self.assertEqual(len(server.posts), 1)

    def test_refusal_is_skipped_without_failing(self) -> None:
        for code in ("state_mismatch", "u8_rejected"):
            server = FakeCloseServer(closed_to=6)
            server.reply = {"ok": False, "status": 409, "code": code, "message": "库存管理 2026 年 7 期：该月有单据"}
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(steps_period_close.apply(self._ctx(server)), (0, 0))
            self.assertIn("该月有单据", out.getvalue())
            self.assertEqual(len(server.posts), 1)

    def test_other_failure_still_fails_with_response(self) -> None:
        server = FakeCloseServer(closed_to=6)
        server.reply = {"ok": False, "status": 504, "code": "outcome_unknown", "message": "已提交但未能回读结账标志"}
        with self.assertRaises(SeedError) as caught:
            steps_period_close.apply(self._ctx(server))
        self.assertIn("未能回读", str(caught.exception))
        self.assertEqual(caught.exception.response["code"], "outcome_unknown")

    def test_still_open_after_success_fails(self) -> None:
        server = FakeCloseServer(closed_to=6)
        server.reply = {"ok": True, "action": "close", "through": [], "count": 0}
        with self.assertRaises(SeedError) as caught:
            steps_period_close.apply(self._ctx(server))
        self.assertIn("2026-07", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
