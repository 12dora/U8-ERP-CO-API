"""应收 / 应付核销、取消核销用例的登记、接线、挑单 SQL 和正文。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.e2e.casespec import ops_run, writeoff_list, writeoff_run, steps
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.writeoff import HX_BUILDERS, HX_CLEANUP, hx_cases
from co.e2e.casespec.writeoff_sql import AP_KEYS, AR_KEYS, HX_PACKS, hx_samples
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.tags import auto_tags, bind_tags

_NAMES = ("ar-writeoff", "ap-writeoff", "ar-writeoff-bad-type")
_ROUTE = "/v1/arap/writeoff"
_CANCEL = "/v1/arap/writeoff/cancel"
_BAG = {
    "hx_rc": "11",
    "hx_rcl": "12",
    "hx_inv": "13",
    "hx_invl": "14",
    "hx_pc": "21",
    "hx_pcl": "22",
    "hx_pinv": "23",
    "hx_pinvl": "24",
    "hx_i0": "5.25",
    "hx_cno": "0000000900001",
    "hx_pno": "12",
}


class WriteoffTests(unittest.TestCase):
    def test_names_registered_once(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in hx_cases()), _NAMES)
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)

    def test_packs_readback_and_builders_are_wired(self) -> None:
        for pack in HX_PACKS:
            self.assertIn(pack, EDIT_PACKS)
            for key in pack[1]:
                self.assertIn(key, pick_keys())
        for name in ("hx_remain", "hx_arbal", "hx_aprbal", "hx_arrows", "hx_aprows"):
            self.assertTrue(has_edit(name), name)
        # 核销号只取本次响应（hx_exec），不再按单据行上最新的核销行去查。
        for name in ("hx_arcno", "hx_apcno"):
            self.assertFalse(has_edit(name), name)
        self.assertIn("FROM dbo.Ap_CloseBills AS b WHERE b.ID = 42;", edit_sql("hx_remain", "42", {}))
        self.assertIn("dbo.Ar_Detail AS d", edit_sql("hx_arbal", "42", {}))
        self.assertIn("d.iBVid = 42;", edit_sql("hx_aprows", "42", {}))
        self.assertIn("d.iFlag < 3", edit_sql("hx_arbal", "42", {}))
        with self.assertRaises(ValueError):
            edit_sql("hx_arbal", "1;--", {})
        for row in hx_samples():
            self.assertIn(row, edit_samples())
        for name, builder in HX_BUILDERS.items():
            self.assertIs(EDIT_BUILDERS[name], builder)

    def test_pick_sql_filters(self) -> None:
        ar, ap = (pack[3]() for pack in HX_PACKS)
        self.assertIn("FROM dbo.Ar_Detail AS d", ar)
        self.assertIn("d.cCoVouchType IN (N'26', N'27')", ar)
        self.assertIn("HAVING SUM(ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0)) >= 2", ar)
        self.assertIn("h.cVouchType = N'48'", ar)
        self.assertIn("ISNULL(m.bflag_AR, 0) <> 0", ar)
        self.assertIn("HAVING SUM(ISNULL(d.iCAmount_f, 0) - ISNULL(d.iDAmount_f, 0)) >= 2", ap)
        self.assertIn("h.cVouchType = N'49'", ap)
        self.assertIn("s.cPBVVerifier", ap)
        for sql in (ar, ap):
            self.assertIn("f.iotherused = -1", sql)
            self.assertIn("ISNULL(b.bPrePay, 0) = 0", sql)
            self.assertIn("b.iRAmt_f >= 2", sql)
            self.assertIn("d.iFlag < 3", sql)
            self.assertEqual(sql.count("N'ROW:'"), 1)

    def test_bodies(self) -> None:
        env = SimpleNamespace(bag=dict(_BAG))
        one = HX_BUILDERS["hx_ar_one"](env)
        self.assertEqual(one["receipt"], {"type": "ar_receipt", "id": 11, "line_id": 12})
        self.assertEqual(one["items"], [{"type": "sale_invoice", "id": 13, "line_id": 14, "amount": 1}])
        ap = HX_BUILDERS["hx_ap_one"](env)
        self.assertEqual(ap["items"][0]["type"], "purchase_invoice")
        self.assertEqual(ap["receipt"]["id"], 21)
        self.assertEqual(HX_BUILDERS["hx_ar_over"](env)["items"][0]["amount"], 6.25)
        bad = HX_BUILDERS["hx_bad_type"](env)
        self.assertEqual(bad["items"][0]["type"], "purchase_invoice")
        self.assertEqual(HX_BUILDERS["hx_ar_cancel"](env), {"flag": "AR", "cancel_no": "HXAR0000000900001"})
        self.assertEqual(HX_BUILDERS["hx_ap_cancel"](env), {"flag": "AP", "cancel_no": "HXAP12"})
        self.assertEqual(HX_BUILDERS["hx_bad_flag"](env)["flag"], "AP")
        with self.assertRaises(ValueError):
            HX_BUILDERS["hx_ar_cancel"](SimpleNamespace(bag={"hx_cno": "1;--"}))
        with self.assertRaises(ValueError):
            HX_BUILDERS["hx_ar_one"](SimpleNamespace(bag={}))
        with self.assertRaises(ValueError):
            HX_BUILDERS["hx_ar_over"](SimpleNamespace(bag={k: v for k, v in _BAG.items() if k != "hx_i0"}))

    def test_steps_and_tags(self) -> None:
        ar, ap, bad = hx_cases()
        self.assertEqual(ar.needs, AR_KEYS)
        self.assertEqual(ap.needs, AP_KEYS)
        self.assertTrue(ap.optional)
        self.assertFalse(ar.optional)
        applies = [(step.sql_name, step.status) for step in ar.steps if step.kind == "apply"]
        want = [("hx_ar_over", 409), ("hx_ar_cancel", 200), ("hx_ar_cancel", 404)]
        self.assertEqual(applies, want)
        routes = [step.route for step in ar.steps if step.kind == "apply"]
        self.assertEqual(routes, [_ROUTE, _CANCEL, _CANCEL])
        stash = [step.code for step in ar.steps if step.kind == "stash"]
        self.assertNotIn("hx_cno", stash)
        undone = [step.code for step in ar.steps if step.kind == "pred" and step.code.startswith("same:")]
        self.assertEqual(undone, ["same:hx_r0", "same:hx_i0", "same:hx_n0"])
        ap_applies = [(step.sql_name, step.status) for step in ap.steps if step.kind == "apply"]
        self.assertEqual(ap_applies, [("hx_ap_cancel", 200)])
        self.assertIn("w:ar_receipt", auto_tags(ar))
        self.assertIn("w:purchase_invoice", auto_tags(ap))
        bound = bind_tags(ar, _BAG)
        self.assertIn("doc:ar_receipt:11", bound)
        self.assertIn("doc:sale_invoice:13", bound)
        self.assertEqual((bad.needs, bad.steps[0].status, bad.steps[0].code), ((), 400, "bad_request"))
        self.assertEqual((bad.steps[1].route, bad.steps[1].status), (_CANCEL, 400))

    def test_cancel_no_is_kept_by_the_write_step(self) -> None:
        # 核销一步（hx_exec）自己从响应记下核销号，取消成功后记下「已取消」：补偿只在两者之间发。
        for spec, side in zip(hx_cases()[:2], ("ar", "ap")):
            kinds = [(step.kind, step.sql_name or step.code) for step in spec.steps]
            write = spec.steps[kinds.index(("custom", "hx_exec"))]
            self.assertEqual((write.route, dict(write.fields)["side"], write.id_from), (_ROUTE, side.upper(), "none"))
            self.assertIn(("cancel_no", "$prefix:HX" + side.upper()), write.checks)
            self.assertTrue(ops_run.has_op("hx_exec"))
            cancel = kinds.index(("apply", "hx_" + side + "_cancel"))
            self.assertEqual(kinds[cancel + 1], ("keep", "hx_cdone" if side == "ar" else "hx_pdone"))
        self.assertEqual(set(HX_CLEANUP), {"ar-writeoff", "ap-writeoff"})

    def test_coverage_rules(self) -> None:
        for path in ("co/bridge/src/ArapWriteoff.cs", "co/bridge/src/ArapUnwriteoff.cs"):
            got = select_changed([path], list(known_names()))
            for name in _NAMES:
                self.assertIn(name, got)


def _batch(**extra) -> dict:
    item = {
        "cancel_no": "HXAR0000000900001",
        "cancellable": True,
        "reason": None,
        "amount": 1.0,
        "gl_voucher": False,
        "receipt": {"type": "ar_receipt", "id": 11, "line_id": 12, "code": "SK1"},
        "targets": [{"type": "sale_invoice", "id": 13, "line_id": 14, "code": "SO1", "amount": 1.0}],
    }
    item.update(extra)
    return item


class WriteoffListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.items: list[dict] = []
        self.status = 200
        self._saved = (writeoff_list.invoke, writeoff_list.auth_fields)
        writeoff_list.invoke = self._invoke
        writeoff_list.auth_fields = lambda _env: {"acc": "801"}

    def tearDown(self) -> None:
        writeoff_list.invoke, writeoff_list.auth_fields = self._saved

    def _invoke(self, _client: object, route: str, fields: dict) -> tuple[int, dict]:
        self.sent.append((route, dict(fields)))
        return self.status, {"ok": True, "flag": "AR", "items": self.items, "code": "x", "message": "y"}

    def _run(self, name: str, side: str = "AR") -> tuple[str, str]:
        env = SimpleNamespace(bag=dict(_BAG), client=None)
        spec = hx_cases()[0 if side == "AR" else 1]
        step = next(step for step in spec.steps if step.sql_name == name)
        return writeoff_list.HX_LIST_OPS[name](env, None, step)

    def test_steps_are_wired_around_the_cancel(self) -> None:
        for spec, side in zip(hx_cases()[:2], ("AR", "AP")):
            names = [step.sql_name for step in spec.steps]
            cancel = names.index("hx_" + side.lower() + "_cancel")
            self.assertLess(names.index("hx_listed"), cancel)
            self.assertGreater(names.index("hx_unlisted"), cancel)
            listed = next(step for step in spec.steps if step.sql_name == "hx_listed")
            self.assertEqual((listed.kind, dict(listed.fields)["side"]), ("custom", side))
            self.assertIn(writeoff_list.REPORT, spec.routes)
        for name in ("hx_listed", "hx_unlisted"):
            self.assertTrue(ops_run.has_op(name), name)

    def test_listed_passes_on_the_new_batch(self) -> None:
        self.items = [_batch()]
        self.assertEqual(self._run("hx_listed")[0], "PASS")
        route, fields = self.sent[-1]
        self.assertEqual(route, "/v1/reports/arap_writeoffs")
        self.assertEqual((fields["flag"], fields["cancel_no"]), ("AR", "HXAR0000000900001"))
        self.items = [_batch(cancel_no="HXAP12", receipt={"type": "ap_payment", "id": 21, "line_id": 22})]
        self.items[0]["targets"] = [{"type": "purchase_invoice", "id": 23, "line_id": 24, "amount": 1}]
        self.assertEqual(self._run("hx_listed", "AP")[0], "PASS")
        self.assertEqual(self.sent[-1][1]["cancel_no"], "HXAP12")

    def test_listed_fails_on_wrong_batches(self) -> None:
        wrong = (
            [],
            [_batch(), _batch()],
            [_batch(cancellable=False, reason="核销已制单，请先删除凭证")],
            [_batch(amount=2.0)],
            [_batch(gl_voucher=True)],
            [_batch(receipt={"type": "ar_receipt", "id": 11, "line_id": 99})],
            [_batch(targets=[])],
            [_batch(targets=[{"type": "sale_invoice", "id": 13, "line_id": 14, "amount": 2}])],
        )
        for items in wrong:
            self.items = items
            self.assertEqual(self._run("hx_listed")[0], "FAIL", items)

    def test_unlisted_and_errors(self) -> None:
        self.assertEqual(self._run("hx_unlisted")[0], "PASS")
        self.items = [_batch()]
        self.assertEqual(self._run("hx_unlisted")[0], "FAIL")
        self.status = 403
        with self.assertRaises(ValueError):
            self._run("hx_unlisted")
        with self.assertRaises(ValueError):
            writeoff_list.unlisted(SimpleNamespace(bag={}, client=None), None, hx_cases()[0].steps[0])


class WriteoffCompensateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.status = 200
        self._saved = (steps.invoke, steps.auth_fields)
        steps.invoke = self._invoke
        steps.auth_fields = lambda _env: {"acc": "801"}

    def tearDown(self) -> None:
        steps.invoke, steps.auth_fields = self._saved

    def _invoke(self, _client: object, route: str, fields: dict) -> tuple[int, dict]:
        self.sent.append((route, dict(fields)))
        return self.status, {"ok": self.status == 200, "code": "state_mismatch"}

    def test_cancels_the_kept_batch(self) -> None:
        env = SimpleNamespace(bag={"hx_cno": "0000000900001"}, client=None)
        note = HX_CLEANUP["ar-writeoff"](env, "ar-writeoff")
        self.assertIn("已补偿", note)
        self.assertEqual(self.sent, [(_CANCEL, {"acc": "801", "flag": "AR", "cancel_no": "HXAR0000000900001"})])
        self.assertEqual(env.bag["hx_cdone"], "1")
        env = SimpleNamespace(bag={"hx_pno": "12"}, client=None)
        self.status = 409
        self.assertIn("快照还原", HX_CLEANUP["ap-writeoff"](env, "ap-writeoff"))
        self.assertEqual(self.sent[-1][1]["cancel_no"], "HXAP12")
        self.assertNotIn("hx_pdone", env.bag)

    def test_gone_batch_counts_as_cancelled(self) -> None:
        # 用例自己的取消已成功、之后的核对才失败：补偿拿到 404 就当已取消。
        env = SimpleNamespace(bag={"hx_cno": "0000000900001"}, client=None)
        self.status = 404
        self.assertIn("已取消", HX_CLEANUP["ar-writeoff"](env, "ar-writeoff"))
        self.assertEqual(env.bag["hx_cdone"], "1")

    def test_skips_without_a_write_or_after_the_cancel(self) -> None:
        for bag in ({}, {"hx_cno": "1;--"}, {"hx_cno": "12", "hx_cdone": "14"}):
            HX_CLEANUP["ar-writeoff"](SimpleNamespace(bag=bag, client=None), "ar-writeoff")
        self.assertEqual(self.sent, [])


def _env(bag: dict) -> SimpleNamespace:
    return SimpleNamespace(bag=bag, client=None, focus="", today="2026-09-29", operator_name="op001", made={}, last_code="")


class WriteoffExecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.status = 200
        self.body: dict = {"ok": True, "cancel_no": "HXAR0000000900010", "items": [{}]}
        self._saved = (writeoff_run.invoke, writeoff_run.auth_fields)
        writeoff_run.invoke = self._invoke
        writeoff_run.auth_fields = lambda _env: {"acc": "801"}

    def tearDown(self) -> None:
        writeoff_run.invoke, writeoff_run.auth_fields = self._saved

    def _invoke(self, _client: object, route: str, fields: dict) -> tuple[int, dict]:
        self.sent.append((route, dict(fields)))
        return self.status, self.body

    def _run(self, side: str, bag: dict) -> tuple[str, str]:
        spec = hx_cases()[0 if side == "AR" else 1]
        step = next(step for step in spec.steps if step.sql_name == "hx_exec")
        return writeoff_list.HX_LIST_OPS["hx_exec"](_env(bag), spec, step)

    def test_records_the_response_cancel_no(self) -> None:
        bag = dict(_BAG)
        bag.pop("hx_cno")
        self.assertEqual(self._run("AR", bag)[0], "PASS")
        self.assertEqual(bag["hx_cno"], "0000000900010")
        route, fields = self.sent[-1]
        self.assertEqual(route, _ROUTE)
        self.assertEqual(fields["items"], [{"type": "sale_invoice", "id": 13, "line_id": 14, "amount": 1}])
        self.body = {"ok": True, "cancel_no": "HXAP7", "items": [{}]}
        self.assertEqual(self._run("AP", bag)[0], "PASS")
        self.assertEqual(bag["hx_pno"], "7")

    def test_records_before_the_checks(self) -> None:
        # 响应核对失败（items 不是一行）也已记下核销号，补偿能按它取消。
        self.body = {"ok": True, "cancel_no": "HXAR0000000900011", "items": [{}, {}]}
        bag: dict = dict(_BAG)
        self.assertEqual(self._run("AR", bag)[0], "FAIL")
        self.assertEqual(bag["hx_cno"], "0000000900011")

    def test_keeps_nothing_on_errors_or_foreign_numbers(self) -> None:
        cases = (
            (409, {"ok": False, "code": "state_mismatch", "message": "x"}),
            (200, {"ok": True, "cancel_no": "HXAP0000000003492", "items": [{}]}),
            (200, {"ok": True, "cancel_no": "HXAR1;--", "items": [{}]}),
        )
        for status, body in cases:
            self.status, self.body = status, body
            bag: dict = {k: v for k, v in _BAG.items() if k != "hx_cno"}
            self._run("AR", bag)
            self.assertNotIn("hx_cno", bag)
        self.assertEqual(writeoff_run.hx_run(_env({}), None, hx_cases()[2].steps[0])[0], "FAIL")


if __name__ == "__main__":
    unittest.main()
