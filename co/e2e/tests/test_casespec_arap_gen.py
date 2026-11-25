"""用例（应收应付、生单、生产订单、列表、并发）的登记、正文、SQL 和自定义步骤。不连接服务器。"""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e.casespec import doc_list, arap_gen_run
from co.e2e.casespec.arap_gen_bodies import (
    CORE_BUILDERS,
    arrival_gen,
    arrival_over,
    ap_bill_body,
    ap_payment_body,
    ar_bill_body,
    ar_receipt_body,
    material_out_gen,
    material_out_nord,
    product_in_gen,
    purchase_in_qm_gen,
    purchase_invoice_badsrc,
    purchase_invoice_dup,
    purchase_invoice_gen,
    purchase_invoice_nocode,
)
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arap_gen import BURST_LOADS, arap_gen_cases
from co.e2e.casespec.picks import _PACKS, pick_keys
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.doc_list_sql import list_count_sql, stock_count_sql
from co.e2e.casespec.arap_gen_readback_sql import has_core, core_sql
from co.e2e.casespec.flow_read_sql import flow_sql
from co.e2e.casespec.steps import _make
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()

# 与桥 ArapReq 的允许列表一致（小写）。
_CLOSE_HEAD = set("cdwcode,dvouchdate,cdeptcode,cperson,csscode,ccode,cexch_name,iexchrate,cdigest".split(","))
_CLOSE_LINE = set("itype,ckm,iamt,iamt_f,cdepcode,cpersoncode,cxmclass,cxm,cmemo".split(","))
_VOUCH_HEAD = set("cdwcode,dvouchdate,cdeptcode,cperson,ccode,cexch_name,iexchrate,cdigest".split(","))
_VOUCH_LINE = set("ccode,iamount,iamount_f,iamt,iamt_f,itaxrate,cdeptcode,cperson,cdigest".split(","))
_NAMES = (
    "ar-receipt-cycle",
    "ap-payment-cycle",
    "ar-bill-cycle",
    "ap-bill-cycle",
    "ar-receipt-voucher-refuse",
    "material-out-generate",
    "material-out-no-rd",
    "product-in-generate",
    "purchase-invoice-generate",
    "purchase-in-from-qm",
    "arrival-generate",
    "mo-unverify-verify",
    "mo-verify-audited",
    "list-sale-order",
    "list-ar-receipt",
    "list-changed-since",
    "stock-current-filter",
    "bridge-concurrency",
    "load-ar-receipt",
    "load-ap-payment",
    "load-ar-bill",
    "load-ap-bill",
)


def _by_name(name: str) -> CaseSpec:
    for spec in build_cases({}):
        if spec.name == name:
            return spec
    raise AssertionError(name)


def _env(client: object = None) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    bag = {
        "ar_master": "C000003|EX040|EX040001|11229901|1002990101|2|64019901",
        "ap_master": "S000003|220201|1002990101|2|64019901",
        "mo_alloc_line": "2000000104",
        "mo_alloc_wh": "13",
        "qm_pin_wh": "10",
        "qm_pin_qty": "25.0000",
        "other_in_master": "13|206|EX092|INV0003",
        "so_list": "C000001|2026-07-25|2026-09-23",
        "stock_pair": "01|INV0001",
        "pinv_line": "2000000106",
        "qmin_wh": "07",
        "arr_line": "2000000103",
        "arr_wh": "01",
        "arr_over": "3.0000",
    }
    for index, kind in enumerate(BURST_LOADS):
        bag["load_" + kind] = str(1000 + index)
    return SimpleNamespace(
        client=client, operator=operator, year="2026", today="2026-09-27", bag=bag,
        made={}, focus="2000000101", last_code="", operator_name="甲",
    )


def _keys(rows: list[dict]) -> set[str]:
    return {str(key).lower() for row in rows for key in row}


class FakeClient:
    """按路由回放响应；新建按顺序发号，删除后读取返回 404。线程安全。"""

    def __init__(self, list_payloads: list[dict] | None = None, dup_code: bool = False) -> None:
        self.lock = threading.Lock()
        self.calls: list[tuple[str, dict]] = []
        self.pages = list(list_payloads or [])
        self.dup_code = dup_code
        self.next_id = 500
        self.deleted: set[int] = set()

    def call(self, route: str, fields: dict) -> dict:
        with self.lock:
            self.calls.append((route, dict(fields)))
            return self._answer(route, fields)

    def _answer(self, route: str, fields: dict) -> dict:
        if route.endswith("/list") or route.endswith("/current"):
            return self.pages.pop(0)
        if route.endswith("/create"):
            self.next_id += 1
            code = "RK1" if self.dup_code else "RK" + str(self.next_id)
            return {"ok": True, "type": "other_in", "id": self.next_id, "code": code}
        if route.endswith("/delete"):
            self.deleted.add(fields["id"])
            return {"ok": True, "id": fields["id"], "deleted": True}
        if fields["id"] in self.deleted:
            raise U8CoError(404, "not_found", "单据不存在")
        return {"ok": True, "id": fields["id"], "code": "C" + str(fields["id"]), "state": {"amount": "100.00"}}


class CoreNameTests(unittest.TestCase):
    def test_names_and_resolvable_steps(self) -> None:
        names = known_names()
        for name in _NAMES:
            self.assertIn(name, names)
        keys = set(pick_keys())
        for spec in arap_gen_cases():
            self.assertTrue(spec.requires_login, spec.name)
            for need in spec.needs:
                self.assertIn(need, keys, spec.name)
            for step in spec.steps:
                self.assertIn(step.kind, _HANDLERS, spec.name)
                self._resolvable(spec, step)

    def _resolvable(self, spec: CaseSpec, step: Step) -> None:
        if step.kind in ("create", "apply"):
            self.assertTrue(callable(_make(step.sql_name)), spec.name)
        if step.kind in ("stash", "pred"):
            reader = core_sql if has_core(step.sql_name) else flow_sql
            self.assertIn("ROW:", reader(step.sql_name, "100", {"mo_alloc_line": "7", "qm_pin": "8", "pinv_line": "9", "arr_line": "10"}))
        if step.kind == "core":
            self.assertIn(step.sql_name, arap_gen_run._OPS, spec.name)

    def test_every_writer_deletes_what_it_made(self) -> None:
        for spec in arap_gen_cases():
            # 期望被拒绝的生单（非 200）不建单据，不要求后面删除。
            made = [
                step
                for step in spec.steps
                if (step.kind == "create" or step.route.endswith("/generate")) and step.status == 200
            ]
            if not made:
                continue
            last = max(spec.steps.index(step) for step in made)
            later = [step for step in spec.steps[last:] if step.route.endswith("/delete") and step.status == 200]
            self.assertTrue(later, spec.name)

    def test_arap_cycle_order_and_refusal(self) -> None:
        spec = _by_name("ar-bill-cycle")
        self.assertEqual(spec.needs, ("ar_master",))
        # 第一步是共用前置（arap_ctl）：账套没有应收受控科目时 N/A。
        self.assertEqual((spec.steps[0].kind, spec.steps[0].sql_name, dict(spec.steps[0].fields)), ("custom", "arap_ctl", {"side": "AR"}))
        self.assertEqual(spec.steps[1].sql_name, "ar_bill")
        routes = [(step.route, step.status) for step in spec.steps if step.kind == "call"]
        self.assertEqual(routes[0], ("/v1/vouchers/verify", 200))
        self.assertEqual(routes[1], ("/v1/vouchers/delete", 409))
        self.assertEqual(routes[-1], ("/v1/vouchers/load", 404))
        self.assertEqual(spec.steps[-1].code, "eq:0")
        refuse = _by_name("ar-receipt-voucher-refuse")
        self.assertEqual([(step.status, step.code) for step in refuse.steps[:2]], [(409, "state_mismatch")] * 2)
        self.assertEqual(refuse.steps[3].sql_name, "ar_receipt_pz")
        again = _by_name("mo-verify-audited").steps[0]
        self.assertEqual((again.status, again.code), (409, "state_mismatch"))
        self.assertEqual(_by_name("load-ap-bill").needs, ("load_ap_bill",))

    def test_mfg_cases_need_rd_testdata(self) -> None:
        self.assertEqual(_by_name("material-out-generate").data, ("stock.material_out_rd",))
        self.assertEqual(_by_name("product-in-generate").data, ("stock.product_in_rd",))
        bare = _by_name("material-out-no-rd")
        self.assertEqual(bare.data, ())
        self.assertTrue(bare.optional)
        self.assertEqual(bare.needs, _by_name("material-out-generate").needs)
        refuse = [step for step in bare.steps if step.kind == "custom"]
        self.assertEqual(len(refuse), 1)
        self.assertEqual((refuse[0].status, refuse[0].code, refuse[0].sql_name), (400, "bad_request", "dry_refuse"))
        self.assertIn(("@f", "head.crdcode"), refuse[0].fields)
        self.assertIn(("@b", "material_out_nord"), refuse[0].fields)


class CoreBodyTests(unittest.TestCase):
    def test_arap_bodies_use_allowed_columns(self) -> None:
        env = _env()
        for builder, head_ok, line_ok in (
            (ar_receipt_body, _CLOSE_HEAD, _CLOSE_LINE),
            (ap_payment_body, _CLOSE_HEAD, _CLOSE_LINE),
            (ar_bill_body, _VOUCH_HEAD, _VOUCH_LINE),
            (ap_bill_body, _VOUCH_HEAD, _VOUCH_LINE),
        ):
            body = builder(env)
            self.assertLessEqual({key.lower() for key in body["head"]}, head_ok, body["type"])
            self.assertLessEqual(_keys(body["lines"]), line_ok, body["type"])
            self.assertEqual(body["head"]["dVouchDate"], "2026-09-27")
        self.assertEqual(ar_receipt_body(env)["lines"][0], {"iType": 0, "cKm": "11229901", "iAmt": 100})
        self.assertEqual(ap_bill_body(env)["lines"][0], {"cCode": "64019901", "iAmount": 100})
        env.bag["ar_master"] = "C1|D|P|K|B|2"
        with self.assertRaises(ValueError):
            ar_receipt_body(env)
        env.bag["ap_master"] = "S1|K|B|2|x'"
        with self.assertRaises(ValueError):
            ap_payment_body(env)

    def test_generate_bodies(self) -> None:
        env = _env()
        out = material_out_gen(env)
        self.assertEqual(out["id"], 2000000101)
        self.assertEqual(out["lines"], [{"source_line_id": 2000000104, "quantity": 1}])
        self.assertEqual(out["head"]["cWhCode"], "13")
        sample = support.sample_testdata()["stock"]
        self.assertEqual(out["head"]["cRdCode"], sample["material_out_rd"])
        bare = material_out_nord(env)
        self.assertNotIn("cRdCode", bare["head"])
        self.assertEqual(bare["lines"], out["lines"])
        env.focus = "6122"
        made = product_in_gen(env)
        self.assertEqual(made["lines"], [{"source_line_id": 6122, "quantity": 25}])
        self.assertEqual(made["head"]["cWhCode"], "10")
        self.assertEqual(made["head"]["cRdCode"], sample["product_in_rd"])
        self.assertLessEqual({"ar_receipt", "ap_bill", "material_out_gen", "product_in_gen"}, set(CORE_BUILDERS))
        env.bag["qm_pin_qty"] = "0"
        with self.assertRaises(ValueError):
            product_in_gen(env)


class CorePurchaseTests(unittest.TestCase):
    def test_purchase_bodies(self) -> None:
        env = _env()
        env.focus = "2000000105"
        made = purchase_invoice_gen(env)
        code = made["head"]["cPBVCode"]
        self.assertTrue(code.startswith("EXE2E") and len(code) == 11)
        self.assertEqual(env.bag["pinv_code"], code)
        self.assertEqual(made["head"]["cPBVBillType"], "01")
        self.assertEqual(made["lines"], [{"source_line_id": 2000000106, "quantity": 1}])
        self.assertNotIn("source_type", made)
        self.assertEqual(purchase_invoice_dup(env)["head"]["cPBVCode"], code)
        self.assertNotIn("cPBVCode", purchase_invoice_nocode(env)["head"])
        self.assertEqual(purchase_invoice_badsrc(env)["source_type"], "dispatch")
        env.focus = "1060"
        got = purchase_in_qm_gen(env)
        self.assertEqual(got["source_type"], "qm_incoming_check")
        self.assertEqual(got["head"], {"cWhCode": "07"})
        self.assertEqual(got["lines"], [{"source_line_id": 1060, "quantity": 1}])

    def test_purchase_cases_and_sql(self) -> None:
        inv = _by_name("purchase-invoice-generate")
        self.assertEqual(inv.needs, ("pinv_src", "pinv_line"))
        expects = [(step.sql_name, step.status, step.code) for step in inv.steps if step.kind == "apply"]
        self.assertEqual(expects, [
            ("purchase_invoice_badsrc", 400, "bad_request"),
            ("purchase_invoice_nocode", 400, "bad_request"),
            ("purchase_invoice_gen", 200, ""),
            ("purchase_invoice_dup", 409, "state_mismatch"),
        ])
        self.assertEqual(inv.steps[-1].code, "same:pinv_po")
        qm = _by_name("purchase-in-from-qm")
        self.assertEqual(qm.needs, ("qmin_src", "qmin_wh"))
        self.assertEqual([step.code for step in qm.steps if step.kind == "pred"][:3],
                         ["plus1:qmin_fsum", "plus1:qmin_valid", "plus1:qmin_po"])
        text = dict(sample_sql())
        self.assertIn("iSumBillQuantity", text["core-pinv-src"])
        self.assertIn("PurSettleVouchs", text["core-pinv-src"])
        self.assertIn("FCONQUANTIY", text["core-qmin-src"])
        self.assertIn("ISNULL(s.iSumBillQuantity, 0)", text["core-pinv-billed"])
        self.assertIn("freceivedqty", text["core-qmin-po"])
        self.assertIn("fValidInQuan", text["core-qmin-valid"])


class CoreArrivalTests(unittest.TestCase):
    def test_arrival_bodies_and_case(self) -> None:
        env = _env()
        env.focus = "2000000102"
        made = arrival_gen(env)
        self.assertEqual(made, {"type": "arrival", "id": 2000000102, "head": {"cWhCode": "01"},
                                "lines": [{"source_line_id": 2000000103, "quantity": 1}]})
        self.assertEqual(arrival_over(env)["lines"][0]["quantity"], 3)
        env.bag["arr_over"] = "0"
        with self.assertRaises(ValueError):
            arrival_over(env)
        spec = _by_name("arrival-generate")
        self.assertEqual(spec.needs, ("arr_src", "arr_line", "arr_wh", "arr_over"))
        applies = [(step.sql_name, step.status, step.code) for step in spec.steps if step.kind == "apply"]
        self.assertEqual(applies, [("arrival_over", 409, "state_mismatch"), ("arrival_gen", 200, "")])
        loads = [step for step in spec.steps if step.kind == "core"]
        self.assertEqual(dict(loads[0].fields)["key"], "$last")
        self.assertEqual(spec.steps[-1].code, "same:arr_fpo")
        text = dict(sample_sql())
        self.assertIn("iArrQTY", text["core-arr-src"])
        self.assertIn("fPoArrQuantity", text["core-arr-fpo"])
        self.assertIn("d.ID = 100", text["core-arr-qty"])

    def test_load_code_uses_last_code(self) -> None:
        env = _env(FakeClient())
        env.made["a"] = "41"
        env.last_code = "C41"
        step = Step("core", fields=(("type", "arrival"), ("key", "$last")), sql_name="load_code", id_from="made")
        with mock.patch.object(FakeClient, "_answer", return_value={"ok": True, "id": 41, "code": "C41", "head": {"x": 1}}):
            self.assertEqual(arap_gen_run.load_code(env, CaseSpec("a", "a"), step)[0], "PASS")
        env.last_code = "C42"
        with mock.patch.object(FakeClient, "_answer", return_value={"ok": True, "id": 41, "code": "C41", "head": {"x": 1}}):
            self.assertEqual(arap_gen_run.load_code(env, CaseSpec("a", "a"), step)[0], "FAIL")


class CoreSqlTests(unittest.TestCase):
    def test_pick_and_readback_sql(self) -> None:
        text = dict(sample_sql())
        self.assertIn("Ar_Detail", text["core-ar-master"])
        self.assertIn("bcus = 1", text["core-ar-master"])
        self.assertIn("Ap_Detail", text["core-ap-master"])
        self.assertIn("cPzID", text["core-ar-voucher"])
        self.assertIn("WIPType = 3", text["core-mo-alloc"])
        self.assertIn("BMERGECHECKFLAG", text["core-qm-pin"])
        self.assertIn("FsumQuantity", text["core-qm-pin"])
        self.assertIn("ca_amoct", text["core-mo-free"])
        self.assertIn("rdrecords11", text["core-mo-free"])
        self.assertIn("iMPoIds = 100", text["core-mo-out-link"])
        self.assertIn("cVouchType = N'R0'", text["core-ar-bill-det"])
        self.assertIn("Ap_CloseBills", text["core-ar-receipt-gone"])
        self.assertIn("iCheckIdBaks = 100", text["core-pin-link"])
        self.assertIn("CONVERT(date, '2026-06-01', 23)", text["core-count-so"])
        self.assertEqual(len([key for key in _PACKS if key[0] in ("mo_alloc", "qm_pin")]), 2)
        with self.assertRaises(ValueError):
            core_sql("mo_iss", "100", {"mo_alloc_line": "1;drop"})
        with self.assertRaises(ValueError):
            core_sql("ar_bill_chk", "0", {})
        with self.assertRaises(ValueError):
            list_count_sql("sale_order", "C1'", "2026-01-01", "2026-01-02")
        with self.assertRaises(ValueError):
            list_count_sql("sale_order", "C1", "2026-1-1", "2026-01-02")
        with self.assertRaises(ValueError):
            stock_count_sql("01", "a b")


class CoreStepTests(unittest.TestCase):
    def test_burst_passes_and_cleans_up(self) -> None:
        client = FakeClient()
        env = _env(client)
        status, detail = arap_gen_run.burst(env, CaseSpec("x", "x"), Step("core"))
        self.assertEqual(status, "PASS", detail)
        sent = [route for route, _fields in client.calls]
        self.assertEqual(sent.count("/v1/vouchers/create"), 3)
        self.assertEqual(sent.count("/v1/vouchers/delete"), 3)
        self.assertEqual(len(client.deleted), 3)
        self.assertTrue(all(fields["acc"] == "801" for _route, fields in client.calls))

    def test_burst_duplicate_codes_fail_but_still_delete(self) -> None:
        client = FakeClient(dup_code=True)
        status, detail = arap_gen_run.burst(_env(client), CaseSpec("x", "x"), Step("core"))
        self.assertEqual(status, "FAIL")
        self.assertIn("重复", detail)
        self.assertEqual(len(client.deleted), 3)

    def test_load_same_checks_code_and_amount(self) -> None:
        env = _env(FakeClient())
        env.made["c"] = "77"
        env.last_code = "C77"
        step = Step("core", fields=(("type", "ar_receipt"),), sql_name="load_same", id_from="made")
        self.assertEqual(arap_gen_run.load_same(env, CaseSpec("c", "c"), step)[0], "PASS")
        env.last_code = "C78"
        self.assertEqual(arap_gen_run.load_same(env, CaseSpec("c", "c"), step)[0], "FAIL")

    def test_list_filter_and_keys(self) -> None:
        rows = [
            {"id": 5, "code": "SO5", "cus_code": "C000001", "doc_date": "2026-08-01", "ufts": "9"},
            {"id": 9, "code": "SO9", "cus_code": "C000001", "doc_date": "2026-09-23 00:00:00", "ufts": "10"},
        ]
        keys = [{"id": row["id"], "code": row["code"], "ufts": row["ufts"]} for row in rows]
        pages = [{"ok": True, "items": rows, "next": None, "watermark": "11"},
                 {"ok": True, "items": keys, "next": None, "watermark": "11"}]
        env = _env(FakeClient(pages))
        spec = CaseSpec("list-sale-order", "x")
        step = Step("core", fields=(("type", "sale_order"), ("pick", "so_list")))
        with mock.patch.object(doc_list, "_count", return_value=2):
            self.assertEqual(doc_list.list_filter(env, spec, step)[0], "PASS")
        self.assertEqual(doc_list.list_keys(env, spec, step)[0], "PASS")
        sent = env.client.calls[0][1]
        self.assertEqual(sent["filter"], {"cus_code": "C000001", "date_from": "2026-07-25", "date_to": "2026-09-23"})
        self.assertTrue(env.client.calls[1][1]["keys_only"])
        stray = dict(rows[0], cus_code="C1")
        env = _env(FakeClient([{"ok": True, "items": [stray], "next": None, "watermark": "1"}]))
        with mock.patch.object(doc_list, "_count", return_value=1):
            self.assertEqual(doc_list.list_filter(env, spec, step)[0], "FAIL")

    def test_list_page_since_and_stock(self) -> None:
        pages = [
            {"ok": True, "items": [{"id": 1}], "next": 1, "watermark": "5"},
            {"ok": True, "items": [{"id": 2}], "next": None, "watermark": "5"},
        ]
        env = _env(FakeClient(pages))
        spec = CaseSpec("s", "s")
        self.assertEqual(doc_list.list_page(env, spec, Step("core", fields=(("type", "ar_receipt"),)))[0], "PASS")
        self.assertEqual(env.client.calls[0][1]["limit"], 1)
        self.assertEqual(env.client.calls[1][1]["after"], 1)
        marks = [
            {"ok": True, "items": [{"id": 1, "code": "a", "ufts": "3"}], "next": 1, "watermark": "40"},
            {"ok": True, "items": [{"id": 88, "code": "b", "ufts": "41"}], "next": None, "watermark": "42"},
        ]
        env = _env(FakeClient(marks))
        env.made["s"] = "88"
        other = Step("core", fields=(("type", "other_in"),))
        self.assertEqual(doc_list.list_mark(env, spec, other)[0], "PASS")
        self.assertEqual(doc_list.list_since(env, spec, other)[0], "PASS")
        self.assertEqual(env.client.calls[1][1]["changed_since"], "40")
        stock = [{"ok": True, "items": [{"id": 20, "wh_code": "01", "inv_code": "INV0001"}], "next": None}]
        env = _env(FakeClient(stock))
        with mock.patch.object(doc_list, "_count", return_value=1):
            got = doc_list.stock_filter(env, spec, Step("core", fields=(("pick", "stock_pair"),)))
        self.assertEqual(got[0], "PASS", got[1])
        self.assertEqual({k: env.client.calls[0][1][k] for k in ("wh", "inv")}, {"wh": "01", "inv": "INV0001"})


if __name__ == "__main__":
    unittest.main()
