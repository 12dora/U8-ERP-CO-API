"""空账套造单据（co/e2e/seed/steps_docs_*）：金额、正文、按标记幂等、审批循环、步骤顺序。不连接服务器。"""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from co.e2e.remote import RemoteResult
from co.e2e.seed import (
    data,
    steps_docs_arap,
    steps_docs_base,
    steps_docs_mo,
    steps_docs_purchase,
    steps_docs_qc,
    steps_docs_sale,
    steps_docs_stock,
)
from co.e2e.seed.context import Link, SeedContext, SeedError
from co.e2e.seed.runner import ALL_STEPS, DOC_STEPS, select
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

DAY = "2026-10-01"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["\t".join(header), "\t".join("-" * len(name) for name in header)]
    lines += ["\t".join(row) for row in rows]
    return "\n".join(lines) + "\n"


class FakeDocServer:
    """按标记查单据、按 COUNT 查是否已审核；新建、生单都成功，主键从 1000000001 起递增。
    write_memo 为真时像桥一样把表头 cmemo 写进单据（之后按该标记能查到新主键）。"""

    def __init__(self) -> None:
        self.marked: dict[str, list[str]] = {}
        self.verified = "0"
        self.posts: list[tuple[str, dict]] = []
        self.sqls: list[str] = []
        self.wf: list[str] = []
        self.next_id = 1000000001
        self.write_memo = True

    def post(self, path: str, body: dict) -> dict:
        self.posts.append((path, body))
        if path.endswith("workflow/state"):
            return {"ok": True, "wf": {"status": self.wf.pop(0) if self.wf else "approved"}}
        doc_id = self.next_id
        self.next_id += 1
        memo = (body.get("head") or {}).get("cmemo")
        if self.write_memo and memo:
            self.marked.setdefault(memo, []).append(str(doc_id))
        return {"ok": True, "type": body.get("type"), "id": doc_id}

    def sql(self, query: str) -> RemoteResult:
        self.sqls.append(query)
        for mark, ids in self.marked.items():
            if "N'" + mark + "'" in query:
                return RemoteResult(0, table(["id"], [[item] for item in ids]))
        if "COUNT(*) AS n" in query:
            return RemoteResult(0, table(["n"], [[self.verified]]))
        return RemoteResult(0, table(["id"], []))

    def link(self) -> Link:
        return Link(self.post, self.sql, lambda: None, "op001", DAY, ["secret-x"])


class DocCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.server = FakeDocServer()
        self.ctx = SeedContext(Path(self.tmp.name), self.server.link())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def paths(self) -> list[str]:
        return [path for path, _body in self.server.posts]


def _step(name: str):
    return next(step for step in ALL_STEPS if step.name == name)


class MoneyTest(unittest.TestCase):
    def test_live_line(self) -> None:
        got = steps_docs_base.taxed_amounts(5, 10)
        expected = {"iunitprice": 8.85, "imoney": 44.25, "itax": 5.75, "isum": 50.0, "itaxrate": 13}
        for key, value in expected.items():
            self.assertEqual(got[key], value, key)
        self.assertEqual(got["inatmoney"], got["imoney"])
        self.assertEqual(got["itaxunitprice"], 10.0)

    def test_sum_is_money_plus_tax(self) -> None:
        for qty, price in ((3, "9.99"), (7, "12.34"), (1, "0.01")):
            got = steps_docs_base.taxed_amounts(qty, price)
            total = Decimal(str(got["imoney"])) + Decimal(str(got["itax"]))
            self.assertEqual(total, Decimal(str(got["isum"])))
            self.assertEqual(Decimal(str(got["isum"])), (Decimal(qty) * Decimal(price)).quantize(Decimal("0.01")))


class BodyTest(unittest.TestCase):
    def test_sale_order(self) -> None:
        body = steps_docs_sale.so_body(DAY, "so_main")
        self.assertEqual(body["head"]["ccuscode"], "C0001")
        self.assertEqual(body["head"]["cmemo"], "seed:so_main")
        self.assertEqual(len(body["lines"]), 2)
        self.assertEqual(body["lines"][0]["dpredate"], "2026-10-08")
        self.assertEqual(body["lines"][0]["isum"], 50.0)

    def test_sale_generates(self) -> None:
        dispatch = steps_docs_sale.dispatch_body(11, 22)
        self.assertEqual(dispatch["lines"], [{"source_line_id": 22, "quantity": 2, "cwhcode": "06"}])
        self.assertEqual(steps_docs_sale.sale_out_body(33), {"type": "sale_out", "id": 33})
        invoice = steps_docs_sale.invoice_body(33, 44)
        self.assertEqual(invoice["head"]["cvouchtype"], "26")
        self.assertEqual(invoice["lines"][0]["quantity"], 1)

    def test_purchase_order_has_no_money_fields(self) -> None:
        body = steps_docs_purchase.plain_po_body(DAY, "po_main")
        self.assertEqual(body["head"]["cMemo"], "seed:po_main")
        for line in body["lines"]:
            self.assertEqual(set(line), {"cInvCode", "iQuantity", "iUnitPrice", "iPerTaxRate"})

    def test_arrival_and_inbound_use_separate_orders(self) -> None:
        names = [step.name for step in steps_docs_purchase.STEPS]
        self.assertLess(names.index("po_arr"), names.index("po_in"))
        self.assertLess(names.index("po_in"), names.index("po_in_inbound"))
        self.assertNotIn("po_main", names)

    def test_purchase_generates(self) -> None:
        arrival = steps_docs_purchase.arrival_body(1, 2, 3, "arrival")
        self.assertEqual(arrival["head"], {"cWhCode": "06", "cMemo": "seed:arrival"})
        pin = steps_docs_purchase.purchase_in_body(1, 2, "purchase_in")
        self.assertEqual(pin["head"]["cwhcode"], "06")
        invoice = steps_docs_purchase.invoice_body(5, 6)
        self.assertTrue(all(isinstance(value, str) for value in invoice["head"].values()))
        self.assertEqual(invoice["lines"], [{"source_line_id": 6, "quantity": 1}])

    def test_stock_bodies(self) -> None:
        other_in = steps_docs_stock.other_in_body(DAY)
        codes = [line["cinvcode"] for line in other_in["lines"]]
        self.assertEqual(codes, [code for code, _name, _flags in data.INVENTORY_ROLES])
        batch = [line for line in other_in["lines"] if "cbatch" in line]
        self.assertEqual([line["cinvcode"] for line in batch], [steps_docs_stock.BATCH_INVENTORY])
        self.assertTrue(all(line["iquantity"] >= 10 for line in other_in["lines"]))
        self.assertEqual(steps_docs_stock.other_out_body(DAY)["lines"][0]["iquantity"], 2)
        head = steps_docs_stock.transfer_body(DAY)["head"]
        self.assertEqual((head["cowhcode"], head["ciwhcode"], head["cordcode"], head["cirdcode"]), ("06", "07", "111", "203"))

    def test_bom_and_mo(self) -> None:
        bom = steps_docs_stock.bom_body()
        self.assertEqual(bom["head"]["inv_code"], steps_docs_stock.BOM_PARENT)
        self.assertEqual(bom["head"]["eff_date"], data.SEED_DATE)
        self.assertEqual(len(bom["lines"]), 3)
        mo = steps_docs_mo.mo_body(DAY, "1")
        line = mo["lines"][0]
        self.assertEqual((line["inv_code"], line["mo_type"], line["due_date"]), (steps_docs_stock.BOM_PARENT, "1", "2026-10-08"))
        self.assertEqual(mo["head"], {"remark": "seed:mo"})

    def test_plain_bom_and_mfg_bodies(self) -> None:
        bom = steps_docs_stock.bom_body()
        self.assertTrue(all(line["wh_code"] == "06" for line in bom["lines"]))
        plain = steps_docs_stock.bom_body(data.PLAIN_BOM_PARENT, "bom_plain")
        self.assertEqual((plain["head"]["inv_code"], plain["head"]["version_desc"]), ("91020008", "seed:bom_plain"))
        self.assertEqual([line["inv_code"] for line in plain["lines"]], list(steps_docs_stock.BOM_CHILDREN))
        flags = dict((code, flags) for code, _name, flags in data.INVENTORY_ROLES)
        self.assertEqual(flags[data.PLAIN_BOM_PARENT]["bPropertyCheck"], 0)
        self.assertEqual(flags[steps_docs_stock.BOM_PARENT]["bPropertyCheck"], 1)
        mo = steps_docs_mo.mo_body(DAY, "1", data.PLAIN_BOM_PARENT, steps_docs_mo.MO_PLAIN_QTY, "mo_plain")
        self.assertEqual(mo["head"], {"remark": "seed:mo_plain"})
        self.assertEqual((mo["lines"][0]["inv_code"], mo["lines"][0]["qty"]), ("91020008", 5))
        out = steps_docs_mo.material_out_body(11, 22)
        self.assertEqual((out["type"], out["id"]), ("material_out", 11))
        self.assertEqual(out["head"], {"cwhcode": "06", "crdcode": data.MATERIAL_OUT_RD, "cmemo": "seed:mo_out"})
        self.assertEqual(out["lines"], [{"source_line_id": 22, "quantity": 2}])
        pin = steps_docs_mo.product_in_body(33)
        self.assertEqual((pin["type"], pin["id"]), ("product_in", 33))
        self.assertEqual(pin["head"], {"cwhcode": "06", "crdcode": data.PRODUCT_IN_RD, "cmemo": "seed:mo_pin"})
        self.assertEqual(pin["lines"], [{"source_line_id": 33, "quantity": 2}])
        # 第三张检验单入库 2 个后还剩 >= 2 个给 qm_pin；订单还剩 >= 2 个没报检给 qmp。
        self.assertGreaterEqual(steps_docs_mo.PASS_QTY - steps_docs_mo.PIN_QTY, 2)
        declared = steps_docs_mo.INSPECTS * steps_docs_mo.INSPECT_QTY + steps_docs_mo.PASS_QTY
        self.assertGreaterEqual(steps_docs_mo.MO_QTY - declared, 2)
        self.assertGreaterEqual(steps_docs_mo.MO_PLAIN_QTY, 3)

    def test_rd_styles_cover_mfg(self) -> None:
        styles = {arc.code: arc.fields["rsflag"] for arc in data.RD_STYLES}
        self.assertEqual(styles[data.MATERIAL_OUT_RD], 0)
        self.assertEqual(styles[data.PRODUCT_IN_RD], 1)
        self.assertEqual((data.MATERIAL_OUT_RD, data.PRODUCT_IN_RD), ("102", "202"))

    def test_children_keep_stock_after_material_out(self) -> None:
        lines = {line["cinvcode"]: line["iquantity"] for line in steps_docs_stock.other_in_body(DAY)["lines"]}
        for code in steps_docs_stock.BOM_CHILDREN:
            self.assertGreaterEqual(lines[code] - steps_docs_mo.OUT_QTY, 10, code)

    def test_quality_bodies(self) -> None:
        check = steps_docs_qc.check_body("qm_incoming_check", 7, 8, 3, "P01")
        self.assertEqual(check["head"]["cCheckPersonCode"], data.INSPECTORS[0])
        self.assertEqual(check["head"]["project_code"], "P01")
        self.assertIn("chDefine13", check["head"])
        pin = steps_docs_qc.qc_in_body(9)
        self.assertEqual((pin["id"], pin["lines"][0]["source_line_id"], pin["source_type"]), (9, 9, "qm_incoming_check"))
        inspect = steps_docs_qc.inspect_body("qm_product_inspect", "production_order", 1, 2, 2)
        self.assertEqual(inspect["lines"], [{"source_line_id": 2, "quantity": 2}])

    def test_arap_bodies(self) -> None:
        receipt = steps_docs_arap.receipt_body(DAY, "11229901")
        self.assertEqual(receipt["head"]["cDwCode"], "C0001")
        self.assertEqual(receipt["lines"][0]["cKm"], "11229901")
        payment = steps_docs_arap.payment_body(DAY, "22020101")
        self.assertEqual(payment["head"]["cDwCode"], data.VENDORS[0].code)
        self.assertEqual(steps_docs_arap.voucher_body(5), {"flag": "AR", "type": "ar_receipt", "id": 5})
        with self.assertRaises(SeedError):
            steps_docs_arap.km_sql("ar_receipt", "bad")


class SqlTextTest(unittest.TestCase):
    def test_lookups_pass_guard(self) -> None:
        texts = [steps_docs_base.memo_sql(kind, "k") for kind in steps_docs_base.MEMO_TABLE]
        texts += [sql.format(id=1) for sql in steps_docs_base.VERIFIED_SQL.values()]
        texts += [
            steps_docs_qc.reassign_sql("QM03", 1, "op001"),
            steps_docs_qc.PROJECT_SQL,
            steps_docs_mo.MOTYPE_SQL,
            steps_docs_mo.ALLOC_SQL.format(id=1),
            steps_docs_mo.PASS_INSPECT_SQL.format(id=1, qty=steps_docs_mo.PASS_QTY),
            steps_docs_qc.reassign_sql("QM04", 1, "op001"),
            steps_docs_arap.km_sql("ap_payment", DAY),
            steps_docs_sale.SO_LINES_SQL.format(id=1),
            steps_docs_purchase.RD01_LINES_SQL.format(id=1),
        ]
        for text in texts:
            self.assertEqual(sql_block_reason("SET NOCOUNT ON;\n" + text), "", text)

    def test_memo_sql_uses_marker(self) -> None:
        self.assertIn("cMemo = N'seed:so_main'", steps_docs_base.memo_sql("sale_order", "so_main"))
        self.assertIn("cTVMemo", steps_docs_base.memo_sql("transfer", "transfer"))

    def test_bad_ids_and_biz_refused(self) -> None:
        with self.assertRaises(SeedError):
            steps_docs_base.safe_id("1; DROP")
        with self.assertRaises(SeedError):
            steps_docs_qc.reassign_sql("QM05", 1, "op001")


class IdempotencyTest(DocCase):
    def test_marked_doc_is_reused(self) -> None:
        self.server.marked["seed:other_in"] = ["1000000077"]
        self.server.verified = "1"
        self.assertEqual(_step("other_in").apply(self.ctx), (0, 1))
        self.assertEqual(self.server.posts, [])
        self.assertEqual(self.ctx.state["docs"]["other_in"], [1000000077])

    def test_missing_doc_is_created_and_verified(self) -> None:
        self.assertEqual(_step("other_in").apply(self.ctx), (1, 0))
        self.assertEqual(self.paths(), ["v1/vouchers/create", "v1/vouchers/verify"])
        verify = self.server.posts[1][1]
        self.assertEqual(verify, {"type": "other_in", "id": 1000000001, "action": "verify"})
        self.assertEqual(self.ctx.state["docs"]["other_in"], [1000000001])

    def test_verified_doc_is_not_verified_again(self) -> None:
        self.server.marked["seed:so_main"] = ["1000000005"]
        self.server.verified = "1"
        self.assertEqual(_step("so_main").apply(self.ctx), (0, 1))
        self.assertEqual(self.server.posts, [])

    def test_probe_reports_missing(self) -> None:
        found = _step("so_open").probe(self.ctx)
        self.assertEqual((found.existing, found.missing), ((), ("so_open#1",)))

    def test_downstream_without_upstream_fails(self) -> None:
        with self.assertRaises(SeedError) as caught:
            _step("dispatch").apply(self.ctx)
        self.assertIn("so_main", str(caught.exception))
        self.assertEqual(self.server.posts, [])

    def test_arrival_comes_from_po_arr_and_inbound_from_po_in(self) -> None:
        self.server.marked["seed:po_arr"] = ["1000000011"]
        self.server.marked["seed:po_in"] = ["1000000022"]
        self.server.verified = "1"
        plain = self.server.sql
        lines = RemoteResult(0, table(["id"], [["5"]]))
        self.ctx.link.sql = lambda query: lines if "FROM PO_Podetails" in query else plain(query)
        _step("po_arr_arrival").apply(self.ctx)
        _step("po_in_inbound").apply(self.ctx)
        made = [body for path, body in self.server.posts if path.endswith("generate")]
        self.assertEqual([(body["type"], body["id"]) for body in made], [("arrival", 1000000011), ("purchase_in", 1000000022)])

    def test_failure_carries_response(self) -> None:
        self.ctx.link.post = lambda path, body: {"ok": False, "status": 400, "message": "不能设置字段 imoney"}
        with self.assertRaises(SeedError) as caught:
            _step("po_open").apply(self.ctx)
        self.assertIn("imoney", str(caught.exception))
        self.assertEqual(caught.exception.response["status"], 400)


class MfgChainTest(DocCase):
    """生产链的新步骤：按标记幂等、依赖没满足时不发请求、QM04 审批后才入库。"""

    def mfg_sql(self, inspects: list[str], checks: list[str], passed: list[str] | None = None) -> None:
        # passed：按数量 PASS_QTY 查到的产品报检单，缺省是 inspects 的第三张。
        plain = self.server.sql
        by_qty = inspects[2:3] if passed is None else passed

        def sql(query: str) -> RemoteResult:
            if "FROM mom_moallocate" in query or "SELECT MoDId AS id FROM mom_orderdetail" in query:
                self.server.sqls.append(query)
                return RemoteResult(0, table(["id"], [["77"]]))
            if "b.FQUANTITY = " in query:
                self.server.sqls.append(query)
                return RemoteResult(0, table(["id"], [[item] for item in by_qty]))
            if "FROM QMINSPECTVOUCHER AS h" in query:
                self.server.sqls.append(query)
                return RemoteResult(0, table(["id"], [[item] for item in inspects]))
            if "FROM QMCHECKVOUCHER WHERE CVOUCHTYPE" in query:
                self.server.sqls.append(query)
                return RemoteResult(0, table(["id"], [[item] for item in checks]))
            return plain(query)

        self.ctx.link.sql = sql

    def generated(self) -> list[dict]:
        return [body for path, body in self.server.posts if path.endswith("generate")]

    def test_material_out_reused_by_marker(self) -> None:
        self.server.marked["seed:mo_out"] = ["1000000044"]
        self.assertEqual(_step("mo_out").apply(self.ctx), (0, 1))
        self.assertEqual(self.server.posts, [])

    def test_material_out_from_first_allocate_line(self) -> None:
        self.server.marked["seed:mo"] = ["1000000031"]
        self.mfg_sql([], [])
        self.assertEqual(_step("mo_out").apply(self.ctx), (1, 0))
        body = self.generated()[0]
        self.assertEqual((body["type"], body["id"], body["lines"][0]["source_line_id"]), ("material_out", 1000000031, 77))
        self.assertEqual(body["head"]["crdcode"], data.MATERIAL_OUT_RD)
        # 材料出库不审核。
        self.assertEqual(self.paths(), ["v1/vouchers/generate"])

    def test_material_out_without_marker_stops(self) -> None:
        self.server.marked["seed:mo"] = ["1000000031"]
        self.server.write_memo = False
        self.mfg_sql([], [])
        with self.assertRaises(SeedError) as caught:
            _step("mo_out").apply(self.ctx)
        self.assertIn("造数标记没有写进单据（cmemo），重跑会重复生成", str(caught.exception))
        # 报错带生单那次调用的记录，不是之后按标记找单的 SQL 输出。
        self.assertTrue(caught.exception.saved.endswith("v1-vouchers-generate.json"), caught.exception.saved)

    def test_product_in_without_marker_stops(self) -> None:
        self.server.marked["seed:mo"] = ["1000000031"]
        self.server.verified = "1"
        self.server.write_memo = False
        self.mfg_sql(["5", "6", "7"], ["9"])
        with self.assertRaises(SeedError) as caught:
            _step("mo_pin").apply(self.ctx)
        self.assertIn("造数标记没有写进单据（cmemo）", str(caught.exception))

    def test_pass_inspect_prefers_recorded_id(self) -> None:
        self.server.marked["seed:mo"] = ["1000000031"]
        self.mfg_sql(["5", "6", "7"], [], passed=["7"])
        self.ctx.state[steps_docs_mo.PASS_STATE_KEY] = 6
        self.assertEqual(steps_docs_mo.find_pass_inspect(self.ctx), [6])
        # 记下的主键已不在这张订单的报检单里：按数量找，不看排第几。
        self.ctx.state[steps_docs_mo.PASS_STATE_KEY] = 99
        self.mfg_sql(["7", "5", "6"], [], passed=["7"])
        self.assertEqual(steps_docs_mo.find_pass_inspect(self.ctx), [7])

    def test_pass_inspect_records_new_id(self) -> None:
        self.server.marked["seed:mo"] = ["1000000031"]
        self.mfg_sql(["5", "6"], [], passed=[])
        self.assertEqual(_step("mo_inspect3").apply(self.ctx), (1, 0))
        self.assertEqual(self.ctx.state[steps_docs_mo.PASS_STATE_KEY], 1000000001)
        self.assertEqual(self.generated()[0]["lines"][0]["quantity"], steps_docs_mo.PASS_QTY)

    def test_plain_mo_created_and_verified(self) -> None:
        plain = self.server.sql
        motype = RemoteResult(0, table(["code"], [["1"]]))
        self.ctx.link.sql = lambda query: motype if "FROM mom_motype" in query else plain(query)
        self.assertEqual(_step("mo_plain").apply(self.ctx), (1, 0))
        created = self.server.posts[0][1]
        self.assertEqual(created["head"], {"remark": "seed:mo_plain"})
        self.assertEqual(created["lines"][0]["inv_code"], data.PLAIN_BOM_PARENT)
        self.assertEqual(self.paths()[-1], "v1/vouchers/verify")

    def test_third_inspect_waits_for_first_two(self) -> None:
        self.server.marked["seed:mo"] = ["1000000031"]
        self.mfg_sql(["5"], [])
        with self.assertRaises(SeedError) as caught:
            _step("mo_inspect3").apply(self.ctx)
        self.assertIn("mo_inspect", str(caught.exception))
        self.assertEqual(self.server.posts, [])

    def test_third_inspect_and_check_reused(self) -> None:
        self.server.marked["seed:mo"] = ["1000000031"]
        self.mfg_sql(["5", "6", "7"], ["9"])
        self.assertEqual(_step("mo_inspect3").apply(self.ctx), (0, 1))
        self.assertEqual(_step("qm04_pass").apply(self.ctx), (0, 1))
        self.assertEqual(self.ctx.state["docs"]["mo_inspect3"], [7])
        self.assertEqual(self.server.posts, [])

    def test_product_in_needs_approved_check(self) -> None:
        self.server.marked["seed:mo"] = ["1000000031"]
        self.mfg_sql(["5", "6", "7"], ["9"])
        with self.assertRaises(SeedError) as caught:
            _step("mo_pin").apply(self.ctx)
        self.assertIn("qm04_approve", str(caught.exception))
        self.assertEqual(self.server.posts, [])
        self.server.verified = "1"
        self.assertEqual(_step("mo_pin").apply(self.ctx), (1, 0))
        body = self.generated()[0]
        self.assertEqual((body["type"], body["id"], body["head"]["crdcode"]), ("product_in", 9, data.PRODUCT_IN_RD))

    def test_qm04_approve_reassigns_qm04(self) -> None:
        self.server.marked["seed:mo"] = ["1000000031"]
        self.mfg_sql(["5", "6", "7"], ["9"])
        self.server.wf = ["not_submitted", "in_approval", "approved"]
        self.assertEqual(_step("qm04_approve").apply(self.ctx), (1, 0))
        refs = [body for path, body in self.server.posts if path.endswith("workflow/submit")]
        self.assertEqual(refs, [{"type": "qm_product_check", "id": 9}])
        updates = [sql for sql in self.server.sqls if "UPDATE Table_Task" in sql]
        self.assertEqual(len(updates), 1)
        self.assertIn("N'QM04'", updates[0])

    def test_qm04_approve_skips_when_done(self) -> None:
        self.server.marked["seed:mo"] = ["1000000031"]
        self.mfg_sql(["5", "6", "7"], ["9"])
        self.server.verified = "1"
        self.assertEqual(_step("qm04_approve").apply(self.ctx), (0, 1))
        self.assertEqual(self.server.posts, [])


class ApproveTest(DocCase):
    def test_submit_then_approve_until_done(self) -> None:
        self.server.wf = ["not_submitted", "in_approval", "in_approval", "approved"]
        rounds = steps_docs_qc.approve_all(self.ctx, "qm_incoming_check", "QM03", 12)
        self.assertEqual(rounds, 2)
        tail = [path.rsplit("/", 1)[-1] for path in self.paths()]
        self.assertEqual(tail, ["state", "submit", "state", "approve", "state", "approve", "state"])
        updates = [sql for sql in self.server.sqls if "UPDATE Table_Task" in sql]
        self.assertEqual(len(updates), 2)
        self.assertIn("N'QM03'", updates[0])

    def test_not_controlled_stops_with_hint(self) -> None:
        self.server.wf = ["not_controlled"]
        with self.assertRaises(SeedError) as got:
            steps_docs_qc.approve_all(self.ctx, "qm_incoming_check", "QM03", 12)
        self.assertIn("审批流", str(got.exception))
        self.assertEqual(self.paths(), ["v1/workflow/state"])

    def test_unexpected_state_stops(self) -> None:
        self.server.wf = ["returned", "returned"]
        with self.assertRaises(SeedError):
            steps_docs_qc.approve_all(self.ctx, "qm_incoming_check", "QM03", 12)


class OrderTest(unittest.TestCase):
    def test_docs_after_archives(self) -> None:
        names = [step.name for step in ALL_STEPS]
        last_archive = max(names.index("inventories"), names.index("operator_link"), names.index("vendors"))
        for step in DOC_STEPS:
            self.assertGreater(names.index(step.name), last_archive, step.name)
        self.assertLess(names.index("bom"), names.index("mo"))
        self.assertLess(names.index("other_in"), names.index("dispatch"))
        self.assertLess(names.index("qc_check"), names.index("qc_approve"))
        self.assertLess(names.index("rd_styles"), names.index("testdata"))
        self.assertLess(names.index("bom_plain"), names.index("mo_plain"))
        self.assertLess(names.index("other_in"), names.index("mo_out"))
        self.assertLess(names.index("mo"), names.index("mo_out"))
        chain = ["mo_inspect", "qm04", "mo_inspect3", "qm04_pass", "qm04_approve", "mo_pin"]
        self.assertEqual(sorted(chain, key=names.index), chain)
        self.assertLess(names.index("qc_approve"), names.index("qm04_approve"))
        self.assertEqual(len(set(names)), len(names))

    def test_skip(self) -> None:
        names = [step.name for step in select("", "qc_approve,qc_in")]
        self.assertNotIn("qc_approve", names)
        self.assertIn("qc_check", names)
        self.assertEqual([step.name for step in select("bom,so_open", "bom")], ["so_open"])
        with self.assertRaises(SeedError):
            select("", "nope")


if __name__ == "__main__":
    unittest.main()
