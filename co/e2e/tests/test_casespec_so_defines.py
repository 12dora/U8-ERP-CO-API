"""销售订单表头自定义项写入用例 `voucher-so-defines` 的登记、分线、选用例、SQL 守卫、纯函数和一轮回放。不连接服务器。

假库和假路由在 sod_fake.py。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import so_defines as mod
from co.e2e.casespec import so_defines_cascade as casc_mod
from co.e2e.casespec import so_defines_trip as trip_mod
from co.e2e.casespec.head_fix_bodies import parse_snap
from co.e2e.casespec.so_defines_bodies import (
    expect_of,
    mismatch_problem,
    non_text,
    parse_cols,
    reply_dry,
    reply_write,
    same_value,
    sod_body,
    untouched_problem,
    write_problem,
)
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.arc_write import CORE_HANDLERS
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.so_defines_sql import columns_sql, pick_sql, restore_sql, snapshot_sql, sod_samples, writable
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support
from co.e2e.tests.sod_fake import Client, Db

_NAME = "voucher-so-defines"
_ALL = "plain+closed+shipped+filled"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(client: Client) -> SimpleNamespace:
    op = SimpleNamespace(acc="801", year="2026", code="op001", password="pw")
    return SimpleNamespace(client=client, operator=op, year="2026", today="2026-09-28", operator_name="", sql_n=0)


def run_case(client: Client, db: Db) -> tuple[str, str]:
    spec = mod.sodefines_cases()[0]
    with (
        mock.patch.object(mod, "run_guarded", db.guarded),
        mock.patch.object(trip_mod, "run_guarded", db.guarded),
        mock.patch.object(casc_mod, "run_guarded", db.guarded),
    ):
        return mod.step_sodefines(_env(client), spec, spec.steps[0])


def _writes(client: Client, so_id: int) -> list[dict]:
    return [body for _route, body in client.calls if body["id"] == so_id and not body.get("dry_run")]


class CatalogTests(unittest.TestCase):
    def test_case_registered(self) -> None:
        (spec,) = mod.sodefines_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(spec.requires_login)
        self.assertEqual(spec.data, ())
        self.assertEqual(spec.routes, ("/v1/vouchers/so_defines",))
        self.assertEqual(spec.steps[0].kind, "core_sodefines")
        self.assertIs(CORE_HANDLERS["core_sodefines"], mod.step_sodefines)
        self.assertIs(_HANDLERS["core_sodefines"], mod.step_sodefines)

    def test_tags_keep_it_apart_from_sale_chain(self) -> None:
        (spec,) = mod.sodefines_cases()
        tags = auto_tags(spec)
        self.assertIn("w:sale_order", tags)
        self.assertIn("chain:sale", tags)
        self.assertTrue(conflicts(tags, frozenset({"chain:sale", "new:sale_order"})))

    def test_selected_by_its_own_sources(self) -> None:
        for path in ("co/bridge/src/VoucherSoDefines.cs", "co/bridge/src/VoucherSoDefinesSql.cs", "co/client/u8co_so_defines.py"):
            picked = select_changed([path], known_names())
            self.assertIn(_NAME, picked, path)
            self.assertLess(len(picked), 5, path)
        self.assertIn(_NAME, select_changed(["api/u8co_api/co_models_so_defines.py"], known_names()))


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, sql in sod_samples():
            self.assertEqual(sql_block_reason(sql), "", name)
            self.assertIn("ROW:", sql, name)
            self.assertNotIn("$(", sql)

    def test_picks_are_data_driven_and_skip_used_orders(self) -> None:
        text = pick_sql("plain", ("11", "12"))
        self.assertIn("m.ID NOT IN (11, 12)", text)
        self.assertIn("cVerifier", text)
        self.assertIn("N'无'", text)
        self.assertIn("NOT EXISTS", text)
        self.assertIn("cCloser", pick_sql("closed"))
        self.assertIn("EXISTS (SELECT 1 FROM dbo.DispatchLists", pick_sql("shipped"))
        self.assertIn("BETWEEN 1 AND 40", pick_sql("filled"))
        self.assertNotRegex(pick_sql("plain"), r"\b[0-9]{5,}\b")
        with self.assertRaises(ValueError):
            pick_sql("plain", ("1; DROP",))

    def test_columns_match_the_bridge_catalog(self) -> None:
        text = columns_sql()
        for needle in ("sys.columns", "UserDef_Base", "N'单据头'", "N'cDefine16'", "max_length / 2", "N'nvarchar'"):
            self.assertIn(needle, text)
        self.assertNotIn("N'cDefine17'", text)

    def test_snapshot_covers_header_lines_and_downstream(self) -> None:
        text = snapshot_sql("42")
        for needle in ("dbo.SO_SOMain", "dbo.SO_SODetails", "dbo.DispatchList", "dbo.SaleBillVouch", "t.ID = 42", "N'cdefine3'"):
            self.assertIn(needle, text)
        with self.assertRaises(ValueError):
            snapshot_sql("0")

    def test_restore_writes_only_allowed_columns(self) -> None:
        cols = {"cDefine1": "<nil>", "cDefine2": "a'b", "cDefine3": "c", "cDefine4": "d", "ufts": "x", "cMemo": "y", "cModifier": "u"}
        text = restore_sql("7", cols)
        self.assertIn("cDefine1 = NULL", text)
        self.assertIn("N'a''b'", text)
        self.assertIn("cDefine3 = N'c'", text)
        self.assertIn("cModifier = N'u'", text)
        for absent in ("ufts", "cMemo", "cDefine4"):
            self.assertNotIn(absent, text)
        self.assertFalse(writable("cDefine1; --"))
        with self.assertRaises(ValueError):
            restore_sql("7", {"ufts": "x"})


class PureTests(unittest.TestCase):
    def test_same_value(self) -> None:
        for one, two in ((None, ""), ("  ", "无"), (" 无 ", None)):
            self.assertTrue(same_value(one, two))
        self.assertTrue(same_value(" Ab1 ", "Ab1"))
        self.assertFalse(same_value("Ab1", "ab1"))
        self.assertFalse(same_value("Ab1", None))

    def test_expect_uses_other_blank_spelling_when_swapped(self) -> None:
        snap = parse_snap(["SO\t1\tcdefine1\t<nil>", "SO\t1\tcdefine2\t无", "SO\t1\tufts\tt0"])
        self.assertEqual(expect_of(snap, "1"), {"cdefine1": None, "cdefine2": "无"})
        self.assertEqual(expect_of(snap, "1", True), {"cdefine1": "无", "cdefine2": None})

    def test_columns(self) -> None:
        cols = parse_cols(["cdefine1\t1\t20\t12", "cdefine2\t1\t20\t0", "cdefine4\t0\t8\t0", "cdefine5\t1\t0\t30", "bad"])
        self.assertEqual(cols, {"cdefine1": (True, 12), "cdefine2": (True, 20), "cdefine4": (False, 8), "cdefine5": (True, 30)})
        self.assertEqual(non_text(cols), "cdefine4")
        self.assertEqual(non_text({"cdefine1": (True, 20)}), "")

    def test_bodies(self) -> None:
        body = sod_body("5", {"cdefine1": "A"}, {"cdefine1": None})
        self.assertEqual((body["type"], body["id"], body["fields"], body["empty_values"]), ("sale_order", 5, {"cdefine1": "A"}, ["无"]))
        self.assertTrue(body["idempotency_key"])
        self.assertNotIn("cascade", body)
        dry = sod_body("5", {"cdefine1": "A"}, {"cdefine1": ""}, cascade=True, dry=True)
        self.assertTrue(dry["dry_run"])
        self.assertIs(dry["cascade"], True)
        self.assertNotIn("idempotency_key", dry)
        self.assertEqual(set(body) - {"idempotency_key"}, {"type", "id", "fields", "expect", "empty_values"})

    def test_reply_checks(self) -> None:
        good = {"changed": True, "current": {"cdefine1": "E2E1", "cdefine2": None}, "ufts": "t1"}
        want = {"cdefine1": "E2E1", "cdefine2": ""}
        self.assertEqual(reply_write(good, True, want), "")
        self.assertIn("changed", reply_write(good, False, want))
        self.assertIn("current.cdefine1", reply_write(good, True, {"cdefine1": "e2e1", "cdefine2": ""}))
        self.assertIn("current 键", reply_write(good, True, {"cdefine1": "E2E1"}))
        self.assertIn("ufts", reply_write({k: v for k, v in good.items() if k != "ufts"}, True, want))
        self.assertIn("dry_run", reply_dry(good, True, want))
        self.assertEqual(reply_dry({**good, "dry_run": True, "mode": "validate"}, True, want), "")
        bad = {"detail": {"current": {"cdefine1": "A", "cdefine2": "无"}}}
        self.assertEqual(mismatch_problem(bad, {"cdefine1": "A", "cdefine2": None}), "")
        self.assertIn("current.cdefine1", mismatch_problem(bad, {"cdefine1": "B", "cdefine2": None}))
        self.assertIn("current 键", mismatch_problem({"detail": {"current": {"cdefine2": "A"}}}, {"cdefine1": "A"}))
        self.assertIn("current", mismatch_problem({}, {"cdefine1": "A"}))

    def test_write_problem_allows_only_the_requested_columns_and_stamps(self) -> None:
        before = parse_snap(
            ["SO\t1\tcdefine1\t<nil>", "SO\t1\tcmodifier\t<nil>", "SO\t1\tcmemo\th:aa", "SO\t1\tufts\tt0", "SD\t10\tcmemo\th:bb"]
        )
        after = parse_snap(
            ["SO\t1\tcdefine1\tE2E1", "SO\t1\tcmodifier\tsvc", "SO\t1\tcmemo\th:aa", "SO\t1\tufts\tt1", "SD\t10\tcmemo\th:bb"]
        )
        self.assertEqual(write_problem(before, after, {"cdefine1": "E2E1"}), ("", ["cmodifier"]))
        self.assertIn("cdefine1", write_problem(before, after, {"cdefine1": "OTHER"})[0])
        self.assertIn("cdefine2", write_problem(before, after, {"cdefine1": "E2E1", "cdefine2": "K"})[0])
        self.assertIn("不该变", write_problem(before, after, {"cdefine3": "E2E1"})[0])
        same_ufts = parse_snap(["SO\t1\tcdefine1\tE2E1", "SO\t1\tcmemo\th:aa", "SO\t1\tufts\tt0", "SD\t10\tcmemo\th:bb"])
        self.assertIn("ufts", write_problem(before, same_ufts, {"cdefine1": "E2E1"})[0])

    def test_untouched_counts_the_row_version(self) -> None:
        one = parse_snap(["SO\t1\tcdefine1\tA", "SO\t1\tufts\tt0"])
        two = parse_snap(["SO\t1\tcdefine1\tA", "SO\t1\tufts\tt1"])
        self.assertEqual(untouched_problem(one, one, "x"), "")
        self.assertIn("ufts", untouched_problem(one, two, "x"))


class ReplayTests(unittest.TestCase):
    def _go(self, db: Db | None = None, **flags: bool) -> tuple[str, str, Client, Db]:
        db = db or Db()
        client = Client(db, **flags)
        status, note = run_case(client, db)
        return status, note, client, db

    def test_passes_on_a_compliant_route_and_restores_everything(self) -> None:
        start = Db().cells()
        status, note, client, db = self._go()
        self.assertEqual(status, "PASS", note)
        self.assertIn(_ALL, note)
        self.assertEqual(db.cells(), start)
        self.assertIn("没有修改人类列变化", note)
        self.assertIsNone(db.so[100]["clocker"])
        self.assertEqual(db.so[101]["cdefine1"], "无")
        self.assertEqual(db.so[102]["cdefine1"], "")
        self.assertEqual(db.so[103]["cdefine1"], "KH-77")
        for _route, body in client.calls:
            self.assertEqual(_route, "/v1/vouchers/so_defines")
            self.assertEqual(body["empty_values"], ["无"], body)
            self.assertEqual(set(body["expect"]), set(body["fields"]), body)

    def test_two_columns_then_case_change_then_single_column(self) -> None:
        _status, _note, client, _db = self._go()
        writes = [body for body in _writes(client, 100) if body["fields"].get("cdefine1", "").startswith(("E2E", "e2e"))]
        self.assertEqual({tuple(sorted(body["fields"])) for body in writes}, {("cdefine1", "cdefine2")})
        self.assertTrue([body for body in writes if body["fields"]["cdefine1"].startswith("e2e")])
        single = [body for body in _writes(client, 100) if set(body["fields"]) == {"cdefine3"}]
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0]["expect"], {"cdefine3": "C3 old"})
        self.assertFalse([body for body in _writes(client, 101) if "cdefine3" in body["fields"]])

    def test_swapped_samples_send_the_empty_mark_in_expect(self) -> None:
        _status, _note, client, _db = self._go()

        def first(so_id: int) -> dict:
            return next(body for body in _writes(client, so_id) if not str(body["expect"]["cdefine1"]).startswith("NOPE"))

        self.assertEqual(first(101)["expect"], {"cdefine1": None, "cdefine2": "无"})
        self.assertEqual(first(103)["expect"], {"cdefine1": "KH-77", "cdefine2": "无"})

    def test_negative_paths_cover_keys_and_columns(self) -> None:
        _status, _note, client, _db = self._go()
        keys = {key for body in _writes(client, 100) for key in body["fields"]}
        self.assertTrue({"cmemo", "cdefine17", "cdefine4"} <= keys)
        values = {body["fields"].get("cdefine1") for body in _writes(client, 100)}
        self.assertTrue({"无", "   ", "", "X" * 21} <= values)

    def test_every_write_carries_an_idempotency_key_and_dry_runs_do_not(self) -> None:
        _status, _note, client, _db = self._go()
        for _route, body in client.calls:
            self.assertEqual(bool(body.get("idempotency_key")), not body.get("dry_run"), body)

    def test_replay_sends_the_same_key_twice(self) -> None:
        _status, _note, client, _db = self._go()
        keys = [body["idempotency_key"] for body in _writes(client, 100) if body.get("idempotency_key")]
        self.assertGreater(len(keys) - len(set(keys)), 0)

    def test_stamp_columns_are_reported_and_restored(self) -> None:
        start = Db().cells()
        status, note, _client, db = self._go(stamp=True)
        self.assertEqual(status, "PASS", note)
        self.assertIn("cmodifier", note)
        self.assertEqual(db.cells(), start)

    def test_missing_kinds_and_columns_are_noted_and_skipped(self) -> None:
        db = Db()
        db.absent = {"plain", "plain_any", "closed"}
        db.cols["cdefine3"] = (True, 4)
        db.cols["cdefine4"] = (True, 20)
        status, note, _client, _db = self._go(db)
        self.assertEqual(status, "PASS", note)
        self.assertIn("没有两列为空、未关闭且没有发货单的已审核订单", note)
        self.assertIn("shipped+filled", note)
        self.assertIn("单列写入", note)
        self.assertIn("非文本列（", note.split("跳过")[1])

    def test_na_when_nothing_to_pick_or_columns_too_narrow(self) -> None:
        db = Db()
        db.absent = set(db.picks)
        status, note, client, _db = self._go(db)
        self.assertEqual(status, "N/A")
        self.assertIn("没有", note)
        self.assertEqual(client.calls, [])
        db = Db()
        db.cols["cdefine2"] = (True, 4)
        status, note, client, _db = self._go(db)
        self.assertEqual((status, client.calls), ("N/A", []))
        self.assertIn("cdefine2", note)

    def _fails(self, text: str, restored: bool = True, **flags: bool) -> None:
        start = Db().cells()
        status, note, _client, db = self._go(**flags)
        self.assertEqual(status, "FAIL", note)
        self.assertIn(text, note)
        if restored:
            self.assertEqual(db.cells(), start, "失败时也要还原")

    def test_fails_when_a_dry_run_writes(self) -> None:
        self._fails("预演", dry_writes=True)

    def test_fails_when_order_lines_are_touched(self) -> None:
        # 订单行、发货单不在兜底还原的范围（只写回自定义项类列）：报「还原后快照仍有差异」，留给会话快照。
        self._fails("不该变", restored=False, touch_lines=True)

    def test_fails_when_downstream_dispatch_is_touched(self) -> None:
        self._fails("不该变", restored=False, touch_dispatch=True)

    def test_fails_when_row_version_does_not_move(self) -> None:
        self._fails("ufts", no_ufts=True)

    def test_fails_when_replay_is_not_idempotent(self) -> None:
        self._fails("幂等重放", no_replay=True)

    def test_fails_when_case_is_folded(self) -> None:
        self._fails("大小写不同", casefold=True)

    def test_fails_when_current_has_unrequested_keys(self) -> None:
        self._fails("current 键", extra_current=True)

    def test_fails_when_a_non_text_column_is_accepted(self) -> None:
        # 假路由把非文本列也写进了订单表头：不在兜底还原的范围，报「还原后快照仍有差异」。
        self._fails("非文本列 cdefine4", restored=False, any_column=True)

    def test_fails_when_column_errors_lack_the_field(self) -> None:
        self._fails("field", no_field=True)

    def test_fails_when_existing_values_are_not_overwritten(self) -> None:
        db = Db()
        db.absent = {"plain", "plain_any", "closed", "shipped"}
        start = Db().cells()
        status, note, _client, db = self._go(db, no_overwrite=True)
        self.assertEqual(status, "FAIL", note)
        self.assertIn("filled：", note)
        self.assertEqual(db.cells(), start)


if __name__ == "__main__":
    unittest.main()
