"""空账套造数的采购期初记账步骤（co/e2e/seed/steps_pu_opening）：顺序、按 SQL 幂等、记账后回读。不连接服务器。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from co.e2e.remote import RemoteResult
from co.e2e.seed import steps_pu_opening
from co.e2e.seed.context import Link, SeedContext, SeedError
from co.e2e.seed.runner import ALL_STEPS, DOC_STEPS, select
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

DAY = "2026-10-01"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _table(start: str, posted: str) -> str:
    return f"start\tposted\n-----\t------\n{start}\t{posted}\n"


class FakeOpeningServer:
    """SQL 回启用日期和记账标志；openings/post 成功时把标志改成 1（或按 reply 回错误）。"""

    def __init__(self, start: str = "2024-01-01", posted: str = "0") -> None:
        self.start = start
        self.posted = posted
        self.reply: dict | None = None
        self.posts: list[tuple[str, dict]] = []
        self.sqls: list[str] = []

    def post(self, path: str, body: dict) -> dict:
        self.posts.append((path, dict(body)))
        if self.reply is not None:
            return self.reply
        self.posted = "1"
        return {"ok": True, "module": "pu", "action": "post", "posted": True, "opening_year": 2024}

    def sql(self, query: str) -> RemoteResult:
        self.sqls.append(query)
        return RemoteResult(0, _table(self.start, self.posted))

    def link(self) -> Link:
        return Link(self.post, self.sql, lambda: None, "op001", DAY, ["secret-x"])


class PuOpeningOrderTests(unittest.TestCase):
    def test_step_runs_before_purchase_documents(self) -> None:
        names = [step.name for step in ALL_STEPS]
        doc_names = [step.name for step in DOC_STEPS]
        self.assertIn("pu_opening", doc_names)
        for later in ("po_open", "po_arr", "po_arr_arrival", "po_in", "po_in_inbound", "purchase_invoice"):
            self.assertLess(names.index("pu_opening"), names.index(later), later)
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual([step.name for step in select("pu_opening")], ["pu_opening"])
        self.assertNotIn("pu_opening", [step.name for step in select("", "pu_opening")])

    def test_state_sql_passes_the_guard(self) -> None:
        text = "SET NOCOUNT ON;\n" + steps_pu_opening.STATE_SQL
        self.assertEqual(sql_block_reason(text), "")
        self.assertIn("cSysID = N'PU' AND cName = N'dPUStartDate'", text)
        self.assertIn("m.iperiod = 0", text)


class PuOpeningRunTests(unittest.TestCase):
    def _ctx(self, server: FakeOpeningServer) -> SeedContext:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return SeedContext(Path(tmp.name), server.link())

    def test_unposted_is_posted_through_the_bridge(self) -> None:
        server = FakeOpeningServer()
        ctx = self._ctx(server)
        self.assertEqual(steps_pu_opening.probe(ctx).missing, ("pu_opening:2024",))
        self.assertEqual(steps_pu_opening.apply(ctx), (1, 0))
        self.assertEqual(server.posts, [("v1/openings/post", {"module": "pu", "action": "post"})])
        self.assertEqual(steps_pu_opening.probe(ctx).existing, ("pu_opening:2024",))

    def test_posted_is_skipped(self) -> None:
        server = FakeOpeningServer(posted="1")
        self.assertEqual(steps_pu_opening.apply(self._ctx(server)), (0, 1))
        self.assertEqual(server.posts, [])

    def test_refusal_already_posted_counts_as_existing(self) -> None:
        server = FakeOpeningServer()
        server.reply = {"ok": False, "status": 409, "code": "state_mismatch", "message": "采购期初已记账"}
        self.assertEqual(steps_pu_opening.apply(self._ctx(server)), (0, 1))

    def test_other_refusal_fails_with_response(self) -> None:
        server = FakeOpeningServer()
        server.reply = {"ok": False, "status": 409, "code": "state_mismatch", "message": "采购已有月份结账，不能期初记账"}
        with self.assertRaises(SeedError) as caught:
            steps_pu_opening.apply(self._ctx(server))
        self.assertIn("月份结账", str(caught.exception))
        self.assertEqual(caught.exception.response["code"], "state_mismatch")

    def test_unchanged_flag_after_success_fails(self) -> None:
        server = FakeOpeningServer()
        server.reply = {"ok": True, "posted": True}
        with self.assertRaises(SeedError) as caught:
            steps_pu_opening.apply(self._ctx(server))
        self.assertIn("仍未记账", str(caught.exception))

    def test_not_enabled_fails_before_the_bridge(self) -> None:
        for start in ("", "NULL", "bad"):
            server = FakeOpeningServer(start=start)
            ctx = self._ctx(server)
            with self.subTest(start=start):
                self.assertEqual(steps_pu_opening.probe(ctx).missing, ("pu_opening:未启用",))
                with self.assertRaises(SeedError) as caught:
                    steps_pu_opening.apply(ctx)
                self.assertIn("采购管理未启用", str(caught.exception))
            self.assertEqual(server.posts, [])


if __name__ == "__main__":
    unittest.main()
