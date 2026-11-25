"""voucher-so-defines 用例连带下游一轮（so_defines_cascade）的 SQL 守卫、纯函数和一轮回放。不连接服务器。

假库和假路由在 sodc_fake.py。
"""

from __future__ import annotations

import unittest

from co.e2e.casespec.head_fix_bodies import parse_snap
from co.e2e.casespec.so_defines_cascade_bodies import cascade_problem, cascade_write_problem
from co.e2e.casespec.so_defines_cascade_sql import cascade_pick_sql, cascade_snapshot_sql, down_restore_sql, mixed_sql, sodc_samples
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support
from co.e2e.tests.sodc_fake import CascClient, CascDb
from co.e2e.tests.test_casespec_so_defines import run_case

_PAIR = ("cdefine1", "cdefine2")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _cascades(client: CascClient, so_id: int) -> list[dict]:
    return [body for _route, body in client.calls if body["id"] == so_id and body.get("cascade") is True]


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, sql in sodc_samples():
            self.assertEqual(sql_block_reason(sql), "", name)
            self.assertIn("ROW:", sql, name)

    def test_links_match_the_bridge(self) -> None:
        text = cascade_snapshot_sql("42")
        for needle in ("dbo.rdrecord32", "NULLIF(r.iDLsID, 0)", "NULLIF(b.iSOsID, 0)", "dbo.DispatchList ", "s1.ID = 42"):
            self.assertIn(needle, text)
        self.assertNotIn("ISNULL(b.iSOsID", text)

    def test_invoice_resolver_mirrors_the_bridge(self) -> None:
        text = mixed_sql("42")
        # 两条链接各自解析、不同即冲突；蓝字一跳只在两条都没给时、排除自指；解析出的来源不是本订单即混合。
        for needle in (
            "NULLIF(b2.iSOsID, 0) IS NULL THEN 0",
            "WHEN (CASE WHEN NULLIF(b2.iSOsID, 0) IS NULL",
            "ELSE -1 END",
            "bb2.AutoID = NULLIF(b2.iSBVID, 0) AND bb2.AutoID <> b2.AutoID",
            "NULLIF(b2.iSOsID, 0) IS NULL AND NULLIF(b2.iDLsID, 0) IS NULL",
            "s32.iSOsID = NULLIF(bb2.iSOsID, 0)",
            ") <> 42",
        ):
            self.assertIn(needle, text)
        self.assertIn("NOT EXISTS", cascade_pick_sql("pure"))
        self.assertIn("p.ID NOT IN (11, 12)", cascade_pick_sql("mixed", ("11", "12")))
        self.assertNotRegex(cascade_pick_sql("pure"), r"\b[0-9]{5,}\b")
        with self.assertRaises(ValueError):
            cascade_pick_sql("pure", ("1; DROP",))

    def test_down_restore_writes_only_the_written_columns(self) -> None:
        text = down_restore_sql("SB", "9", {"cdefine1": "<nil>", "cdefine2": "a'b", "cdefine3": "c", "cmodifier": "u", "ufts": "x"})
        self.assertIn("UPDATE dbo.SaleBillVouch SET cdefine1 = NULL, cdefine2 = N'a''b', cdefine3 = N'c'", text)
        self.assertNotIn("cmodifier", text)
        with self.assertRaises(ValueError):
            down_restore_sql("DL", "9", {"cmodifier": "u", "cdefine4": "d"})


class PureTests(unittest.TestCase):
    def test_cascade_problem(self) -> None:
        snap = parse_snap(["DL\t7\tcdefine1\tA", "DL\t7\tcdefine2\t<nil>", "SB\t8\tcdefine1\tB"])
        item = {"type": "dispatch", "id": 7, "result": "updated", "before": {"cdefine1": "A", "cdefine2": None}}
        other = {"type": "sale_invoice", "id": 8, "result": "skipped", "reason": "mixed_sources", "before": {"cdefine1": "B", "cdefine2": None}}
        want = {("DL", "7"): ("updated", None), ("SB", "8"): ("skipped", "mixed_sources")}
        self.assertEqual(cascade_problem({"cascade": [item, other]}, snap, want, _PAIR), "")
        self.assertIn("单据", cascade_problem({"cascade": [item]}, snap, want, _PAIR))
        self.assertIn("result", cascade_problem({"cascade": [item, {**other, "reason": None}]}, snap, want, _PAIR))
        self.assertIn("before", cascade_problem({"cascade": [{**item, "before": {"cdefine1": "Z", "cdefine2": None}}, other]}, snap, want, _PAIR))
        old_keys = {**item, "before": {"cdefine1": "A"}}
        self.assertIn("before 键", cascade_problem({"cascade": [old_keys, other]}, snap, want, _PAIR))
        self.assertIn("cascade", cascade_problem({}, snap, want, _PAIR))

    def test_write_problem(self) -> None:
        before = parse_snap(["SO\t1\tcdefine1\tA", "SO\t1\tcdefine2\t<nil>", "DL\t7\tcdefine1\tA", "DL\t7\tcmodifier\tx"])
        after = parse_snap(["SO\t1\tcdefine1\tK", "SO\t1\tcdefine2\tH", "DL\t7\tcdefine1\tK", "DL\t7\tcmodifier\tx"])
        targets = {"cdefine1": "K", "cdefine2": "H"}
        self.assertIn("下游", cascade_write_problem(before, after, "1", targets, {("DL", "7")})[0])
        after["DL"]["7"]["cdefine2"] = "H"
        self.assertEqual(cascade_write_problem(before, after, "1", targets, {("DL", "7")})[0], "")
        self.assertIn("不该变", cascade_write_problem(before, after, "1", targets, set())[0])
        after["DL"]["7"]["cdefine3"] = "Z"
        self.assertIn("不该变", cascade_write_problem(before, after, "1", targets, {("DL", "7")})[0])


class ReplayTests(unittest.TestCase):
    def _go(self, db: CascDb | None = None, **flags: bool) -> tuple[str, str, CascClient, CascDb]:
        db = db or CascDb()
        client = CascClient(db, **flags)
        status, note = run_case(client, db)
        return status, note, client, db

    def test_passes_and_restores_everything(self) -> None:
        start = CascDb().cells()
        status, note, client, db = self._go()
        self.assertEqual(status, "PASS", note)
        self.assertIn("cascade-pure+cascade-mixed", note)
        self.assertEqual(db.cells(), start)
        pure = _cascades(client, 200)
        self.assertEqual(len(pure), 4)
        for body in pure:
            self.assertEqual((set(body["fields"]), body["empty_values"]), (set(_PAIR), ["无"]))
        self.assertEqual(len(_cascades(client, 201)), 1)
        self.assertFalse([body for _route, body in client.calls if body["id"] < 200 and body.get("cascade")])

    def test_missing_cascade_kinds_are_noted(self) -> None:
        db = CascDb()
        db.absent = {"cascade-pure", "cascade-mixed"}
        status, note, client, _db = self._go(db)
        self.assertEqual(status, "PASS", note)
        self.assertIn("（连带下游）", note)
        self.assertFalse([body for _route, body in client.calls if body.get("cascade")])

    def _fails(self, text: str, **flags: bool) -> None:
        start = CascDb().cells()
        status, note, _client, db = self._go(**flags)
        self.assertEqual(status, "FAIL", note)
        self.assertIn(text, note)
        self.assertEqual(db.cells(), start, "失败时也要还原")

    def test_fails_when_mixed_sources_are_updated(self) -> None:
        self._fails("result/reason", cascade_mixed=True)

    def test_fails_when_downstream_is_not_written(self) -> None:
        self._fails("下游", no_cascade=True)

    def test_fails_when_unrequested_downstream_columns_are_written(self) -> None:
        self._fails("不该变", cascade_all=True)

    def test_fails_when_downstream_modifier_is_stamped(self) -> None:
        start = CascDb().cells()
        status, note, _client, db = self._go(cascade_stamp=True)
        self.assertEqual(status, "FAIL", note)
        self.assertIn("不该变", note)
        # 下游的修改人不在兜底写回范围：报「还原后快照仍有差异」，留给会话快照。
        self.assertNotEqual(db.cells(), start)


if __name__ == "__main__":
    unittest.main()
