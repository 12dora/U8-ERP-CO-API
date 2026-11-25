"""发货单表头修正用例 `voucher-head-fix` 的登记、分线、SQL 守卫、纯函数和一轮回放。不连接服务器。

假库和假路由在 hf_fake.py。
"""

from __future__ import annotations

import copy
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e.casespec import head_fix as mod
from co.e2e.casespec import head_fix_ctx as ctx_mod
from co.e2e.casespec.head_fix_bodies import (
    already_new_problem,
    ar_report,
    change_problem,
    changes,
    downstream_of,
    fingerprint,
    hf_body,
    norm_val,
    parse_snap,
    reply_hf,
    reply_hf_dry,
    same_problem,
    undo_of,
    updated_problem,
    view_of,
    want_changes,
)
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.arc_write import CORE_HANDLERS
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.head_fix_sql import (
    already_new_pick_sql,
    hf_samples,
    merged_new_pick_sql,
    merged_pick_sql,
    null_pick_sql,
    reset_sql,
    single_pick_sql,
    snapshot_sql,
)
from co.e2e.casespec.head_fix_refuse_sql import (
    empty_person_pick_sql,
    not_reversible_pick_sql,
    refuse_samples,
    unaudited_pick_sql,
)
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support
from co.e2e.tests.hf_fake import NIL, Client, Db

_NAME = "voucher-head-fix"
_KINDS = "single+merged+merged_new+already_new+null"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(client: Client) -> SimpleNamespace:
    op = SimpleNamespace(acc="801", year="2026", code="op001", password="pw")
    return SimpleNamespace(client=client, operator=op, year="2026", today="2026-09-28", operator_name="", sql_n=0)


def _run(client: Client, db: Db) -> tuple[str, str]:
    spec = mod.headfix_cases()[0]
    with ExitStack() as stack:
        for module in (mod, ctx_mod):
            stack.enter_context(mock.patch.object(module, "run_guarded", db.guarded))
        return mod.step_headfix(_env(client), spec, spec.steps[0])


def _start() -> dict:
    return Db().cells()


def _diffs(client: Client, did: int) -> list[set]:
    """对这张发货单的、真的改了库的调用的格子变化（正向、反向各一份，按调用顺序）。"""
    pairs = zip((body for _route, body in client.calls), client.diffs, strict=True)
    return [diff for body, diff in pairs if body["id"] == did and diff]


def _undos(client: Client, did: int) -> list[tuple[dict, dict | None]]:
    """对这张发货单的、真写的调用：(请求, 响应)。"""
    rows = zip((body for _route, body in client.calls), client.replies, strict=True)
    return [(body, reply) for body, reply in rows if body["id"] == did and reply and not body.get("dry_run") and reply.get("changed")]


class CatalogTests(unittest.TestCase):
    def test_case_registered(self) -> None:
        (spec,) = mod.headfix_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(spec.requires_login)
        self.assertEqual(spec.data, ())
        self.assertEqual(spec.routes, ("/v1/vouchers/head_fix",))
        self.assertEqual(spec.steps[0].kind, "core_headfix")
        self.assertIs(CORE_HANDLERS["core_headfix"], mod.step_headfix)
        self.assertIs(_HANDLERS["core_headfix"], mod.step_headfix)

    def test_tags_keep_it_apart_from_sale_chain_and_address_cases(self) -> None:
        (spec,) = mod.headfix_cases()
        tags = auto_tags(spec)
        self.assertIn("chain:sale", tags)
        self.assertIn("arc:customer_address", tags)
        self.assertTrue(conflicts(tags, frozenset({"chain:sale", "new:dispatch"})))
        self.assertTrue(conflicts(tags, frozenset({"arc:customer_address"})))


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, sql in hf_samples() + refuse_samples():
            self.assertEqual(sql_block_reason(sql), "", name)
            self.assertIn("ROW:", sql, name)
            self.assertNotIn("$(", sql)

    def test_picks_are_data_driven(self) -> None:
        picks = (
            single_pick_sql(),
            single_pick_sql(False),
            merged_pick_sql(),
            merged_pick_sql(("7", "8")),
            merged_new_pick_sql(("7",)),
            already_new_pick_sql(("7",)),
            null_pick_sql(),
            null_pick_sql(False),
            empty_person_pick_sql(("7",)),
        )
        for sql in picks:
            self.assertEqual(sql_block_reason(sql), "")
            self.assertIn("d.cVerifier IS NOT NULL", sql)
            self.assertIn("dPInValidDate", sql)
            self.assertNotRegex(sql, r"N'[A-Za-z]*\d[A-Za-z0-9]*'")  # 没有写死的编码字面量
        for sql in picks[:-1]:
            self.assertIn("dbo.CusDeliverAdd", sql)
        for sql in picks:  # 原值必须可回滚：原地址为空或在档案里；原业务员存在、有效、部门一致
            self.assertIn("ra.cAddCode = d.caddcode", sql)
        for sql in picks[:-1]:
            self.assertIn("rp.cPersonCode = d.cPersonCode", sql)
            self.assertIn("rp.cDepCode = d.cDepCode", sql)
            self.assertIn("rp.dPInValidDate", sql)
        self.assertIn("a.cPerson = d.cPersonCode", single_pick_sql(True))
        self.assertNotIn("a.cPerson = d.cPersonCode", single_pick_sql(False))
        self.assertIn("COUNT(DISTINCT l2.DLID)", merged_pick_sql())
        self.assertIn("d.DLID NOT IN (7, 8)", merged_pick_sql(("7", "8")))
        self.assertIn("AND d.caddcode IS NULL AND", null_pick_sql())
        with self.assertRaises(ValueError):
            merged_pick_sql(("7; DROP",))
        for sql in picks:  # 每个挑单只取一张：最新的发货单
            self.assertIn("SELECT TOP (1) N'ROW:'", sql)
            self.assertTrue(sql.rstrip().endswith("ORDER BY d.DLID DESC;"))

    def test_merged_new_takes_the_value_of_the_other_dispatch(self) -> None:
        sql = merged_new_pick_sql()
        self.assertIn("SELECT TOP (1) o.caddcode AS c", sql)
        self.assertIn("SELECT TOP (1) o.cPersonCode AS c", sql)
        self.assertIn("o2.DLID <> d.DLID", sql)  # 其它来源发货单必须都等于新值
        self.assertIn("a.cAddCode = na.c", sql)  # 新地址须是本客户的收货地址
        self.assertIn("p.cDepCode = d.cDepCode", sql)  # 新业务员须在发货单部门

    def test_already_new_needs_the_outgoing_header_to_hold_both_new_values(self) -> None:
        self.assertIn("h.caddcode = na.c AND h.cPersonCode = np.c", already_new_pick_sql())

    def test_not_reversible_picks_select_unreversible_originals(self) -> None:
        addr = not_reversible_pick_sql("addr", ("7",))
        self.assertIn("NOT EXISTS (SELECT 1 FROM dbo.CusDeliverAdd AS ra", addr)
        self.assertIn("rp.cPersonCode = d.cPersonCode", addr)  # 业务员本身是可回滚的，拒绝原因才唯一
        self.assertIn("d.DLID NOT IN (7)", addr)
        person = not_reversible_pick_sql("person")
        self.assertIn("NOT EXISTS (SELECT 1 FROM dbo.Person AS rp", person)
        self.assertIn("ra.cAddCode = d.caddcode", person)
        for sql in (addr, person):
            self.assertEqual(sql_block_reason(sql), "")
            self.assertNotRegex(sql, r"N'[A-Za-z]*\d[A-Za-z0-9]*'")
        with self.assertRaises(ValueError):
            not_reversible_pick_sql("dept")

    def test_unaudited_pick(self) -> None:
        sql = unaudited_pick_sql(("7",))
        self.assertEqual(sql_block_reason(sql), "")
        self.assertIn("(d.cVerifier IS NULL OR LTRIM(RTRIM(d.cVerifier)) = N'')", sql)
        self.assertNotIn("d.cVerifier IS NOT NULL", sql)
        self.assertIn("d.DLID NOT IN (7)", sql)
        self.assertNotRegex(sql, r"N'[A-Za-z]*\d[A-Za-z0-9]*'")

    def test_empty_person_pick(self) -> None:
        sql = empty_person_pick_sql()
        self.assertIn("LTRIM(RTRIM(d.cPersonCode)) = N''", sql)
        self.assertIn("d.cPersonCode IS NULL", sql)

    def test_snapshot_covers_all_touched_tables(self) -> None:
        sql = snapshot_sql("1000")
        for tag in ("DL", "DS", "RD", "SB", "AR", "SO"):
            self.assertIn(f"N'ROW:{tag}'", sql)
        for table in ("dbo.DispatchList ", "dbo.rdrecord32 ", "dbo.SaleBillVouch ", "dbo.Ar_Detail ", "dbo.SO_SOMain "):
            self.assertIn(table, sql)
        self.assertIn("N'cprocstyle'", sql)  # 往来明细的处理方式、核销号要原值，写进用例说明
        self.assertIn("N'ccancelno'", sql)
        self.assertNotIn("UPDATE", sql)
        with self.assertRaises(ValueError):
            snapshot_sql("1000; DROP")

    def test_reset_only_writes_target_columns(self) -> None:
        sql = reset_sql("DL", "7", {"caddcode": NIL, "cPersonCode": "P'1", "cShipAddress": "x"})
        self.assertIn("caddcode = NULL", sql)
        self.assertIn("cPersonCode = N'P''1'", sql)
        self.assertNotIn("cShipAddress", sql)
        with self.assertRaises(ValueError):
            reset_sql("DL", "7", {"cShipAddress": "x"})
        with self.assertRaises(ValueError):
            reset_sql("SO", "7", {"cmemo": "x"})


class PureTests(unittest.TestCase):
    def _snap(self) -> dict:
        return parse_snap(
            [
                "DL\t1\tcaddcode\t01",
                "DL\t1\tcPersonCode\tP900001",
                "DL\t1\tufts\th:a",
                "RD\t2\tcaddcode\t01",
                "RD\t2\tcPersonCode\tP900001",
                "SB\t3\tcsbvcode\tSB3",
                "SB\t3\tcvouchtype\t26",
                "AR\t5\tcvouchtype\t26",
                "AR\t5\tcvouchid\tSB3",
                "AR\t5\tcperson\tP900001",
                "AR\t5\tcprocstyle\t9M",
                "AR\t5\tccancelno\t<nil>",
                "AR\t6\tcvouchtype\t26",
                "AR\t6\tcvouchid\tSB3",
                "AR\t6\tcperson\tP900009",
                "AR\t7\tcvouchtype\t26",
                "AR\t7\tcvouchid\tSBX",
                "AR\t7\tcperson\tP900001",
                "noise",
            ]
        )

    def _undo(self) -> dict:
        return {
            "addr_code": {"rdrecord32": [2], "SaleBillVouch": [3]},
            "person_code": {"rdrecord32": [2], "SaleBillVouch": [3], "Ar_Detail": [5]},
        }

    def _down(self) -> list[dict]:
        return [
            {"table": "rdrecord32", "id": 2, "action": "updated", "fields": ["addr_code", "person_code"]},
            {"table": "SaleBillVouch", "id": 3, "action": "updated", "fields": ["addr_code", "person_code"]},
            {"table": "Ar_Detail", "invoice": "SB3", "rows": 1, "action": "updated"},
        ]

    def test_parse_view_and_fingerprint(self) -> None:
        snap = self._snap()
        self.assertEqual(view_of(snap), {"addr_code": "01", "person_code": "P900001"})
        other = copy.deepcopy(snap)
        other["DL"]["1"]["ufts"] = "h:b"
        self.assertEqual(fingerprint(snap), fingerprint(other))
        other["DL"]["1"]["caddcode"] = "02"
        self.assertNotEqual(fingerprint(snap), fingerprint(other))

    def test_target_columns_are_compared_after_normalisation(self) -> None:
        snap = self._snap()
        other = copy.deepcopy(snap)
        other["DL"]["1"]["caddcode"] = "A1"
        snap["DL"]["1"]["caddcode"] = "a1"
        self.assertEqual(changes(snap, other), {})  # 大小写不分
        self.assertEqual(len(changes(snap, other, False)), 1)
        other["DL"]["1"]["caddcode"] = "a1"
        other["RD"]["2"]["caddcode"] = ""
        snap["RD"]["2"]["caddcode"] = NIL
        self.assertEqual(changes(snap, other), {})  # 空串与 NULL 相同
        self.assertEqual(list(changes(snap, other, False)), [("RD", "2", "caddcode")])
        self.assertEqual(same_problem(snap, other), "")
        self.assertIn("RD.2.caddcode", same_problem(snap, other, False))
        self.assertEqual(norm_val(" Ab "), "ab")
        self.assertEqual(norm_val(NIL), "")

    def test_other_columns_are_compared_exactly(self) -> None:
        snap = self._snap()
        other = copy.deepcopy(snap)
        snap["SB"]["3"]["csbvcode"] = "SB3"
        other["SB"]["3"]["csbvcode"] = "sb3"  # 不是目标列：大小写也算改了
        other["AR"]["5"]["cprocstyle"] = "9m"
        self.assertEqual(sorted(changes(snap, other)), [("AR", "5", "cprocstyle"), ("SB", "3", "csbvcode")])

    def test_stamps_are_ignored_and_rows_that_vanish_are_changes(self) -> None:
        snap = self._snap()
        other = copy.deepcopy(snap)
        other["DL"]["1"]["ufts"] = "h:z"
        self.assertEqual(changes(snap, other), {})
        del other["AR"]["6"]
        self.assertIn(("AR", "6", "cvouchid"), changes(snap, other))

    def test_undo_of_normalises_and_rejects_bad_shapes(self) -> None:
        self.assertEqual(undo_of({"undo": {"addr_code": {"rdrecord32": [3, 1], "SaleBillVouch": []}}}), {"addr_code": {"rdrecord32": [1, 3]}})
        self.assertEqual(undo_of({"undo": {}}), {})
        for bad in ({}, {"undo": []}, {"undo": {"x": {}}}, {"undo": {"addr_code": {"Foo": [1]}}}, {"undo": {"addr_code": {"rdrecord32": ["1"]}}}):
            self.assertIsNone(undo_of(bad), bad)

    def test_want_changes_come_from_undo_and_are_cross_checked(self) -> None:
        snap = self._snap()
        fields = {"addr_code": "02", "person_code": "P900002"}
        want, why = want_changes(snap, fields, self._down(), self._undo())
        self.assertEqual(why, "")
        self.assertEqual(
            want,
            {
                ("DL", "1", "caddcode"): "02",
                ("DL", "1", "cpersoncode"): "P900002",
                ("RD", "2", "caddcode"): "02",
                ("RD", "2", "cpersoncode"): "P900002",
                ("SB", "3", "caddcode"): "02",
                ("SB", "3", "cpersoncode"): "P900002",
                ("AR", "5", "cperson"): "P900002",
            },
        )

    def test_undo_must_match_the_response(self) -> None:
        snap = self._snap()
        fields = {"addr_code": "02", "person_code": "P900002"}
        short = {**self._undo(), "addr_code": {"rdrecord32": [2]}}
        self.assertIn("不一致", want_changes(snap, fields, self._down(), short)[1])
        rows = self._down()[:2] + [{"table": "Ar_Detail", "invoice": "SB3", "rows": 2, "action": "updated"}]
        self.assertIn("Ar_Detail 行数", want_changes(snap, fields, rows, self._undo())[1])
        self.assertIn("不在快照里", want_changes(snap, fields, self._down(), {"addr_code": {"rdrecord32": [9]}})[1])
        self.assertIn("没改的字段", want_changes(snap, {"addr_code": "02"}, self._down(), self._undo())[1])

    def test_partial_update_follows_the_fields_list(self) -> None:
        snap = self._snap()
        fields = {"addr_code": "02", "person_code": "P900002"}
        down = [{"table": "rdrecord32", "id": 2, "action": "updated", "fields": ["addr_code"]}]
        undo = {"addr_code": {"rdrecord32": [2]}}
        want, why = want_changes(snap, fields, down, undo)
        self.assertEqual(why, "")
        self.assertIn(("RD", "2", "caddcode"), want)
        self.assertNotIn(("RD", "2", "cpersoncode"), want)
        self.assertIn("只更新了", updated_problem(down, "rdrecord32", "2", ("addr_code", "person_code")))
        self.assertEqual(updated_problem(down, "rdrecord32", "2", ("addr_code",)), "")
        self.assertIn("应被更新", updated_problem([{"table": "Ar_Detail", "action": "none"}], "Ar_Detail", ""))

    def test_receivable_rows_must_be_the_invoice_own_rows(self) -> None:
        snap = self._snap()
        fields = {"addr_code": "02", "person_code": "P900002"}
        foreign = {**self._undo(), "person_code": {**self._undo()["person_code"], "Ar_Detail": [5, 7]}}
        self.assertIn("不是发票自己的行", want_changes(snap, fields, self._down(), foreign)[1])
        extra = {**self._undo(), "person_code": {**self._undo()["person_code"], "Ar_Detail": [5, 6]}}
        self.assertIn("cPerson 为旧值", want_changes(snap, fields, self._down(), extra)[1])
        # 发票自己 cPerson 为旧值的行一行都没改：正向要报；回滚（exact=False）只看归属
        none = {**self._undo(), "person_code": {"rdrecord32": [2], "SaleBillVouch": [3]}}
        down = self._down()[:2]
        self.assertIn("cPerson 为旧值", want_changes(snap, fields, down, none)[1])
        self.assertEqual(want_changes(snap, fields, down, none, False)[1], "")

    def test_ar_report_lists_process_style_and_cancel_no(self) -> None:
        snap = self._snap()
        self.assertEqual(ar_report(snap, self._undo()), "Ar_Detail 9M/-×1")
        self.assertEqual(ar_report(snap, {"addr_code": {"rdrecord32": [2]}}), "")

    def test_clearing_expects_nil(self) -> None:
        want, _why = want_changes(self._snap(), {"addr_code": ""}, [], {})
        self.assertEqual(want, {("DL", "1", "caddcode"): NIL})

    def test_change_problem_names_each_kind(self) -> None:
        snap = self._snap()
        after = copy.deepcopy(snap)
        after["DL"]["1"]["caddcode"] = "02"
        after["SB"]["3"]["csbvcode"] = "SBX"
        text = change_problem(snap, after, {("DL", "1", "caddcode"): "03", ("DL", "1", "cpersoncode"): "P2"})
        self.assertIn("多改了 SB.3.csbvcode", text)
        self.assertIn("没改到 DL.1.cpersoncode", text)
        self.assertIn("值不对 DL.1.caddcode", text)
        self.assertEqual(change_problem(snap, after, {("DL", "1", "caddcode"): "02", ("SB", "3", "csbvcode"): "SBX"}), "")
        loose = copy.deepcopy(snap)
        loose["DL"]["1"]["caddcode"] = "AB"
        self.assertEqual(change_problem(snap, loose, {("DL", "1", "caddcode"): "ab"}), "")

    def test_bodies(self) -> None:
        dry = hf_body("1000", {"addr_code": "02"}, {"addr_code": "01"}, True)
        self.assertTrue(dry["dry_run"])
        self.assertNotIn("idempotency_key", dry)
        self.assertNotIn("undo", dry)
        self.assertEqual((dry["type"], dry["id"]), ("dispatch", 1000))
        real = hf_body("1000", {"addr_code": ""}, {"addr_code": "01"}, undo={"addr_code": {"rdrecord32": (1, 2)}})
        self.assertTrue(real["idempotency_key"].startswith("e2e-headfix-"))
        self.assertEqual(real["fields"], {"addr_code": ""})
        self.assertEqual(real["undo"], {"addr_code": {"rdrecord32": [1, 2]}})
        self.assertNotIn("expect", hf_body("1000", {"addr_code": "02"}, None))
        self.assertEqual(hf_body("1000", {"addr_code": "02"}, {"addr_code": "01"}, undo={})["undo"], {})

    def test_reply_checks(self) -> None:
        good = {"ok": True, "type": "dispatch", "id": 1000, "changed": True, "before": {"addr_code": "01"}, "after": {"addr_code": None}}
        views = ({"addr_code": "01"}, {"addr_code": ""})
        self.assertEqual(reply_hf(good, "1000", True, views), "")
        self.assertIn("changed", reply_hf(good, "1000", False, views))
        self.assertIn("id", reply_hf(good, "7", True, views))
        self.assertIn("type", reply_hf({**good, "type": "x"}, "1000", True, views))
        self.assertIn("after", reply_hf({**good, "after": {"addr_code": "02"}}, "1000", True, views))
        self.assertIn("before", reply_hf({**good, "before": []}, "1000", True, views))
        dry = {"ok": True, "type": "dispatch", "id": 1000, "dry_run": True, "mode": "validate", "before": {"addr_code": "01"}}
        self.assertEqual(reply_hf_dry(dry, "1000", {"addr_code": "01"}), "")
        self.assertIn("dry_run", reply_hf_dry({**dry, "dry_run": False}, "1000", {"addr_code": "01"}))
        self.assertEqual(downstream_of({"downstream": [{"a": 1}, "x"]}), [{"a": 1}])
        self.assertEqual(downstream_of({}), [])

    def test_already_new_problem(self) -> None:
        good = [{"table": "rdrecord32", "id": 2, "action": "skipped", "reason": "already_new"}]
        self.assertEqual(already_new_problem(good, "rdrecord32", "2"), "")
        self.assertIn("already_new", already_new_problem([{**good[0], "reason": "differs"}], "rdrecord32", "2"))
        self.assertIn("already_new", already_new_problem([{**good[0], "action": "updated"}], "rdrecord32", "2"))
        self.assertIn("already_new", already_new_problem([], "rdrecord32", "2"))


class ReplayTests(unittest.TestCase):
    def test_passes_and_restores_every_touched_cell(self) -> None:
        db = Db()
        client = Client(db)
        verdict, note = _run(client, db)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(db.cells(), _start())
        self.assertIn(_KINDS, note)
        self.assertIn("9M/-×1", note)  # 往来明细的处理方式、核销号
        self.assertNotIn("跳过", note)

    def test_forward_changes_only_target_columns(self) -> None:
        db = Db()
        client = Client(db)
        _run(client, db)
        forward, backward = _diffs(client, 1000)
        want = {
            ("dl", 1000, "caddcode"),
            ("dl", 1000, "cpersoncode"),
            ("rd", 2000, "caddcode"),
            ("rd", 2000, "cpersoncode"),
            ("sb", 3000, "caddcode"),
            ("sb", 3000, "cpersoncode"),
            ("ar", 5000, "cperson"),
            ("ar", 5001, "cperson"),
        }
        self.assertEqual(forward, want)
        self.assertEqual(backward, want)

    def test_every_reverse_call_carries_the_forward_undo(self) -> None:
        db = Db()
        client = Client(db)
        _run(client, db)
        for did in (1000, 1100, 1200, 1300, 1400):
            (fwd_body, fwd_reply), (back_body, back_reply) = _undos(client, did)[:2]
            self.assertNotIn("undo", fwd_body, did)
            self.assertEqual(back_body["undo"], fwd_reply["undo"], did)
            self.assertEqual(back_reply["undo"], fwd_reply["undo"], did)  # 回滚响应的 undo 是再次回滚用的，行相同
        undo = _undos(client, 1000)[0][1]["undo"]
        self.assertEqual(undo["addr_code"], {"rdrecord32": [2000], "SaleBillVouch": [3000]})
        self.assertEqual(undo["person_code"]["Ar_Detail"], [5000, 5001])  # 发票自己的行，不含别的业务员和别的发票的行

    def test_shared_invoice_is_skipped_and_reverse_restores(self) -> None:
        db = Db()
        client = Client(db)
        _run(client, db)
        merged = _diffs(client, 1100)
        self.assertEqual(len(merged), 2)  # 正向、反向
        for diff in merged:
            self.assertFalse(any(cell[0] in ("sb", "ar") for cell in diff))
            self.assertTrue(any(cell[0] == "rd" for cell in diff))
        self.assertEqual(_undos(client, 1100)[0][1]["undo"], {"addr_code": {"rdrecord32": [2100]}, "person_code": {"rdrecord32": [2100]}})

    def test_invoice_updated_when_the_other_dispatch_already_holds_the_new_value(self) -> None:
        db = Db()
        client = Client(db)
        _run(client, db)
        forward, backward = _diffs(client, 1300)
        for diff in (forward, backward):
            self.assertTrue({("sb", 3300, "caddcode"), ("sb", 3300, "cpersoncode"), ("ar", 5300, "cperson")} <= diff)
        self.assertEqual(db.sb[3300]["caddcode"], "01")  # 带 undo 的反向调用把发票恢复了
        self.assertEqual(db.dl[1301]["caddcode"], "02")  # 另一张发货单没动

    def test_row_that_already_holds_the_new_value_is_left_alone(self) -> None:
        db = Db()
        client = Client(db)
        _run(client, db)
        for diff in _diffs(client, 1400):
            self.assertFalse(any(cell[:2] == ("rd", 2400) for cell in diff))
        reply = _undos(client, 1400)[0][1]
        entry = next(item for item in reply["downstream"] if item["table"] == "rdrecord32")
        self.assertEqual((entry["action"], entry["reason"]), ("skipped", "already_new"))
        self.assertEqual((db.rd[2400]["caddcode"], db.rd[2400]["cpersoncode"]), ("02", "P900002"))

    def test_null_address_round_trip(self) -> None:
        db = Db()
        client = Client(db)
        _run(client, db)
        sent = [body for _route, body in client.calls if body["id"] == 1200 and not body.get("dry_run")]
        pairs = [(body["fields"], body.get("expect")) for body in sent]
        self.assertEqual(pairs[0], ({"addr_code": "02"}, {"addr_code": ""}))  # 先填
        self.assertIn(({"addr_code": ""}, {"addr_code": "02"}), pairs)  # 再用空串清回
        self.assertEqual(pairs[-1], ({"addr_code": ""}, {"addr_code": ""}))  # 已是 NULL 再清：无事可做
        back = next(body for body in sent if body["fields"] == {"addr_code": ""} and body["expect"] == {"addr_code": "02"})
        self.assertEqual(back["undo"], {"addr_code": {"rdrecord32": [2200], "SaleBillVouch": [3200]}})
        self.assertIsNone(db.dl[1200]["caddcode"])
        self.assertIsNone(db.rd[2200]["caddcode"])
        self.assertIsNone(db.sb[3200]["caddcode"])

    def test_covers_every_refusal(self) -> None:
        db = Db()
        client = Client(db)
        _run(client, db)
        single = [body for _route, body in client.calls if body["id"] == 1000]
        self.assertTrue(single[0]["dry_run"])
        self.assertTrue(any(body["type"] == "sale_out" for body in single))
        self.assertTrue(any(body["fields"].get("person_code") == "" for body in single))
        self.assertTrue(any("memo" in body["fields"] for body in single))
        self.assertTrue(any("expect" not in body for body in single))
        self.assertTrue(any(str(body["fields"].get("addr_code", "")).startswith("ZZNOA") for body in single))
        self.assertTrue(any(str(body["fields"].get("person_code", "")).startswith("ZZNOP") for body in single))
        self.assertTrue(any(body["fields"].get("person_code") == "P900003" for body in single))
        mismatch = [body for body in single if body.get("expect") == {"addr_code": "00", "person_code": "P900000"}]
        self.assertEqual(len(mismatch), 1)  # expect 不符：改最后一个字符，长度不变，不会因超长变成 400
        self.assertTrue(any(body["id"] > max(db.dl) for _route, body in client.calls))
        self.assertTrue(any(body.get("undo", {}).get("addr_code") == {"Ar_Detail": [1]} for body in single))  # 表不属于该字段
        self.assertTrue(all("idempotency_key" in body for body in single if not body.get("dry_run")))

    def test_undo_row_not_at_expect_is_refused(self) -> None:
        db = Db()
        client = Client(db)
        _run(client, db)
        calls = [body for _route, body in client.calls if body["id"] == 1100 and body.get("undo") == {"addr_code": {"SaleBillVouch": [3100]}}]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["fields"], {"addr_code": "01", "person_code": "P900001"})
        self.assertEqual(calls[0]["expect"], {"addr_code": "02", "person_code": "P900002"})
        self.assertEqual(db.sb[3100]["caddcode"], "01")

    def test_person_change_on_empty_person_is_refused(self) -> None:
        db = Db()
        client = Client(db)
        _run(client, db)
        calls = [body for _route, body in client.calls if body["id"] == 1500]
        self.assertEqual([(body["fields"], body["expect"]) for body in calls], [({"person_code": "P900002"}, {"person_code": ""})])
        self.assertIsNone(db.dl[1500]["cpersoncode"])

    def test_not_reversible_originals_are_refused(self) -> None:
        db = Db()
        client = Client(db)
        _run(client, db)
        addr = [body for _route, body in client.calls if body["id"] == 1600]
        self.assertEqual([(body["fields"], body["expect"]) for body in addr], [({"addr_code": "02"}, {"addr_code": "ZZ9"})])
        person = [body for _route, body in client.calls if body["id"] == 1700]
        self.assertEqual([(body["fields"], body["expect"]) for body in person], [({"person_code": "P900002"}, {"person_code": "P9BAD"})])
        self.assertEqual(db.dl[1600]["caddcode"], "ZZ9")
        self.assertEqual(db.dl[1700]["cpersoncode"], "P9BAD")

    def test_empty_person_may_be_refused_as_not_reversible(self) -> None:
        db = Db()
        client = Client(db)
        orig = client._reversible

        def strict(row: dict, todo: dict, cur: dict, fields: dict) -> None:
            if "person_code" in todo and not cur["person_code"]:
                raise U8CoError(409, "not_reversible", "原业务员缺失")
            orig(row, todo, cur, fields)

        client._reversible = strict  # type: ignore[method-assign]
        client.flags["no_person_empty"] = True
        verdict, note = _run(client, db)
        self.assertEqual(verdict, "PASS", note)

    def test_unaudited_dispatch_is_refused_in_both_modes(self) -> None:
        db = Db()
        client = Client(db)
        _run(client, db)
        calls = [body for _route, body in client.calls if body["id"] == 1800]
        self.assertEqual([bool(body.get("dry_run")) for body in calls], [False, True])  # 真写和预演各一次
        self.assertEqual(calls[0]["fields"], {"addr_code": "02", "person_code": "P900002"})
        self.assertEqual(calls[0]["expect"], {"addr_code": "01", "person_code": "P900001"})
        self.assertIsNone(db.dl[1800]["cverifier"])
        self.assertEqual(db.dl[1800]["caddcode"], "01")

    def test_strict_pick_falls_back_to_loose_pick(self) -> None:
        db = Db()
        db.strict_empty = True
        verdict, note = _run(Client(db), db)
        self.assertEqual(verdict, "PASS", note)

    def test_missing_kinds_are_skipped_and_reported(self) -> None:
        db = Db()
        db.picks = {**db.picks, "merged": "", "merged_new": "", "already_new": "", "null": "", "empty": ""}
        db.picks.update(not_rev_addr="", not_rev_person="", unaudited="")
        verdict, note = _run(Client(db), db)
        self.assertEqual(verdict, "PASS", note)
        for label in (
            "发票合并多张发货单的发货单",
            "发票的其它来源发货单已是新值的发货单",
            "出库单已是新值的发货单",
            "地址为 NULL 的发货单",
            "业务员为空的发货单",
            "原收货地址不在档案里的发货单",
            "原业务员无效的发货单",
            "未审核的发货单",
        ):
            self.assertIn(label, note)
        self.assertEqual(db.cells(), _start())

    def test_not_applicable_without_any_sample(self) -> None:
        db = Db()
        db.picks = {key: "" for key in db.picks}
        verdict, note = _run(Client(db), db)
        self.assertEqual(verdict, "N/A", note)

    def test_pick_that_is_not_null_is_skipped(self) -> None:
        db = Db()
        db.picks = {**db.picks, "null": "1000\t02\tP900002", "single": ""}
        verdict, note = _run(Client(db), db)
        self.assertEqual(verdict, "PASS", note)
        self.assertIn("挑到的地址不是 NULL", note)


class BrokenRouteTests(unittest.TestCase):
    def _fail(self, **flags: bool) -> tuple[str, str, Db]:
        db = Db()
        verdict, note = _run(Client(db, **flags), db)
        return verdict, note, db

    def test_extra_column_touched_fails(self) -> None:
        verdict, note, _db = self._fail(touch_ship=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("多改了", note)

    def test_receivable_rows_not_updated_fails(self) -> None:
        verdict, note, _db = self._fail(skip_ar=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("Ar_Detail", note)

    def test_receivable_row_of_another_invoice_fails(self) -> None:
        verdict, note, _db = self._fail(ar_foreign=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("Ar_Detail", note)

    def test_shared_invoice_updated_fails(self) -> None:
        verdict, note, db = self._fail(force_invoice=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("应被跳过", note)
        self.assertEqual(db.sb[3100]["caddcode"], "01")  # 兜底写回

    def test_clearing_to_empty_string_fails_and_is_repaired(self) -> None:
        verdict, note, db = self._fail(clear_to_empty=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("反向调用", note)
        self.assertEqual(db.cells(), _start())

    def test_dry_run_that_writes_fails(self) -> None:
        verdict, note, _db = self._fail(dry_writes=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("预演", note)

    def test_expect_mismatch_accepted_fails(self) -> None:
        verdict, note, _db = self._fail(accept_mismatch=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("expect 不符", note)

    def test_reverse_that_ignores_undo_fails(self) -> None:
        # 另一张发货单已是新值时，重新判断会把发票当成「还没轮到」而跳过，发票留在新值上
        verdict, note, _db = self._fail(ignore_undo=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("undo", note)

    def test_response_without_undo_fails(self) -> None:
        verdict, note, db = self._fail(drop_undo=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("缺少 undo", note)
        self.assertEqual(db.cells(), _start())

    def test_person_change_on_empty_person_accepted_fails(self) -> None:
        verdict, note, _db = self._fail(no_person_empty=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("person_empty", note)

    def test_unaudited_change_accepted_fails(self) -> None:
        verdict, note, _db = self._fail(no_not_audited=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("not_audited", note)

    def test_unreversible_change_accepted_fails(self) -> None:
        verdict, note, _db = self._fail(no_not_reversible=True)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("not_reversible", note)

    def test_dropped_route_fails(self) -> None:
        db = Db()
        client = Client(db)

        def gone(_route: str, _fields: dict) -> dict:
            raise U8CoError(404, "not_found", "路由不存在")

        client.call = gone  # type: ignore[method-assign]
        verdict, note = _run(client, db)
        self.assertEqual(verdict, "FAIL", note)
        self.assertEqual(db.cells(), _start())


if __name__ == "__main__":
    unittest.main()
