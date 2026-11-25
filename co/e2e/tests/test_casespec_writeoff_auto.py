"""应收自动核销用例的登记、接线、挑单 SQL、正文和试算一步。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.e2e.casespec import ops_run, writeoff_auto_ops, writeoff_run, steps
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples
from co.e2e.casespec.writeoff_auto import HXA_BUILDERS, HXA_CLEANUP, ROUTE, hxa_cases
from co.e2e.casespec.writeoff_auto_sql import AUTO_KEYS, HXA_PACKS, hxa_samples
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.tags import auto_tags, bind_tags

_NAMES = ("ar-writeoff-auto", "ar-writeoff-auto-bad")
_CANCEL = "/v1/arap/writeoff/cancel"
_BAG = {
    "hxa_dw": "C001",
    "hxa_rc": "11",
    "hxa_rcl": "12",
    "hxa_inv": "13",
    "hxa_invl": "14",
    "hxa_r0": "5.00",
    "hxa_i0": "3.00",
    "hxa_cno": "0000000900002",
}


class HxaTests(unittest.TestCase):
    def test_names_registered_once(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in hxa_cases()), _NAMES)
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)

    def test_packs_builders_and_readback_are_wired(self) -> None:
        for pack in HXA_PACKS:
            self.assertIn(pack, EDIT_PACKS)
            for key in pack[1]:
                self.assertIn(key, pick_keys())
        for name, builder in HXA_BUILDERS.items():
            self.assertIs(EDIT_BUILDERS[name], builder)
        for step in hxa_cases()[0].steps:
            if step.kind in ("stash", "pred"):
                self.assertTrue(has_edit(step.sql_name), step.sql_name)
        for row in hxa_samples():
            self.assertIn(row, edit_samples())
        self.assertTrue(ops_run.has_op("hxa_dry"))

    def test_pick_sql_filters(self) -> None:
        sql = HXA_PACKS[0][3]()
        self.assertEqual(HXA_PACKS[0][2], (False, True, True, True, True))
        for text in (
            "FROM dbo.Ar_Detail AS d",
            "d.cCoVouchType IN (N'26', N'27')",
            "o.v >= 2 AND b.iRAmt_f >= 2 AND ISNULL(b.bPrePay, 0) = 0",
            "p.line < o.line",
            "h.cDwCode NOT LIKE N'%[^0-9A-Za-z._-]%'",
            "ISNULL(h.IsWfControlled, 0) = 0",
            "f.iotherused = -1",
            "ISNULL(m.bflag_AR, 0) <> 0",
        ):
            self.assertIn(text, sql)
        self.assertEqual(sql.count("N'ROW:'"), 1)

    def test_bodies(self) -> None:
        env = SimpleNamespace(bag=dict(_BAG))
        run = HXA_BUILDERS["hxa_run"](env)
        self.assertEqual((run["flag"], run["partner"], run["max_amount"], run["dry_run"]), ("AR", "C001", 1, False))
        self.assertEqual(run["receipt"], {"type": "ar_receipt", "id": 11, "line_id": 12})
        self.assertEqual(run["targets"], [{"type": "sale_invoice", "id": 13}])
        self.assertEqual(HXA_BUILDERS["hxa_cancel"](env), {"flag": "AR", "cancel_no": "HXAR0000000900002"})
        self.assertNotIn("partner", HXA_BUILDERS["hxa_no_partner"](env))
        self.assertEqual(HXA_BUILDERS["hxa_bad_flag"](env)["targets"][0]["type"], "purchase_invoice")
        for bag in ({}, dict(_BAG, hxa_dw="C 1;--"), dict(_BAG, hxa_rc="0")):
            with self.assertRaises(ValueError):
                HXA_BUILDERS["hxa_run"](SimpleNamespace(bag=bag))
        with self.assertRaises(ValueError):
            HXA_BUILDERS["hxa_cancel"](SimpleNamespace(bag={"hxa_cno": "1;--"}))

    def test_steps_tags_and_coverage(self) -> None:
        auto, bad = hxa_cases()
        self.assertEqual(auto.needs, AUTO_KEYS)
        applies = [(step.route, step.sql_name, step.status) for step in auto.steps if step.kind == "apply"]
        self.assertEqual(applies, [(_CANCEL, "hxa_cancel", 200)])
        names = [step.sql_name for step in auto.steps]
        self.assertLess(names.index("hxa_dry"), names.index("hxa_exec"))
        run = auto.steps[names.index("hxa_exec")]
        self.assertEqual((run.kind, run.route, run.id_from), ("custom", ROUTE, "none"))
        self.assertIn(("batches#len", "1"), run.checks)
        self.assertTrue(ops_run.has_op("hxa_exec"))
        preds = [step.code for step in auto.steps if step.kind == "pred"]
        self.assertEqual(preds[3:6], ["shift:-1:hxa_r0", "shift:-1:hxa_i0", "plus1:hxa_n0"])
        self.assertEqual(preds[6:], ["same:hxa_r0", "same:hxa_i0", "same:hxa_n0"])
        kinds = [(step.kind, step.sql_name or step.code) for step in auto.steps]
        self.assertNotIn(("stash", "hx_arcno"), kinds)
        self.assertEqual(kinds[kinds.index(("apply", "hxa_cancel")) + 1], ("keep", "hxa_cdone"))
        self.assertEqual(set(HXA_CLEANUP), {"ar-writeoff-auto"})
        self.assertIn("w:ar_receipt", auto_tags(auto))
        self.assertIn("doc:sale_invoice:13", bind_tags(auto, _BAG))
        self.assertEqual([(step.status, step.code) for step in bad.steps], [(400, "bad_request")] * 2)
        for path in ("co/bridge/src/ArapAutoWriteoff.cs", "api/u8co_api/co_models_writeoff_auto.py"):
            got = select_changed([path], list(known_names()))
            for name in _NAMES:
                self.assertIn(name, got)


def _plan(**extra) -> dict:
    target = {"type": "sale_invoice", "id": 13, "line_id": 14, "code": "SO1", "balance": 3.0, "amount": 3.0}
    receipt = {"type": "ar_receipt", "id": 11, "line_id": 12, "remaining": 5.0}
    batch = {"receipt": receipt, "targets": [target], "amount": 3.0}
    batch.update(extra)
    return batch


class HxaDryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.body: dict = {"ok": True, "dry_run": True, "plan": [_plan()], "total": 3.0}
        self.status = 200
        self._saved = (writeoff_auto_ops.invoke, writeoff_auto_ops.auth_fields)
        writeoff_auto_ops.invoke = self._invoke
        writeoff_auto_ops.auth_fields = lambda _env: {"acc": "801"}

    def tearDown(self) -> None:
        writeoff_auto_ops.invoke, writeoff_auto_ops.auth_fields = self._saved

    def _invoke(self, _client: object, route: str, fields: dict) -> tuple[int, dict]:
        self.sent.append((route, dict(fields)))
        return self.status, self.body

    def _run(self) -> tuple[str, str]:
        env = SimpleNamespace(bag=dict(_BAG), client=None)
        return writeoff_auto_ops.dry(env, None, None)

    def test_dry_passes_on_a_sane_plan(self) -> None:
        self.assertEqual(self._run()[0], "PASS")
        route, fields = self.sent[-1]
        self.assertEqual(route, ROUTE)
        self.assertEqual((fields["acc"], fields["dry_run"], fields["partner"]), ("801", True, "C001"))
        self.assertNotIn("max_amount", fields)

    def test_dry_fails_on_bad_plans(self) -> None:
        big = {"type": "sale_invoice", "id": 13, "line_id": 14, "balance": 1.0, "amount": 2.0}
        over = _plan(targets=[big], amount=2.0)
        other = _plan(targets=[{"type": "sale_invoice", "id": 99, "line_id": 14, "balance": 3.0, "amount": 3.0}])
        wrong = (
            {"ok": True, "dry_run": True, "plan": [], "total": 0},
            {"ok": True, "plan": [_plan()], "total": 3.0},
            {"ok": True, "dry_run": True, "plan": [_plan()], "total": 2.0},
            {"ok": True, "dry_run": True, "plan": [_plan(amount=2.0)], "total": 2.0},
            {"ok": True, "dry_run": True, "plan": [over], "total": 2.0},
            {"ok": True, "dry_run": True, "plan": [other], "total": 3.0},
            {"ok": True, "dry_run": True, "plan": [_plan(receipt={"id": 11, "line_id": 99})], "total": 3.0},
            {"ok": True, "dry_run": True, "plan": [_plan(targets=[], amount=0)], "total": 0},
        )
        for body in wrong:
            self.body = body
            try:
                verdict = self._run()[0]
            except ValueError:
                verdict = "FAIL"
            self.assertEqual(verdict, "FAIL", body)

    def test_dry_raises_on_bridge_errors(self) -> None:
        self.status = 409
        self.body = {"ok": False, "code": "state_mismatch", "message": "x"}
        with self.assertRaises(ValueError):
            self._run()


def _env(bag: dict) -> SimpleNamespace:
    return SimpleNamespace(bag=bag, client=None, focus="", today="2026-09-29", operator_name="op001", made={}, last_code="")


class HxaExecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.body: dict = {"ok": True, "flag": "AR", "batches": [{"cancel_no": "HXAR0000000900010"}]}
        self._saved = (writeoff_run.invoke, writeoff_run.auth_fields)
        writeoff_run.invoke = self._invoke
        writeoff_run.auth_fields = lambda _env: {"acc": "801"}

    def tearDown(self) -> None:
        writeoff_run.invoke, writeoff_run.auth_fields = self._saved

    def _invoke(self, _client: object, route: str, fields: dict) -> tuple[int, dict]:
        self.sent.append((route, dict(fields)))
        return 200, self.body

    def _run(self, bag: dict) -> tuple[str, str]:
        spec = hxa_cases()[0]
        step = next(step for step in spec.steps if step.sql_name == "hxa_exec")
        return writeoff_auto_ops.HXA_OPS["hxa_exec"](_env(bag), spec, step)

    def test_records_the_batch_from_the_response(self) -> None:
        bag = {k: v for k, v in _BAG.items() if k != "hxa_cno"}
        self.assertEqual(self._run(bag)[0], "PASS")
        self.assertEqual(bag["hxa_cno"], "0000000900010")
        self.assertNotIn("hxa_more", bag)
        route, fields = self.sent[-1]
        self.assertEqual((route, fields["max_amount"], fields["dry_run"]), (ROUTE, 1, False))

    def test_extra_batches_are_kept_and_compensated(self) -> None:
        # 批数不对时核对失败，但各批的核销号已记下，补偿逐批取消。
        self.body = {"ok": True, "flag": "AR", "batches": [{"cancel_no": "HXAR5"}, {"cancel_no": "HXAR6"}, {"cancel_no": "HXAR7"}]}
        bag = {k: v for k, v in _BAG.items() if k != "hxa_cno"}
        self.assertEqual(self._run(bag)[0], "FAIL")
        self.assertEqual((bag["hxa_cno"], bag["hxa_more"]), ("5", "6,7"))
        sent: list[str] = []
        saved = (steps.invoke, steps.auth_fields)
        steps.invoke = lambda _c, _r, fields: (sent.append(fields["cancel_no"]) or 200, {"ok": True})
        steps.auth_fields = lambda _env: {"acc": "801"}
        try:
            HXA_CLEANUP["ar-writeoff-auto"](SimpleNamespace(bag=bag, client=None), "ar-writeoff-auto")
        finally:
            steps.invoke, steps.auth_fields = saved
        self.assertEqual(sent, ["HXAR5", "HXAR6", "HXAR7"])
        self.assertEqual(bag["hxa_cdone"], "1")


if __name__ == "__main__":
    unittest.main()
