"""应收制单用例的登记、接线、回读 SQL、正文和补偿。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.client.u8co_client import U8CoError

from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import rule_for, select_changed
from co.e2e.casespec.wiring import EDIT_BUILDERS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.arap_voucher import AV_BUILDERS, AV_CLEANUP, DROP, ROUTE, av_cases
from co.e2e.casespec.arap_voucher_sql import av_samples
from co.e2e.casespec.tags import auto_tags, bind_tags

_NAMES = ("ar-voucher", "ar-voucher-bad-flag")
_BAG = {"av_inv": "42", "av_pzno": "0000000004751"}


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


def _env(client: _Replay, bag: dict) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-29", bag=bag)


class ArapVoucherCaseTests(unittest.TestCase):
    def test_names_registered_once(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in av_cases()), _NAMES)
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)

    def test_builders_and_readback_are_wired(self) -> None:
        for name, builder in AV_BUILDERS.items():
            self.assertIs(EDIT_BUILDERS[name], builder)
        for name in ("av_pz", "av_glbad", "av_marks", "av_left"):
            self.assertTrue(has_edit(name), name)
        for row in av_samples():
            self.assertIn(row, edit_samples())

    def test_readback_sql(self) -> None:
        self.assertIn("SUBSTRING(d.cPZid, 3, 20)", edit_sql("av_pz", "42", {}))
        glbad = edit_sql("av_glbad", "42", _BAG)
        self.assertIn("g.coutno_id = N'AR0000000004751'", glbad)
        self.assertIn("ISNULL(g.coutsysname, N'') = N'AR'", glbad)
        self.assertIn("b.SBVID = 42", glbad)
        # 子查询不能放进聚合函数（SQL Server 130）：往来科目用 CROSS JOIN 取。
        self.assertIn("g.ccode = a.k", glbad)
        marks = edit_sql("av_marks", "42", _BAG)
        self.assertIn("d.cGLSign IS NULL", marks)
        self.assertIn("b.cClue", marks)
        left = edit_sql("av_left", "42", _BAG)
        self.assertIn("dbo.GL_accvouch AS g WHERE g.coutno_id = N'AR0000000004751'", left)
        with self.assertRaises(ValueError):
            edit_sql("av_left", "1;--", _BAG)
        with self.assertRaises(ValueError):
            edit_sql("av_glbad", "42", {"av_pzno": "1' OR 1=1"})
        with self.assertRaises(ValueError):
            edit_sql("av_marks", "42", {})

    def test_bodies(self) -> None:
        env = SimpleNamespace(bag=dict(_BAG))
        self.assertEqual(AV_BUILDERS["av_make"](env), {"flag": "AR", "type": "sale_invoice", "id": 42})
        self.assertEqual(AV_BUILDERS["av_drop"](env), {"flag": "AR", "pz_id": "AR0000000004751"})
        with self.assertRaises(ValueError):
            AV_BUILDERS["av_make"](SimpleNamespace(bag={}))
        with self.assertRaises(ValueError):
            AV_BUILDERS["av_drop"](SimpleNamespace(bag={"av_inv": "42"}))
        self.assertEqual(AV_BUILDERS["av_bad_flag"](env)["flag"], "AP")
        self.assertEqual(AV_BUILDERS["av_bad_drop"](env), {"flag": "AP", "pz_id": "AR0000000000001"})

    def test_steps_and_tags(self) -> None:
        case, bad = av_cases()
        self.assertEqual(case.needs, ("arv_inv", "arv_inv_line"))
        routes = [step.route for step in case.steps if step.kind == "apply"]
        self.assertEqual(routes.count(ROUTE), 3)
        self.assertEqual(routes.count(DROP), 2)
        makes = [(step.status, step.code) for step in case.steps if step.route == ROUTE]
        # 未审核 409、制单 200、重复制单 409。
        self.assertEqual(makes, [(409, "state_mismatch"), (200, ""), (409, "state_mismatch")])
        drops = [(step.status, step.code) for step in case.steps if step.route == DROP]
        self.assertEqual(drops, [(200, ""), (404, "not_found")])
        actions = [dict(step.fields).get("action") for step in case.steps if step.kind == "call"]
        # 弃审在弃复之前；制单在应收审核之后、取消制单在弃审之前。
        self.assertLess(actions.index("arap_unverify"), actions.index("unverify"))
        kinds = [step.kind + ":" + (step.sql_name or step.route) for step in case.steps]
        self.assertLess(kinds.index("stash:av_pz"), kinds.index("pred:av_left"))
        tags = auto_tags(case)
        for tag in ("gl", "new:gl:转", "w:sale_invoice", "new:sale_invoice"):
            self.assertIn(tag, tags)
        self.assertIn("src:dispatch:55", bind_tags(case, {"arv_inv": "55", "arv_inv_line": "56"}))
        self.assertEqual([(step.status, step.code) for step in bad.steps], [(400, "bad_request")] * 2)
        self.assertIn(case.name, AV_CLEANUP)

    def test_cleanup_is_best_effort(self) -> None:
        client = _Replay([{"ok": True}, {"ok": True}, U8CoError(409, "state_mismatch", "单据未审核")])
        note = AV_CLEANUP["ar-voucher"](_env(client, dict(_BAG)), "ar-voucher")
        self.assertIn("delete 200", note)
        self.assertIn("arap_unverify 200", note)
        self.assertIn("unverify 409", note)
        self.assertEqual([route for route, _fields in client.calls], [DROP, "/v1/vouchers/verify", "/v1/vouchers/verify"])
        self.assertEqual(client.calls[0][1]["pz_id"], "AR0000000004751")
        skipped = _Replay([{"ok": True}, {"ok": True}])
        note = AV_CLEANUP["ar-voucher"](_env(skipped, {"av_inv": "42"}), "ar-voucher")
        self.assertIn("没有外部业务号", note)
        self.assertEqual(len(skipped.calls), 2)
        self.assertEqual(AV_CLEANUP["ar-voucher"](_env(_Replay([]), {}), "ar-voucher"), "没有生成的发票")

    def test_coverage_rules(self) -> None:
        self.assertEqual(rule_for("co/bridge/src/ArapVoucher.cs")[0], "co/bridge/src/Arap*.cs")
        got = select_changed(["co/bridge/src/ArapVoucherBack.cs"], list(known_names()))
        for name in _NAMES:
            self.assertIn(name, got)


if __name__ == "__main__":
    unittest.main()
