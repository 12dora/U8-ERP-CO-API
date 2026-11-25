"""字段标签、单据搜索、批量读取、全部写路由的幂等键用例的登记、接线、纯判断和自定义步骤。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.e2e.casespec import ops_run, batch_read, idem_routes, meta_fields, doc_search
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.dry_suite import DRY_SUITE_CLEANUP, DRY_SUITE_OPS, dry_suite_cases
from co.e2e.casespec.meta_batch import MB_CLEANUP, MB_OPS, mb_cases
from co.e2e.casespec.meta_batch_judge import (
    batch_problem,
    expand_side,
    fields_problem,
    gl_names,
    gl_required,
    kind_writable,
    page_problem,
    row_problem,
    same_as_list,
    unsupported_op,
)
from co.e2e.casespec.runner import _CLEANUP
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_REV = "a" * 64
_META = {
    "ok": True,
    "kinds": [
        {"name": "sale_order", "writable": {
            "create": {"head": {"exact": ["ccuscode", "cbustype"], "spans": [{"prefix": "cdefine", "from": 1, "to": 2}]},
                       "lines": {"exact": ["cinvcode", "iquantity"], "spans": []},
                       "required": {"head": [], "lines": ["cinvcode"]}},
            "update": {"head": {"exact": ["cmemo"], "spans": []}, "lines": None}}},
        {"name": "purchase_invoice", "writable": {"create": {"head": None, "lines": None}, "update": None}},
    ],
    "archives": [{"name": "customer", "writable": ["name", "abbrname"]}],
    "gl": {"head": ["sign", "date"], "line": ["account", "digest"], "cash_flow": ["item"],
           "required_head": ["sign"], "required_line": ["account", "digest"]},
}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _row(name: str, label: object = "标签", kind: object = "string", required: bool = False, **extra: object) -> dict:
    return {"name": name, "label": label, "type": kind, "required": required, **extra}


def _env(bag: dict | None = None) -> SimpleNamespace:
    client = SimpleNamespace(meta=lambda: _META)
    return SimpleNamespace(bag={} if bag is None else bag, client=client, focus="", today="2026-09-29", made={},
                           last_code="", sql_n=0)


class RegistryTests(unittest.TestCase):
    def test_cases_registered_once(self) -> None:
        names = known_names()
        mine = [spec.name for spec in mb_cases()]
        self.assertEqual(len(mine), len(set(mine)))
        for name in mine:
            self.assertEqual(names.count(name), 1, name)
            self.assertIn(name, [spec.name for spec in dry_suite_cases()])
        for name in ("meta-fields-voucher", "meta-fields-archive", "meta-fields-gl", "vouchers-search", "batch-load-sql",
                     "batch-load-com", "archive-get-many", "idempotency-sale-order", "idempotency-gl-void",
                     "idempotency-wf-submit", "idempotency-auto-plan"):
            self.assertIn(name, mine)

    def test_ops_and_cleanups_wired(self) -> None:
        for op in MB_OPS:
            self.assertIn(op, DRY_SUITE_OPS)
            self.assertTrue(ops_run.has_op(op), op)
        for name, fn in MB_CLEANUP.items():
            self.assertIs(DRY_SUITE_CLEANUP[name], fn)
            self.assertIs(_CLEANUP[name], fn)
        for spec in mb_cases():
            for step in spec.steps:
                if step.kind == "custom":
                    self.assertTrue(ops_run.has_op(step.sql_name), step.sql_name)

    def test_tags(self) -> None:
        cases = {spec.name: spec for spec in mb_cases()}
        for spec in cases.values():
            self.assertTrue(auto_tags(spec), spec.name)
        self.assertIn("new:sale_order", auto_tags(cases["idempotency-sale-order"]))
        self.assertIn("gl", auto_tags(cases["idempotency-gl-void"]))
        self.assertIn("wf", auto_tags(cases["idempotency-wf-submit"]))
        self.assertTrue(cases["idempotency-wf-submit"].optional)
        self.assertIn("list:sale_order", auto_tags(cases["vouchers-search"]))
        plan = cases["idempotency-auto-plan"]
        self.assertEqual(auto_tags(plan), frozenset({"probe"}))
        self.assertFalse(plan.requires_login)
        fields = dict(plan.steps[0].fields)
        self.assertEqual((fields["@dry"], fields["@key"], fields["@f"]), ("1", "1", "idempotency_key"))

    def test_coverage(self) -> None:
        names = list(known_names())
        search = select_changed(["co/bridge/src/VoucherSearchRoute.cs"], names)
        self.assertIn("vouchers-search", search)
        self.assertNotIn("sale-verify", search)
        self.assertIn("vouchers-search", select_changed(["co/bridge/src/ListSql.cs"], names))
        many = select_changed(["co/bridge/src/LoadMany.cs"], names)
        self.assertLessEqual({"batch-load-sql", "batch-load-com"}, set(many))
        arc = select_changed(["co/bridge/src/ArcGetMany.cs"], names)
        self.assertIn("archive-get-many", arc)
        self.assertNotIn("archive-customer-cycle", arc)
        self.assertIn("meta-fields-voucher", select_changed(["co/bridge/src/MetaFieldsDoc.cs"], names))
        self.assertIn("idempotency-sale-order", select_changed(["co/bridge/src/IdemFlow.cs"], names))
        self.assertEqual(select_changed(["co/bridge/src/IdemReq.cs"], names), names)
        client = select_changed(["co/client/u8co_lookup.py"], names)
        self.assertLessEqual({"meta-fields-gl", "vouchers-search", "archive-get-many"}, set(client))
        self.assertIn("idempotency-gl-void", select_changed(["co/client/u8co_idem.py"], names))
        self.assertIn("batch-load-com", select_changed(["api/u8co_api/co_routes_lookup.py"], names))


class JudgeTests(unittest.TestCase):
    def test_expand_side_and_meta_lookups(self) -> None:
        side = {"exact": ["cCusCode"], "spans": [{"prefix": "cdefine", "from": 3, "to": 5}]}
        self.assertEqual(expand_side(side), {"ccuscode", "cdefine3", "cdefine4", "cdefine5"})
        self.assertEqual(expand_side(None), set())
        self.assertEqual(kind_writable(_META, "sale_order")["required"]["lines"], ["cinvcode"])
        self.assertEqual(kind_writable(_META, "no_such"), {})
        self.assertEqual(unsupported_op(_META), ("purchase_invoice", "update"))
        self.assertEqual(gl_names(_META), {"sign", "date", "account", "digest", "item"})
        self.assertEqual(gl_required(_META), {"sign", "account", "digest"})

    def test_row_problem(self) -> None:
        self.assertEqual(row_problem(_row("ccuscode", max_length=20)), "")
        self.assertEqual(row_problem(_row("cdefine1", None, None)), "")
        self.assertEqual(row_problem(_row("cbustype", kind="enum", enum=[{"code": "1", "name": "普通"}])), "")
        for bad in (_row(""), _row("a", kind="money"), _row("a", required=1), _row("a", kind="enum", enum=[]),
                    _row("a", enum=[{"code": "1", "name": "x"}]), _row("a", max_length=0), _row("a", label=5)):
            with self.subTest(bad=bad):
                self.assertNotEqual(row_problem(bad), "")

    def test_fields_problem(self) -> None:
        payload = {"ok": True, "fields_revision": _REV, "head": [_row("ccuscode"), _row("cdefine1", None, None)],
                   "lines": [_row("cinvcode", required=True)]}
        self.assertEqual(fields_problem(payload, {"ccuscode", "cdefine1"}, set(), ("head",)), "")
        self.assertEqual(fields_problem(payload, {"cinvcode"}, {"cinvcode"}, ("lines",)), "")
        self.assertIn("多", fields_problem(payload, {"ccuscode"}, set(), ("head",)))
        self.assertIn("required", fields_problem(payload, {"ccuscode", "cdefine1"}, {"ccuscode"}, ("head",)))
        self.assertIn("fields_revision", fields_problem(dict(payload, fields_revision="x"), set(), set()))

    def test_pages_and_items(self) -> None:
        listed = {"items": [{"id": 1, "cus_code": "C1"}, {"id": 2, "cus_code": "C1"}], "next": 2}
        self.assertEqual(page_problem({"items": [{"id": 1}, {"id": 2}], "next": 2}, listed, "x"), "")
        self.assertIn("≠", page_problem({"items": [{"id": 1}]}, listed, "x"))
        self.assertIn("next", page_problem({"items": [{"id": 1}, {"id": 2}]}, listed, "x"))
        item = {"id": 1, "cus_code": "C1", "partner_name": "客户甲"}
        self.assertEqual(same_as_list(item, listed["items"][0]), "")
        self.assertIn("partner_name", same_as_list({"id": 1, "cus_code": "C1"}, listed["items"][0]))
        self.assertIn("cus_code", same_as_list({"id": 1, "cus_code": "C2", "partner_name": None}, listed["items"][0]))

    def test_batch_problem(self) -> None:
        single = {"ok": True, "id": 5, "head": {"a": 1}}
        items = [{"id": 5, "head": {"a": 1}}, {"id": 9, "error": {"code": "not_found", "message": "没有"}}]
        self.assertEqual(batch_problem(items, [(5, single), (9, None)], "id"), "")
        self.assertIn("head.a", batch_problem([{"id": 5, "head": {"a": 2}}, items[1]], [(5, single), (9, None)], "id"))
        wrong = [items[0], {"id": 9, "error": {"code": "no_permission"}}]
        self.assertIn("not_found", batch_problem(wrong, [(5, single), (9, None)], "id"))
        self.assertIn("2 项", batch_problem(items[:1], [(5, single), (9, None)], "id"))


class _Fake(unittest.TestCase):
    """把各模块的 send 换成按路由排队的假应答；没排队的路由回 200 {"ok": true}。"""

    modules: tuple = (meta_fields, doc_search, batch_read, idem_routes)

    def setUp(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.replies: dict[str, list[tuple[int, dict]]] = {}
        self._saved = [(module, module.send) for module in self.modules]
        for module in self.modules:
            module.send = self._send

    def tearDown(self) -> None:
        for module, fn in self._saved:
            module.send = fn

    def _send(self, _env: object, route: str, body: dict) -> tuple[int, dict]:
        self.sent.append((route, dict(body)))
        queue = self.replies.get(route) or [(200, {"ok": True})]
        return queue.pop(0) if len(queue) > 1 else queue[0]


class IdemStepTests(_Fake):
    def test_verify_replay_mismatch_and_lookup(self) -> None:
        first = {"ok": True, "id": 7, "state": "verified"}
        self.replies["/v1/vouchers/verify"] = [
            (200, first), (200, dict(first)), (409, {"ok": False, "code": "idempotency_mismatch"}),
            (409, {"ok": False, "code": "state_mismatch"}),
        ]
        self.replies["/v1/idempotency/get"] = [(200, {"ok": True, "found": True, "state": "ok", "status": 200,
                                                      "response": first})]
        verdict = idem_routes.so_verify(_env({"mb_so": "7"}), None, None)
        self.assertEqual(verdict[0], "PASS", verdict)
        bodies = [body for route, body in self.sent if route == "/v1/vouchers/verify"]
        key = bodies[0]["idempotency_key"]
        self.assertTrue(key.startswith("e2e-idem-"))
        self.assertEqual([body.get("idempotency_key") for body in bodies], [key, key, key, None])
        self.assertEqual(bodies[2]["action"], "unverify")
        self.assertEqual(self.sent[-1][1]["route"], "/u8co/v1/vouchers/verify")

    def test_replay_difference_fails(self) -> None:
        self.replies["/v1/vouchers/delete"] = [(200, {"ok": True, "deleted": True}), (404, {"ok": False, "code": "not_found"})]
        verdict = idem_routes.so_delete(_env({"mb_so": "7"}), None, None)
        self.assertEqual(verdict[0], "FAIL")
        self.assertIn("重放", verdict[1])

    def test_gl_void_mismatch_uses_other_no(self) -> None:
        spec = SimpleNamespace(name="idempotency-gl-void")
        first = {"ok": True, "period": 9, "sign": "转", "no": 12}
        self.replies["/v1/gl/vouchers/void"] = [(200, first), (200, dict(first)),
                                                (409, {"ok": False, "code": "idempotency_mismatch"}),
                                                (409, {"ok": False, "code": "state_mismatch"})]
        self.replies["/v1/idempotency/get"] = [(200, {"ok": True, "found": True, "state": "ok", "status": 200,
                                                      "response": first})]
        verdict = idem_routes.gl_void(_env({"core:idempotency-gl-void": "9|转|12"}), spec, None)
        self.assertEqual(verdict[0], "PASS", verdict)
        voids = [body for route, body in self.sent if route == "/v1/gl/vouchers/void"]
        self.assertEqual([body["no"] for body in voids], [12, 12, 13, 12])
        self.assertNotIn("idempotency_key", voids[3])

    def test_wf_disabled_is_na_and_flag_only_after_submit(self) -> None:
        spec = SimpleNamespace(name="idempotency-wf-submit", needs=("qm04_b",))
        self.replies["/v1/workflow/submit"] = [(409, {"ok": False, "code": "workflow_disabled"})]
        bag = {"qm04_b": "31"}
        self.assertEqual(idem_routes.wf_submit(_env(bag), spec, None)[0], "N/A")
        self.assertNotIn(idem_routes.WF_FLAG, bag)
        self.replies["/v1/workflow/submit"] = [(200, {"ok": True}), (200, {"ok": True}),
                                               (409, {"ok": False, "code": "already_submitted"})]
        self.assertEqual(idem_routes.wf_submit(_env(bag), spec, None)[0], "PASS")
        self.assertEqual(bag[idem_routes.WF_FLAG], "31")


class ReadStepTests(_Fake):
    def test_meta_voucher_fields(self) -> None:
        head = [_row("ccuscode"), _row("cbustype", kind="enum", required=True, enum=[{"code": "a", "name": "a"}]),
                _row("cdefine1", None, None), _row("cdefine2", None, None)]
        lines = [_row("cinvcode", required=True), _row("iquantity", kind="decimal")]
        self.replies["/v1/meta/fields"] = [(200, {"ok": True, "type": "sale_order", "op": "create", "vt_id": 17,
                                                  "fields_revision": _REV, "head": head, "lines": lines})]
        verdict = meta_fields.voucher(_env(), None, None)
        self.assertEqual(verdict[0], "PASS", verdict)
        self.assertIn("1 个枚举", verdict[1])
        lines[0] = _row("cinvcode")
        self.assertIn("required", meta_fields.voucher(_env(), None, None)[1])

    def test_meta_refused_uses_unsupported_op(self) -> None:
        self.replies["/v1/meta/fields"] = [(400, {"ok": False, "code": "bad_request", "field": "type"}),
                                           (400, {"ok": False, "code": "bad_request", "field": "op"})]
        self.assertEqual(meta_fields.voucher_refused(_env(), None, None)[0], "PASS")
        self.assertEqual(self.sent[-1][1], {"type": "purchase_invoice", "op": "update"})

    def test_search_refused_and_paging(self) -> None:
        self.replies["/v1/vouchers/search"] = [(400, {"ok": False, "code": "bad_request", "field": "partner"}),
                                               (400, {"ok": False, "code": "bad_request", "field": "warehouse"})]
        self.assertEqual(doc_search.refused(_env(), None, None)[0], "PASS")
        self.replies["/v1/vouchers/search"] = [(200, {"ok": True, "items": [{"id": 3}], "next": 3}),
                                               (200, {"ok": True, "items": [{"id": 8}], "next": 8})]
        self.replies["/v1/vouchers/list"] = [(200, {"ok": True, "items": [{"id": 3}, {"id": 8}], "next": 8})]
        self.assertEqual(doc_search.paging(_env(), None, None), ("PASS", "[3, 8]"))
        self.assertEqual(self.sent[-2][1]["after"], 3)

    def test_search_sql_passes_guard(self) -> None:
        for sql in (doc_search.inv_sql("12"), doc_search.holders_sql("INV-01", 99)):
            self.assertEqual(sql_block_reason(sql), "")
        for bad in (lambda: doc_search.inv_sql("1;x"), lambda: doc_search.holders_sql("a'b", 9)):
            with self.assertRaises(ValueError):
                bad()

    def test_load_many_com(self) -> None:
        self.replies["/v1/vouchers/list"] = [(200, {"ok": True, "items": [{"id": 4}, {"id": 5}]})]
        self.replies["/v1/vouchers/load"] = [(200, {"ok": True, "id": 4}), (200, {"ok": True, "id": 5})]
        missing = {"id": batch_read.MISSING_ID, "error": {"code": "not_found", "message": "没有"}}
        self.replies["/v1/vouchers/load_many"] = [(200, {"ok": True, "type": "sale_order",
                                                         "items": [{"id": 4}, {"id": 5}, missing]}),
                                                  (400, {"ok": False, "code": "bad_request", "field": "ids"})]
        verdict = batch_read.com_kind(_env(), None, None)
        self.assertEqual(verdict[0], "PASS", verdict)
        batches = [body["ids"] for route, body in self.sent if route == "/v1/vouchers/load_many"]
        self.assertEqual(batches[0], [4, 5, batch_read.MISSING_ID])
        self.assertEqual(len(batches[1]), 6)

    def test_load_many_sql_na_without_docs(self) -> None:
        self.replies["/v1/vouchers/list"] = [(200, {"ok": True, "items": []})]
        self.assertEqual(batch_read.sql_kind(_env(), None, None)[0], "N/A")


if __name__ == "__main__":
    unittest.main()
