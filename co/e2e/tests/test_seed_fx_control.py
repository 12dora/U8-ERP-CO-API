"""造数的应付美元受控科目（co/e2e/seed/steps_fx_control）：顺序、编码推算、复制 SQL、幂等和回读。不连接服务器。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from co.e2e.remote import RemoteResult
from co.e2e.seed import steps_fx_control as ctl
from co.e2e.seed.context import Link, SeedContext, SeedError
from co.e2e.seed.runner import ALL_STEPS, DOC_STEPS, select
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

DAY = "2026-10-02"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["\t".join(header), "\t".join("-" * len(name) for name in header)]
    lines += ["\t".join(row) for row in rows]
    return "\n".join(lines) + "\n"


class FakeCodes:
    """科目表的假服务器：usd 为现有的美元户（空串表示没有）；插入语句执行后美元户变成 made。"""

    def __init__(self, usd: str = "", source: tuple[str, str] = ("220201", "2"), siblings: tuple[str, ...] = ("220201",)) -> None:
        self.usd = usd
        self.source = source
        self.siblings = siblings
        self.made = "220202"
        self.sqls: list[str] = []
        self.inserts: list[str] = []

    def sql(self, query: str) -> RemoteResult:
        self.sqls.append(query)
        if "INSERT INTO code" in query:
            self.inserts.append(query)
            self.usd = self.made
            return RemoteResult(0, "")
        return RemoteResult(0, self._answer(query))

    def _answer(self, query: str) -> str:
        if "ISNULL(cexch_name, N'') <>" in query:
            return _table(["code", "grade"], [list(self.source)])
        if "cexch_name = N'美元'" in query:
            return _table(["code"], [[self.usd]] if self.usd else [])
        if "igrade = " in query:
            return _table(["code"], [["2202"]])
        if "LEN(LTRIM(RTRIM(ccode)))" in query:
            return _table(["code"], [[code] for code in self.siblings])
        return _table(["code"], [])

    def link(self) -> Link:
        return Link(lambda _p, _b: {"ok": True}, self.sql, lambda: None, "op001", DAY, ["secret-x"])


def _step():
    return next(step for step in ALL_STEPS if step.name == ctl.NAME)


class OrderTests(unittest.TestCase):
    def test_runs_after_arap_and_before_fx_documents(self) -> None:
        docs = [step.name for step in DOC_STEPS]
        self.assertIn(ctl.NAME, docs)
        self.assertLess(docs.index("ap_payment"), docs.index(ctl.NAME))
        for later in ("fx_rate", "fx_ap_payment", "fx_ap_bill"):
            self.assertLess(docs.index(ctl.NAME), docs.index(later), later)
        self.assertEqual([step.name for step in select(ctl.NAME)], [ctl.NAME])


class PureTests(unittest.TestCase):
    def test_next_code(self) -> None:
        self.assertEqual(ctl.next_code("2202", 6, ["220201"]), "220202")
        self.assertEqual(ctl.next_code("2202", 6, ["220201", "220209", "22020x"]), "220210")
        self.assertEqual(ctl.next_code("2202", 6, []), "220201")
        self.assertEqual(ctl.next_code("220201", 8, ["22020101"]), "22020102")
        with self.assertRaises(SeedError):
            ctl.next_code("2202", 6, ["220299"])
        with self.assertRaises(SeedError):
            ctl.next_code("2202", 4, [])

    def test_insert_copies_source_and_overrides(self) -> None:
        sql = ctl.insert_sql(2026, "220201", "220202")
        self.assertEqual(sql_block_reason("SET NOCOUNT ON;\n" + sql), "")
        for part in (
            "INSERT INTO code (cclass,",
            "c.ccode = N'220201'",
            "N'220202', N'应付美元户', N'AP USD'",
            "N'美元', c.cmeasure",
            "c.bsup",
            "c.cother",
            "c.bexchange",
            "2026, c.dModifyDate",
            "t.iyear = 2026 AND t.ccode = N'220202'",
        ):
            self.assertIn(part, sql)
        self.assertNotIn("i_id", sql)
        self.assertIn(", 1, c.bexchange", sql)  # bend 写死 1
        for bad in (("2202'1", "220202"), ("220201", "")):
            with self.assertRaises(SeedError):
                ctl.insert_sql(2026, *bad)


class RunTests(unittest.TestCase):
    def _ctx(self, server: FakeCodes) -> SeedContext:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return SeedContext(Path(tmp.name), server.link())

    def test_missing_account_is_copied_then_read_back(self) -> None:
        server = FakeCodes()
        ctx = self._ctx(server)
        self.assertEqual(_step().probe(ctx).missing, ("account:2202*:美元",))
        self.assertEqual(_step().apply(ctx), (1, 0))
        self.assertEqual(len(server.inserts), 1)
        self.assertIn("N'220202'", server.inserts[0])
        self.assertEqual(ctx.state["created"]["account"], ["220202"])
        self.assertEqual(_step().probe(ctx).existing, ("account:220202",))
        for query in server.sqls:
            self.assertEqual(sql_block_reason(query), "", query)

    def test_existing_account_is_kept(self) -> None:
        server = FakeCodes(usd="220299")
        ctx = self._ctx(server)
        self.assertEqual(_step().apply(ctx), (0, 1))
        self.assertEqual(server.inserts, [])

    def test_first_grade_source_fails(self) -> None:
        server = FakeCodes(source=("2202", "1"))
        with self.assertRaises(SeedError) as caught:
            _step().apply(self._ctx(server))
        self.assertIn("一级末级科目", str(caught.exception))
        self.assertEqual(server.inserts, [])

    def test_read_back_mismatch_fails(self) -> None:
        server = FakeCodes()
        server.made = "220299"
        with self.assertRaises(SeedError) as caught:
            _step().apply(self._ctx(server))
        self.assertIn("220202", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
