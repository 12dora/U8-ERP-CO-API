"""编辑 / 关闭 / 生单用例的登记、正文和回读断言。不连接服务器。"""

from __future__ import annotations

import unittest

from co.e2e.casespec.edit_close_gen_bodies import (
    close_other,
    dispatch_gen,
    invoice_gen,
    po_body,
    sale_out_gen,
    sale_update,
    transfer_body,
)
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.checks import View, match_checks
from co.e2e.casespec.model import CaseSpec, keep_step
from co.e2e.casespec.preds import judge_pred, store_cell
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.flow_read_sql import flow_sql
from co.e2e.casespec.steps import step_keep
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()

_BLOCKED = {
    "autoid",
    "cmaker",
    "cverifier",
    "chandler",
    "maker",
    "verifier",
    "editprop",
    "ufts",
    "cscloser",
    "isosid",
}
_EDIT_CLOSE_GEN = (
    "load-transfer",
    "load-sale-invoice",
    "load-purchase-invoice",
    "load-production-order",
    "sale-update",
    "sale-close",
    "po-cycle",
    "po-close-lines",
    "dispatch-generate",
    "invoice-generate",
    "purchase-in-generate",
    "transfer-cycle",
    "other-in-update",
    "sale-invoice-unverify-reject",
    "update-verified",
    "close-other-in",
)


def _by_name(name: str) -> CaseSpec:
    for spec in build_cases({}):
        if spec.name == name:
            return spec
    raise AssertionError(name)


def _env() -> object:
    env = type("E", (), {})()
    env.today = "2026-09-27"
    env.focus = "15"
    env.bag = {
        "sale_master": "C01|ST|D01|P01|INV|2.5|10|13|2026-09-28",
        "so_line": "88",
        "so_qty": "2.5000",
        "so_ship_wh": "06",
        "so_ship_line": "90",
        "po_master": "S000001|EX040||CG|普通采购|人民币|1.000000|13.000000|INV0007|1980.000000|T",
        "po_recv_wh": "12",
        "po_recv_line": "77",
        "transfer_master": "06|10|INV0006|EX031|EX092",
        "other_in_master": "01|101|D01|INV01",
        "oi_line": "5",
        "oi_qty": "2.0000",
    }
    return env


def _keys(body: dict) -> set[str]:
    found = set()
    head = body.get("head") or {}
    found.update(str(key).lower() for key in head)
    for line in body.get("lines") or []:
        found.update(str(key).lower() for key in line)
    return found


class FlowNameTests(unittest.TestCase):
    def test_public_names_and_load_count(self) -> None:
        names = known_names()
        for name in _EDIT_CLOSE_GEN:
            self.assertIn(name, names)
        self.assertIn("load-sale_order", names)
        loads = [spec for spec in build_cases({}) if spec.name.startswith("load-")]
        self.assertEqual(len(loads), 27)  # 含 load-sale-return-apply
        self.assertEqual(_by_name("load-sale-invoice").needs, ("load_sale_invoice",))
        self.assertEqual(_by_name("load-production-order").needs, ("load_production_order",))

    def test_writers_delete_and_negatives_reject(self) -> None:
        update = _by_name("sale-update")
        self.assertEqual(update.steps[-1].status, 404)
        self.assertEqual(update.steps[-1].route, "/v1/vouchers/load")
        self.assertEqual(update.steps[4].route, "/v1/vouchers/update")
        cycle = _by_name("po-cycle")
        self.assertEqual(cycle.steps[0].checks, (("code", "$prefix:PO"),))
        self.assertEqual(cycle.steps[1].kind, "readcode")
        self.assertEqual(cycle.steps[1].sql_name, "po")
        made = _by_name("dispatch-generate")
        deleted = [dict(step.fields).get("type") for step in made.steps if step.route.endswith("/delete")]
        self.assertEqual(deleted, ["sale_out", "dispatch"])
        self.assertEqual(_by_name("update-verified").steps[0].status, 409)
        self.assertEqual(_by_name("update-verified").steps[0].code, "state_mismatch")
        self.assertEqual(_by_name("close-other-in").steps[0].code, "bad_request")
        self.assertEqual(_by_name("sale-invoice-unverify-reject").steps[0].code, "u8_rejected")
        self.assertEqual(_by_name("transfer-cycle").steps[3].code, "eq:2")
        self.assertEqual(_by_name("transfer-cycle").steps[4].checks, (("generated#types", "other_out,other_in"),))

    def test_invoice_generate_checks_settle_and_checker(self) -> None:
        spec = _by_name("invoice-generate")
        self.assertEqual(spec.needs, ("inv_src", "inv_src_line"))
        self.assertTrue(spec.requires_login)
        kinds = [step.kind for step in spec.steps]
        self.assertEqual(
            kinds,
            ["stash", "stash", "apply", "keep", "pred", "call", "pred", "pred",
             "apply", "keep", "call", "pred", "call", "pred", "call"],
        )
        gen, grown, deleted, back, gone = (spec.steps[i] for i in (2, 4, 5, 6, 7))
        self.assertEqual(gen.route, "/v1/vouchers/generate")
        self.assertEqual(gen.sql_name, "invoice_gen")
        self.assertEqual(gen.id_from, "need")
        self.assertEqual(grown.code, "added:inv_settle+inv_qty")
        self.assertEqual(deleted.route, "/v1/vouchers/delete")
        self.assertEqual(dict(deleted.fields)["type"], "sale_invoice")
        self.assertEqual(deleted.checks, (("deleted", "true"),))
        self.assertEqual(back.code, "same:inv_settle")
        self.assertEqual(gone.code, "eq:0")
        self.assertEqual(gone.id_from, "bag:inv_id")
        verify, verified, unverify, cleared, refused = (spec.steps[i] for i in (10, 11, 12, 13, 14))
        self.assertEqual(dict(verify.fields)["action"], "verify")
        self.assertEqual(verify.id_from, "bag:inv_id")
        self.assertEqual(verified.code, "opname")
        self.assertEqual(dict(unverify.fields)["action"], "unverify")
        self.assertEqual(cleared.code, "empty")
        self.assertEqual((refused.status, refused.code), (-1, "200|409:state_mismatch"))


class FlowBodyTests(unittest.TestCase):
    def test_sale_update_adds_a_line_and_skips_blocked_fields(self) -> None:
        body = sale_update(_env())
        changed, added = body["lines"]
        self.assertEqual(changed["op"], "update")
        self.assertEqual(changed["line_id"], 88)
        self.assertEqual(changed["iQuantity"], 3.5)
        self.assertEqual(added["op"], "add")
        self.assertNotIn("line_id", added)
        self.assertEqual(body["head"], {"cMemo": "CO桥回归改"})
        self.assertTrue(_keys(body).isdisjoint(_BLOCKED))

    def test_po_transfer_and_generate_bodies(self) -> None:
        env = _env()
        made = po_body(env)
        self.assertEqual(made["lines"][0]["iTaxPrice"], 1980)
        self.assertNotIn("iUnitPrice", made["lines"][0])
        self.assertNotIn("cPersonCode", made["head"])
        self.assertNotIn("cMaker", made["head"])
        moved = transfer_body(env)
        self.assertEqual(moved["head"]["cORdCode"], "111")
        self.assertEqual(moved["head"]["cIRdCode"], "203")
        self.assertEqual(moved["lines"][0]["iTVQuantity"], 1)
        shipped = dispatch_gen(env)
        self.assertEqual(shipped["lines"][0]["quantity"], 1)
        self.assertEqual(shipped["head"]["cWhCode"], "06")
        self.assertNotIn("lines", sale_out_gen(env))
        self.assertEqual(close_other(env)["type"], "other_in")
        self.assertTrue(_keys(made).isdisjoint(_BLOCKED))
        self.assertTrue(_keys(moved).isdisjoint(_BLOCKED))
        with self.assertRaises(ValueError):
            env.bag["po_master"] = "short"
            po_body(env)

    def test_invoice_body_sends_remaining_quantity(self) -> None:
        env = _env()
        env.bag["inv_src_line"] = "44"
        env.bag["inv_qty"] = "3.5000"
        body = invoice_gen(env)
        self.assertEqual(body["type"], "sale_invoice")
        self.assertEqual(body["id"], 15)
        self.assertEqual(body["lines"], [{"source_line_id": 44, "quantity": 3.5}])
        self.assertNotIn("head", body)
        self.assertTrue(_keys(body).isdisjoint(_BLOCKED))
        env.bag["inv_qty"] = "10.0000"
        self.assertEqual(invoice_gen(env)["lines"][0]["quantity"], 10)
        env.bag["inv_qty"] = "0"
        with self.assertRaises(ValueError):
            invoice_gen(env)


class FlowSqlTests(unittest.TestCase):
    def test_pick_predicates_and_safe_ids(self) -> None:
        text = dict(sample_sql())
        self.assertIn("iFHQuantity", text["flow-so-ship"])
        self.assertIn("bWhPos = 0", text["flow-so-ship"])
        self.assertIn("bInvBatch", text["flow-so-ship"])
        self.assertIn("bPropertyCheck", text["flow-po-recv"])
        self.assertIn("iReceivedQTY", text["flow-po-recv"])
        self.assertIn("bDepEnd = 1", text["flow-transfer"])
        self.assertIn("iQuantity >= 2", text["flow-transfer"])
        self.assertIn("cVerifier", text["flow-sale-invoice"])
        src = text["flow-invoice-src"]
        self.assertIn("cVouchType = N'05'", src)
        self.assertIn("bReturnFlag = 0", src)
        self.assertIn("cVerifier", src)
        self.assertIn("rdrecords32", src)
        # 部分开票的行仍可挑：已开票数量在 iSettleQuantity 里，另要求没有已复核的发票、没有未开票的退货。
        self.assertIn("JOIN dbo.SaleBillVouch AS s ON s.SBVID = b.SBVID", src)
        self.assertIn("ISNULL(s.cChecker, N''))) <> N''", src)
        self.assertIn("ISNULL(l.fretqtywkp, 0) = 0", src)
        self.assertIn("ISNULL(l.iQuantity, 0) - ISNULL(l.iSettleQuantity, 0) > 0", src)
        self.assertIn("iDLsID = 100", text["flow-inv-settle"])
        self.assertIn("ROUND(ISNULL(d.iQuantity, 0) - ISNULL(d.iSettleQuantity, 0), 4, 1)", text["flow-inv-qty"])
        self.assertIn("cChecker", text["flow-inv-checker"])
        self.assertIn("COUNT(SBVID)", text["flow-inv-gone"])
        self.assertIn("SBVID = 100", text["flow-inv-gone"])
        self.assertIn("cPOID", text["code-po"])
        self.assertIn("iSOsID = 100", text["flow-so-back"])
        self.assertIn("N'调拨'", text["flow-tv-kids"])
        with self.assertRaises(ValueError):
            flow_sql("so_sum", "0", {})
        with self.assertRaises(ValueError):
            flow_sql("so_qty", "100", {"so_line": "1;drop"})
        with self.assertRaises(ValueError):
            flow_sql("missing", "100", {})
        with self.assertRaises(ValueError):
            flow_sql("inv_qty", "100", {"inv_src_line": "1;drop"})

    def test_predicates_and_keep(self) -> None:
        bag = {"so_qty": "2.5000", "po_isum": "10.7800", "so_fh": "4.0000", "inv_settle": "1.5000", "inv_qty": "2.2500"}
        self.assertEqual(judge_pred("12.5000", "gt0", bag, "甲")[0], "PASS")
        self.assertEqual(judge_pred("21.5600", "double:po_isum", bag, "甲")[0], "PASS")
        self.assertEqual(judge_pred("5.0000", "plus1:so_fh", bag, "甲")[0], "PASS")
        self.assertEqual(judge_pred("4.0000", "same:so_fh", bag, "甲")[0], "PASS")
        self.assertEqual(judge_pred("甲|", "lineclosed", bag, "甲")[0], "PASS")
        self.assertEqual(judge_pred("|", "lineopen", bag, "甲")[0], "PASS")
        back = "2|3.5000|1|操作员甲"
        self.assertEqual(judge_pred(back, "saleupd:2", bag, "操作员甲")[0], "PASS")
        self.assertEqual(judge_pred(back, "saleupd:1", bag, "操作员甲")[0], "FAIL")
        self.assertEqual(judge_pred("", "empty", bag, "甲")[0], "PASS")
        self.assertEqual(judge_pred("3.7500", "added:inv_settle+inv_qty", bag, "甲")[0], "PASS")
        self.assertEqual(judge_pred("4.0000", "added:inv_settle+inv_qty", bag, "甲")[0], "FAIL")
        self.assertEqual(judge_pred("操作员甲", "opname", bag, "操作员甲")[0], "PASS")
        self.assertEqual(judge_pred("别人", "opname", bag, "操作员甲")[0], "FAIL")
        store_cell(bag, "po_line", "77")
        self.assertEqual(bag["po_line"], "77")
        with self.assertRaises(ValueError):
            store_cell(bag, "po_line", "1;drop")
        view = View("15", "", "2026-09-27", "甲")
        payload = {"code": "PO0000000001", "generated": [{"type": "other_out", "id": 1}, {"type": "other_in", "id": 2}]}
        status, detail = match_checks(payload, (("code", "$prefix:PO"), ("generated#types", "other_out,other_in")), view)
        self.assertEqual(status, "PASS", detail)
        env = type("E", (), {})()
        env.made = {"demo": "15"}
        env.bag = {}
        env.focus = ""
        status, detail = step_keep(env, CaseSpec("demo", "演示"), keep_step("disp_id"))
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(env.bag["disp_id"], "15")


if __name__ == "__main__":
    unittest.main()
