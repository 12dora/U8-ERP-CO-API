"""写预演、名称解析、幂等结果查询、结构化错误、应付自动核销与制单用例的登记、接线、纯判断和自定义步骤。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.e2e.casespec import ops_run, dry_leaks, dry_ops, steps
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.dry_suite import DRY_SUITE_CLEANUP, DRY_SUITE_OPS, dry_suite_cases
from co.e2e.casespec.ap_writeoff_voucher_run import cancel_digits, pz_digits
from co.e2e.casespec.dry_bodies import DRY_SUITE_BUILDERS
from co.e2e.casespec.dry_judge import (
    Want,
    cancel_nos,
    common_piece,
    common_pieces,
    first_diff,
    idem_problem,
    middle_piece,
    new_ids,
    parse_watch,
    preview_problem,
    resolve_problem,
    split_fields,
)
from co.e2e.casespec.dry_sql import (
    HEADS,
    DRY_AP_PACKS,
    count_sql,
    digest_count_sql,
    disabled_customer_sql,
    enabled_customers_sql,
    ids_after_sql,
    dry_suite_samples,
)
from co.e2e.casespec.dry_steps import CREATE, DELETE, LOCK, VERIFY, dry, dry_doc, dry_new, hx_key, leak_key, lock_key, max_key, refuse
from co.e2e.casespec.runner import _CLEANUP
from co.e2e.casespec.arap_gen_readback_sql import has_core
from co.e2e.casespec.returns_misc_readback_sql import has_custom
from co.e2e.casespec.flow_read_sql import _READBACK as FLOW_READBACK
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_BAG = {"dry_ap_dw": "V001", "dry_ap_pc": "21", "dry_ap_pcl": "22", "dry_ap_pinv": "23", "dry_ap_pinvl": "24", "dry_ap_cno": "0000000000017"}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _cases() -> dict:
    return {spec.name: spec for spec in dry_suite_cases()}


def _readable(name: str) -> bool:
    return has_core(name) or has_custom(name) or has_edit(name) or name in FLOW_READBACK


class RegistryTests(unittest.TestCase):
    def test_names_registered_once(self) -> None:
        names = known_names()
        mine = [spec.name for spec in dry_suite_cases()]
        self.assertEqual(len(mine), len(set(mine)))
        for name in mine:
            self.assertEqual(names.count(name), 1, name)
        for name in ("dry-sale-order", "dry-gl-ops", "dry-refuse", "errors-structured", "resolve-archives",
                     "idempotency-get", "ap-writeoff-auto", "ap-voucher", "dry-ar-writeoff", "dry-wf-submit",
                     "dry-mo-open", "dry-qm-incoming", "dry-sale-out-cycle", "dry-other-out-cycle",
                     "dry-material-out-cycle", "dry-product-in-cycle", "dry-sale-return-cycle",
                     "dry-purchase-return-cycle"):
            self.assertIn(name, mine)

    def test_ops_cleanups_builders_packs_wired(self) -> None:
        for op in DRY_SUITE_OPS:
            self.assertTrue(ops_run.has_op(op), op)
        self.assertLessEqual(set(DRY_SUITE_CLEANUP), set(_cases()))
        for name, fn in DRY_SUITE_CLEANUP.items():
            self.assertIs(_CLEANUP[name], fn)
        for name, builder in DRY_SUITE_BUILDERS.items():
            self.assertIs(EDIT_BUILDERS[name], builder)
        for pack in DRY_AP_PACKS:
            self.assertIn(pack, EDIT_PACKS)
            for key in pack[1]:
                self.assertIn(key, pick_keys())
        for row in dry_suite_samples():
            self.assertIn(row, edit_samples())

    def test_every_step_is_resolvable(self) -> None:
        # 步骤里引用的构造器、回读 SQL、自定义操作都登记过。
        for spec in dry_suite_cases():
            for step in spec.steps:
                args, _body = split_fields(step.fields)
                with self.subTest(case=spec.name, step=step.sql_name or step.route):
                    if step.kind == "custom":
                        self.assertTrue(ops_run.has_op(step.sql_name))
                        for builder in (args.get("@b"), dict(step.fields).get("builder")):
                            if builder:
                                self.assertTrue(steps._make(builder))
                    if step.kind in ("apply", "create"):
                        self.assertTrue(steps._make(step.sql_name))
                    if step.kind in ("stash", "pred"):
                        self.assertTrue(_readable(step.sql_name))
                    if args.get("@w"):
                        self.assertTrue(parse_watch(args["@w"]))

    def test_create_and_generate_kinds_are_counted(self) -> None:
        for spec in dry_suite_cases():
            for step in spec.steps:
                args, body = split_fields(step.fields)
                if step.sql_name == "dry_op" and step.route.endswith(("/create", "/generate")):
                    self.assertIn(dict(body).get("type"), HEADS, spec.name)

    def test_tags(self) -> None:
        cases = _cases()
        self.assertIn("new:sale_order", auto_tags(cases["dry-sale-order"]))
        self.assertIn("new:dispatch", auto_tags(cases["dry-dispatch-generate"]))
        self.assertIn("gl", auto_tags(cases["dry-gl-ops"]))
        self.assertIn("wf", auto_tags(cases["dry-wf-submit"]))
        self.assertIn("arc-read:customer", auto_tags(cases["resolve-archives"]))
        for name in ("dry-refuse", "errors-structured"):
            tags = auto_tags(cases[name])
            self.assertIn("probe", tags)
            self.assertFalse(any(tag.startswith(("new:", "w:")) for tag in tags), name)
            self.assertFalse(cases[name].requires_login)

    def test_gap_cases_preview_every_op(self) -> None:
        cases = _cases()
        for name, kind in (("dry-sale-return-cycle", "sale_return"), ("dry-purchase-return-cycle", "purchase_return"),
                           ("dry-other-out-cycle", "other_out")):
            ops = [(step.route, dict(step.fields).get("action")) for step in cases[name].steps
                   if step.sql_name == "dry_op" and dict(step.fields).get("type") == kind]
            self.assertEqual(ops, [(VERIFY, "verify"), (VERIFY, "unverify"), (DELETE, None)], name)
        qm = [step for step in cases["dry-qm-incoming"].steps if step.sql_name in ("dry_op", "dry_refuse")]
        self.assertEqual([dict(step.fields).get("@m") for step in qm], ["rollback", None, "validate"])
        self.assertIn("dry-mo-open", DRY_SUITE_CLEANUP)

    def test_coverage(self) -> None:
        names = list(known_names())
        dry_cases = [name for name in names if name.startswith("dry-")]
        got = select_changed(["co/bridge/src/DryRunModes.cs"], names)
        self.assertLessEqual(set(dry_cases), set(got))
        self.assertNotIn("sale-verify", got)
        got = select_changed(["co/bridge/src/GlDryRun.cs"], names)
        self.assertIn("gl-cycle", got)
        self.assertIn("dry-gl-ops", got)
        self.assertIn("resolve-archives", select_changed(["co/bridge/src/ArcResolveReq.cs"], names))
        self.assertNotIn("archive-customer-cycle", select_changed(["co/bridge/src/ArcResolveReq.cs"], names))
        self.assertIn("idempotency-get", select_changed(["co/bridge/src/IdemGet.cs"], names))
        self.assertIn("errors-structured", select_changed(["api/u8co_api/errors.py"], names))
        # 每次写都经过的预演核心文件：全跑（fail closed）。
        for name in ("DryRun.cs", "DryRunRun.cs", "DryRunReq.cs"):
            self.assertEqual(select_changed(["co/bridge/src/" + name], names), names, name)


class JudgeTests(unittest.TestCase):
    def _new(self, **extra) -> dict:
        doc = {"type": "sale_order", "id": 9001, "state": "exists", "head": {"ccuscode": "C1"},
               "lines": [{"autoid": 1}], "lines_total": 1}
        body = {"ok": True, "dry_run": True, "mode": "rollback", "docs": [doc],
                "warnings": ["number_may_skip", "locks_held"]}
        body.update(extra)
        return body

    def test_rollback_new(self) -> None:
        want = Want("rollback", "new", "sale_order", 0, True)
        self.assertEqual(preview_problem(self._new(), want), "")
        self.assertIn("mode", preview_problem(self._new(mode="validate"), want))
        self.assertIn("number_may_skip", preview_problem(self._new(warnings=["locks_held"]), want))
        self.assertIn("新的", preview_problem(self._new(docs=[]), want))
        self.assertIn("不是预演", preview_problem(self._new(dry_run=False), want))
        bare = self._new(docs=[{"type": "sale_order", "id": 9001, "state": "exists"}])
        self.assertIn("head", preview_problem(bare, want))
        self.assertEqual(new_ids(self._new(), "sale_order"), [9001])
        self.assertEqual(new_ids(self._new(), "sale_order", 9001), [])

    def test_existing_docs(self) -> None:
        exists = Want("rollback", "exists", "sale_order", 7, False)
        body = self._new(docs=[{"type": "sale_order", "id": 7, "state": "exists", "head": {"a": 1}}], warnings=["locks_held"])
        self.assertEqual(preview_problem(body, exists), "")
        gone = Want("rollback", "deleted", "sale_order", 7, False)
        self.assertIn("deleted", preview_problem(body, gone))
        body["docs"] = [{"type": "sale_order", "id": 7, "state": "deleted"}]
        self.assertEqual(preview_problem(body, gone), "")
        self.assertIn("docs 里没有", preview_problem(body, Want("rollback", "exists", "sale_order", 8, False)))

    def test_validate_and_detail(self) -> None:
        want = Want("validate", "new", "production_order", 0, True, "input")
        body = {"ok": True, "dry_run": True, "mode": "validate", "docs": [], "warnings": ["validate_only"],
                "detail": {"input": {"x": 1}}}
        self.assertEqual(preview_problem(body, want), "")
        self.assertIn("detail", preview_problem(dict(body, detail={}), want))
        self.assertIn("validate_only", preview_problem(dict(body, warnings=[]), want))
        made = dict(body, docs=[{"type": "production_order", "id": 5, "state": "exists"}])
        self.assertIn("不该有新单据", preview_problem(made, want))

    def test_watch_and_fields(self) -> None:
        self.assertEqual(parse_watch("sale_order:$id, wf.qm_product_check:bag:k"),
                         [("sale_order", "$id"), ("wf.qm_product_check", "bag:k")])
        for bad in ("sale_order", "sale_order:7", ":$id"):
            with self.assertRaises(ValueError):
                parse_watch(bad)
        args, body = split_fields((("type", "a"), ("@m", "rollback"), ("id", "$id")))
        self.assertEqual((args, body), ({"@m": "rollback"}, (("type", "a"), ("id", "$id"))))

    def test_first_diff(self) -> None:
        self.assertEqual(first_diff({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}), "")
        self.assertEqual(first_diff({"a": [1, {"b": 2}]}, {"a": [1, {"b": 3}]}), "a.1.b")
        self.assertEqual(first_diff({"a": [1]}, {"a": [1, 2]}), "a#len")
        self.assertEqual(first_diff({"a": 1}, {"b": 1}), "a")
        self.assertEqual(first_diff(1, 2), "(整体)")

    def test_resolve_helpers(self) -> None:
        exact = {"status": "exact", "match": {"code": "C1"}, "candidates": [{"code": "C1"}], "more": False}
        self.assertEqual(resolve_problem(exact, ("exact",), "C1"), "")
        self.assertIn("match.code", resolve_problem(exact, ("exact",), "C2"))
        self.assertIn("status", resolve_problem(exact, ("none",)))
        self.assertEqual(resolve_problem({"status": "none", "candidates": [], "more": False}, ("none",)), "")
        self.assertIn("候选应为空", resolve_problem({"status": "none", "candidates": [{"code": "x"}], "more": False}, ("none",)))
        many = {"status": "ambiguous", "candidates": [{"code": "C3"}], "more": True}
        self.assertEqual(resolve_problem(many, ("ambiguous",), "C1"), "")
        self.assertEqual(middle_piece("张三贸易公司"), "三贸易公")
        self.assertEqual(middle_piece("张三"), "")
        names = ["北京张三贸易有限公司", "上海李四贸易有限公司", "王五"]
        self.assertEqual(common_piece(names, set()), "贸易有限公司")
        self.assertEqual(common_piece(names, {"贸易有限公司", "易有限公司"}), "贸易有限公")
        self.assertEqual(common_piece(["甲乙", "丙丁"], set()), "")
        self.assertEqual(common_pieces(names, set(), 2), ["贸易有限公司", "易有限公司"])
        self.assertEqual(common_pieces(["甲乙", "丙丁"], set()), [])

    def test_idem_problem(self) -> None:
        created = {"period": 9, "sign": "转", "no": 12}
        found = {"ok": True, "found": True, "state": "ok", "status": 200, "created_utc": "2026-09-29T08:00:00Z",
                 "response": {"ok": True, "period": 9, "sign": "转", "no": 12}}
        self.assertEqual(idem_problem(found, created), "")
        self.assertIn("response.no", idem_problem(dict(found, response={"period": 9, "sign": "转", "no": 13}), created))
        self.assertIn("state", idem_problem(dict(found, state="in_flight"), created))
        self.assertIn("found", idem_problem({"ok": True, "found": False}, created))


class StepBuilderTests(unittest.TestCase):
    def test_dry_helpers(self) -> None:
        step = dry(CREATE, (("type", "other_in"),), builder="other_in")
        self.assertEqual((step.kind, step.sql_name, step.id_from), ("custom", "dry_op", "none"))
        self.assertIn(("@m", "rollback"), step.fields)
        self.assertIn(("@x", "deleted"), dry_doc(DELETE, "sale_order", "bag:k").fields)
        self.assertIn(("@w", "sale_order:$id"), dry_doc(DELETE, "sale_order", "bag:k").fields)
        self.assertIn(("@x", "new"), dry_new(CREATE, "other_in", "other_in").fields)
        with self.assertRaises(ValueError):
            dry(CREATE, (), colour="red")
        bad = refuse(CREATE, (), builder="dry_probe_other_in", field="lines")
        self.assertEqual((bad.status, bad.code, bad.sql_name), (400, "bad_request", "dry_refuse"))
        self.assertEqual(refuse(CREATE, (), dry="1", id_from="bag:k").id_from, "bag:k")
        self.assertEqual(bad.id_from, "none")

    def test_sql(self) -> None:
        for kind in HEADS:
            self.assertEqual(sql_block_reason(count_sql(kind)), "", kind)
        self.assertIn("FROM dbo.Ap_CloseBill AS h\nWHERE cFlag = N'AP' AND cVouchType = N'49'", count_sql("ap_payment"))
        with self.assertRaises(ValueError):
            count_sql("nope")
        with self.assertRaises(ValueError):
            disabled_customer_sql("2026-9-1")
        with self.assertRaises(ValueError):
            digest_count_sql("2026", "x';--")
        self.assertIn("dEndDate <= '2026-09-29'", disabled_customer_sql("2026-09-29"))
        self.assertIn("c.dEndDate > '2026-09-29'", enabled_customers_sql("2026-09-29"))
        swept = ids_after_sql("ap_payment", "120")
        self.assertIn("WHERE h.iID > 120 AND cFlag = N'AP' AND cVouchType = N'49'", swept)
        self.assertEqual(sql_block_reason(swept), "")
        for bad in (("nope", "1"), ("other_in", "1;--"), ("other_in", "")):
            with self.assertRaises(ValueError):
                ids_after_sql(*bad)
        pick = DRY_AP_PACKS[0][3]()
        self.assertEqual(sql_block_reason(pick), "")
        for text in ("dbo.Ap_Detail AS d", "N'01', N'02'", "h.cVouchType = N'49'", "bflag_AP", "p.line < o.line"):
            self.assertIn(text, pick)

    def test_bodies(self) -> None:
        env = SimpleNamespace(bag=dict(_BAG))
        run = DRY_SUITE_BUILDERS["dry_ap_run"](env)
        self.assertEqual((run["flag"], run["partner"], run["max_amount"], run["dry_run"]), ("AP", "V001", 1, False))
        self.assertEqual(run["receipt"], {"type": "ap_payment", "id": 21, "line_id": 22})
        self.assertEqual(run["targets"], [{"type": "purchase_invoice", "id": 23}])
        plan = DRY_SUITE_BUILDERS["dry_ap_plan"](env)
        self.assertTrue(plan["dry_run"])
        self.assertNotIn("max_amount", plan)
        self.assertEqual(DRY_SUITE_BUILDERS["dry_ap_cancel"](env), {"flag": "AP", "cancel_no": "HXAP0000000000017"})
        env.bag.update({"dry_apb": "31", "dry_apb_pz": "0000000009"})
        self.assertEqual(DRY_SUITE_BUILDERS["dry_apb_voucher"](env), {"flag": "AP", "type": "ap_bill", "id": 31})
        self.assertEqual(DRY_SUITE_BUILDERS["dry_apb_drop"](env), {"flag": "AP", "pz_id": "AP0000000009"})
        for bag in ({}, dict(_BAG, dry_ap_dw="V 1;--"), dict(_BAG, dry_ap_pc="0")):
            with self.assertRaises(ValueError):
                DRY_SUITE_BUILDERS["dry_ap_run"](SimpleNamespace(bag=bag))
        with self.assertRaises(ValueError):
            DRY_SUITE_BUILDERS["dry_hx_cancel"](SimpleNamespace(bag={"dry_hx_cno": "1;--"}))
        self.assertNotIn("lines", DRY_SUITE_BUILDERS["dry_probe_no_lines"](env))

    def test_recorders(self) -> None:
        self.assertEqual(cancel_digits({"cancel_no": "HXAP0012"}, "AP"), ["0012"])
        batches = {"batches": [{"cancel_no": "HXAP1"}, {"cancel_no": "HXAR2"}, {"cancel_no": "HXAP3"}]}
        self.assertEqual(cancel_digits(batches, "AP"), ["1", "3"])
        self.assertEqual(pz_digits("AP0009", "AP"), "0009")
        self.assertEqual(pz_digits("AR0009", "AP"), "")
        self.assertEqual(pz_digits("AP09x", "AP"), "")
        detail = {"writeoff": {"cancel_no": "HXAR0001", "items": [{"cancel_no": "HXAR0002"}]}, "x": [{"cancel_no": 3}]}
        self.assertEqual(sorted(cancel_nos(detail)), ["HXAR0001", "HXAR0002"])


def _env(bag: dict) -> SimpleNamespace:
    return SimpleNamespace(bag=bag, client=None, focus="", today="2026-09-29", operator_name="op001", made={}, last_code="",
                           sql_n=0)


class DryStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.replies: dict[str, list[tuple[int, dict]]] = {}
        self.counts = ["ROW:5|100", "ROW:5|100"]
        self._saved = (dry_ops.send, dry_ops.run_guarded)
        dry_ops.send = self._send
        dry_ops.run_guarded = lambda _env, _sql, _label: self.counts.pop(0)

    def tearDown(self) -> None:
        dry_ops.send, dry_ops.run_guarded = self._saved

    def _send(self, _env: object, route: str, body: dict) -> tuple[int, dict]:
        self.sent.append((route, dict(body)))
        queue = self.replies.get(route) or [(200, {"ok": True})]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def _preview(self, **extra) -> dict:
        doc = {"type": "other_in", "id": 77, "state": "exists", "head": {"cwhcode": "01"}, "lines": [{"autoid": 1}],
               "lines_total": 1}
        body = {"ok": True, "dry_run": True, "mode": "rollback", "docs": [doc], "warnings": ["number_may_skip", "locks_held"]}
        body.update(extra)
        return body

    def _run(self, step, bag: dict | None = None) -> tuple[str, str]:
        spec = SimpleNamespace(name="dry-x", needs=())
        return dry_ops.dry_step(_env(bag if bag is not None else {"other_in_master": "01|101|D1|INV1"}), spec, step)

    def test_create_passes_when_nothing_left(self) -> None:
        self.replies["/v1/vouchers/create"] = [(200, self._preview())]
        self.replies["/v1/vouchers/load"] = [(404, {"ok": False, "code": "not_found"})]
        verdict = self._run(dry_new(CREATE, "other_in", "other_in"))
        self.assertEqual(verdict[0], "PASS", verdict)
        route, body = self.sent[0]
        self.assertEqual((route, body["dry_run"], body["type"]), ("/v1/vouchers/create", True, "other_in"))
        self.assertEqual(self.sent[1], ("/v1/vouchers/load", {"type": "other_in", "id": 77}))

    def test_create_fails_when_doc_or_count_left(self) -> None:
        self.replies["/v1/vouchers/create"] = [(200, self._preview())]
        self.replies["/v1/vouchers/load"] = [(200, {"ok": True})]
        self.assertEqual(self._run(dry_new(CREATE, "other_in", "other_in"))[0], "FAIL")
        self.counts = ["ROW:5|100", "ROW:6|101"]
        self.replies["/v1/vouchers/load"] = [(404, {"ok": False})]
        verdict = self._run(dry_new(CREATE, "other_in", "other_in"))
        self.assertEqual(verdict[0], "FAIL")
        self.assertIn("5|100 → 6|101", verdict[1])

    def test_watch_detects_change(self) -> None:
        preview = self._preview(docs=[{"type": "sale_order", "id": 9, "state": "deleted"}], warnings=["locks_held"])
        self.replies["/v1/vouchers/delete"] = [(200, preview)]
        self.replies["/v1/vouchers/load"] = [(200, {"ok": True, "head": {"a": 1}}), (200, {"ok": True, "head": {"a": 2}})]
        verdict = self._run(dry_doc(DELETE, "sale_order", "bag:k"), {"k": "9"})
        self.assertEqual(verdict[0], "FAIL")
        self.assertIn("head.a", verdict[1])
        self.replies["/v1/vouchers/load"] = [(200, {"ok": True, "head": {"a": 1}})]
        self.replies["/v1/vouchers/delete"] = [(200, preview)]
        self.assertEqual(self._run(dry_doc(DELETE, "sale_order", "bag:k"), {"k": "9"})[0], "PASS")

    def test_unreadable_before_fails_without_sending(self) -> None:
        self.replies["/v1/vouchers/load"] = [(429, {"ok": False, "code": "busy"})]
        verdict = self._run(dry_doc(DELETE, "sale_order", "bag:k"), {"k": "9"})
        self.assertEqual(verdict[0], "FAIL")
        self.assertIn("无法比较", verdict[1])
        self.assertEqual([route for route, _body in self.sent], ["/v1/vouchers/load"])

    def test_leaks_are_recorded(self) -> None:
        self.replies["/v1/vouchers/create"] = [(200, self._preview(detail={"writeoff": {"cancel_no": "HXAR0005"}}))]
        self.replies["/v1/vouchers/load"] = [(200, {"ok": True})]
        bag = {"other_in_master": "01|101|D1|INV1"}
        self.assertEqual(self._run(dry_new(CREATE, "other_in", "other_in"), bag)[0], "FAIL")
        self.assertEqual(bag[leak_key("dry-x")], "other_in:77")
        self.assertEqual(bag[max_key("dry-x", "other_in")], "100")
        self.assertEqual(bag[hx_key("dry-x")], "HXAR0005")
        self.replies["/v1/vouchers/lock"] = [(200, self._preview(docs=[], warnings=["locks_held"]))]
        self.replies["/v1/vouchers/load"] = [(200, {"ok": True})]
        bag = {"k": "9"}
        self._run(dry_doc(LOCK, "sale_order", "bag:k", ("action", "lock")), bag)
        self.assertEqual(bag[lock_key("dry-x")], "sale_order:9")

    def test_bridge_error_fails(self) -> None:
        self.replies["/v1/vouchers/create"] = [(400, {"ok": False, "code": "bad_request", "message": "该操作不支持预演"})]
        verdict = self._run(dry_new(CREATE, "other_in", "other_in"))
        self.assertEqual(verdict[0], "FAIL")
        self.assertIn("不支持预演", verdict[1])

    def test_refuse_checks_text_and_field(self) -> None:
        self.replies["/v1/vouchers/create"] = [(400, {"ok": False, "code": "bad_request", "message": "缺少字段 lines",
                                                      "field": "lines"})]
        spec = SimpleNamespace(name="errors-x", needs=())
        good = refuse(CREATE, (), builder="dry_probe_no_lines", text="缺少", field="lines")
        self.assertEqual(dry_ops.refuse_step(_env({}), spec, good)[0], "PASS")
        wrong = refuse(CREATE, (), builder="dry_probe_no_lines", field="head")
        self.assertEqual(dry_ops.refuse_step(_env({}), spec, wrong)[0], "FAIL")
        keyed = refuse(CREATE, (), builder="dry_probe_other_in", dry="1", key="1", text="缺少")
        dry_ops.refuse_step(_env({}), spec, keyed)
        body = self.sent[-1][1]
        self.assertTrue(body["dry_run"])
        self.assertTrue(body["idempotency_key"].startswith("e2e-dry-"))


if __name__ == "__main__":
    unittest.main()


class LeakCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.loads: dict[int, int] = {}
        self.report: list[dict] = []
        self._saved = (dry_leaks._call, dry_leaks._swept, dry_leaks.cancel_one)
        dry_leaks._call = self._call
        dry_leaks._swept = lambda _env, _case: [("dispatch", 300)]
        dry_leaks.cancel_one = lambda _env, flag, digits: (True, f"取消 HX{flag}{digits}")

    def tearDown(self) -> None:
        dry_leaks._call, dry_leaks._swept, dry_leaks.cancel_one = self._saved

    def _call(self, _env: object, route: str, extra: dict) -> tuple[int, dict]:
        self.sent.append((route, dict(extra)))
        if route == "/v1/vouchers/load":
            return self.loads.get(extra["id"], 200), {}
        if route == "/v1/reports/arap_writeoffs":
            return 200, {"items": self.report}
        return 200, {"ok": True}

    def test_downstream_first_and_skips_gone(self) -> None:
        bag = {leak_key("dry-a"): "sale_out:501,dispatch:300", lock_key("dry-a"): "sale_order:7"}
        self.loads[300] = 200
        note = dry_leaks.leak_cleanup(SimpleNamespace(bag=bag), "dry-a")
        deletes = [(extra["type"], extra["id"]) for route, extra in self.sent if route == "/v1/vouchers/delete"]
        self.assertEqual(deletes, [("sale_out", 501), ("dispatch", 300)])
        self.assertEqual(self.sent[0], ("/v1/vouchers/lock", {"type": "sale_order", "id": 7, "action": "unlock"}))
        self.assertIn("sale_out 501", note)
        self.sent.clear()
        self.loads.update({300: 404, 501: 404})
        dry_leaks.leak_cleanup(SimpleNamespace(bag=bag), "dry-a")
        self.assertFalse([route for route, _extra in self.sent if route == "/v1/vouchers/delete"])

    def test_writeoff_cancelled_only_when_mine(self) -> None:
        bag = {hx_key("dry-ar-writeoff"): "HXAR0009", "hx_rc": "11"}
        self.report = [{"cancel_no": "HXAR0009", "receipt": {"id": 12}}]
        self.assertNotIn("取消", dry_leaks.leak_cleanup(SimpleNamespace(bag=bag), "dry-ar-writeoff"))
        self.report = [{"cancel_no": "HXAR0009", "receipt": {"id": 11}}]
        self.assertIn("取消 HXAR0009", dry_leaks.leak_cleanup(SimpleNamespace(bag=bag), "dry-ar-writeoff"))
