"""造数追加的应付核销链、预收 / 预付款单、外币汇率和外币单据（steps_docs_arap_more、steps_docs_fx）。不连接服务器。"""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from co.e2e.remote import RemoteResult
from co.e2e.seed import steps_docs_arap_more as more
from co.e2e.seed import steps_docs_fx as fx
from co.e2e.seed.context import Link, SeedContext, SeedError
from co.e2e.seed.runner import ALL_STEPS, DOC_STEPS
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

DAY = "2026-10-02"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["\t".join(header), "\t".join("-" * len(name) for name in header)]
    lines += ["\t".join(row) for row in rows]
    return "\n".join(lines) + "\n"


class FakeServer:
    """按 SQL 里的片段回答：answers 是（片段, 表头, 行）的列表，先命中的说了算；没命中回空表。新建都成功，主键递增。"""

    def __init__(self) -> None:
        self.answers: list[tuple[str, list[str], list[list[str]]]] = []
        self.posts: list[tuple[str, dict]] = []
        self.sqls: list[str] = []
        self.next_id = 1000000001

    def post(self, path: str, body: dict) -> dict:
        self.posts.append((path, body))
        doc_id = self.next_id
        self.next_id += 1
        return {"ok": True, "type": body.get("type"), "id": doc_id}

    def sql(self, query: str) -> RemoteResult:
        self.sqls.append(query)
        for part, header, rows in self.answers:
            if part in query:
                return RemoteResult(0, table(header, rows))
        return RemoteResult(0, table(["id"], []))

    def link(self) -> Link:
        return Link(self.post, self.sql, lambda: None, "op001", DAY, ["secret-x"])


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.server = FakeServer()
        self.ctx = SeedContext(Path(self.tmp.name), self.server.link())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def paths(self) -> list[str]:
        return [path for path, _body in self.server.posts]


def _step(name: str):
    return next(step for step in ALL_STEPS if step.name == name)


class BodyTest(unittest.TestCase):
    def test_apwo_chain_bodies(self) -> None:
        po = more.wo_po_body(DAY)
        self.assertEqual(po["head"]["cMemo"], "seed:apwo_po")
        self.assertEqual(po["head"]["cVenCode"], "S0001")
        self.assertEqual([line["iQuantity"] for line in po["lines"]], [2])
        inbound = more.wo_in_body(11, 12)
        self.assertEqual(inbound["lines"], [{"source_line_id": 12, "quantity": 2}])
        self.assertEqual(inbound["head"]["cmemo"], "seed:apwo_in")
        invoice = more.wo_invoice_body(13, 14)
        self.assertEqual(invoice["head"], {"cPBVCode": "SEEDINV003", "cPBVBillType": "01", "cPBVMemo": "seed:apwo_invoice"})
        self.assertEqual(invoice["lines"], [{"source_line_id": 14, "quantity": 2}])

    def test_prepay_bodies(self) -> None:
        ar = more.prepay_body("ar_prepay", DAY, "112201")
        self.assertEqual((ar["type"], ar["head"]["cDwCode"], ar["head"]["cDigest"]), ("ar_receipt", "C0001", "seed:ar_prepay"))
        self.assertEqual(ar["lines"], [{"iType": 1, "cKm": "112201", "iAmt": 50}])
        ap = more.prepay_body("ap_prepay", DAY, "220201")
        self.assertEqual((ap["type"], ap["head"]["cDwCode"], ap["head"]["cDigest"]), ("ap_payment", "S0001", "seed:ap_prepay"))
        self.assertEqual(ap["lines"], [{"iType": 1, "cKm": "220201", "iAmt": 50}])

    def test_fx_bodies(self) -> None:
        rate = Decimal("7.1")
        receipt = fx.fx_body("fx_ar_receipt", DAY, ("112201", ""), rate)
        self.assertEqual(receipt["type"], "ar_receipt")
        self.assertEqual(receipt["lines"], [{"iType": 0, "cKm": "112201", "iAmt_f": 20}])
        bill = fx.fx_body("fx_ap_bill", DAY, ("220201", "6401"), rate)
        self.assertEqual((bill["type"], bill["head"]["cDwCode"], bill["head"]["cCode"]), ("ap_bill", "S0001", "220201"))
        self.assertEqual(bill["lines"], [{"cCode": "6401", "iAmount_f": 20}])
        for body, key in ((receipt, "fx_ar_receipt"), (bill, "fx_ap_bill")):
            head = body["head"]
            self.assertEqual((head["cexch_name"], head["iExchRate"], head["cDigest"]), ("美元", 7.1, "seed:" + key))

    def test_rate_code_and_sql(self) -> None:
        self.assertEqual(fx.rate_code(DAY), "美元:2026:10")
        self.assertEqual(fx.rate_body(DAY), {"archive": "exchange_rate", "code": "美元:2026:10", "fields": {"rate": 7.1}})
        query = fx.rate_sql(DAY)
        self.assertIn("iYear = 2026 AND iperiod = 10 AND itype = 2", query)
        self.assertEqual(sql_block_reason("SET NOCOUNT ON;\n" + query), "")
        with self.assertRaises(SeedError):
            fx.rate_sql("2026-13-01")


class RateStepTest(Case):
    def test_missing_rate_is_created_then_read_back(self) -> None:
        reads = iter([[], [], [["7.100000"]]])
        plain = self.server.sql
        self.ctx.link.sql = lambda q: RemoteResult(0, table(["rate"], next(reads))) if "FROM exch" in q else plain(q)
        self.assertEqual(_step("fx_rate").probe(self.ctx).missing, ("exchange_rate:美元:2026:10",))
        self.assertEqual(_step("fx_rate").apply(self.ctx), (1, 0))
        self.assertEqual(self.server.posts, [("v1/archives/create", fx.rate_body(DAY))])

    def test_existing_rate_is_kept(self) -> None:
        self.server.answers.append(("FROM exch", ["rate"], [["6.500000"]]))
        self.assertEqual(_step("fx_rate").apply(self.ctx), (0, 1))
        self.assertEqual(self.server.posts, [])
        self.assertEqual(fx.need_rate(self.ctx), Decimal("6.5000"))

    def test_docs_need_the_rate(self) -> None:
        self.server.answers.append(("FROM code", ["code"], [["112201"]]))
        with self.assertRaises(SeedError) as caught:
            _step("fx_ar_receipt").apply(self.ctx)
        self.assertIn("fx_rate", str(caught.exception))
        self.assertEqual(self.server.posts, [])


class DocStepTest(Case):
    def test_fx_bill_created_with_the_read_rate_and_verified(self) -> None:
        self.server.answers += [
            ("FROM exch", ["rate"], [["6.500000"]]),
            ("FROM code", ["code"], [["112201"]]),
            ("COUNT(*) AS n", ["n"], [["0"]]),
        ]
        self.assertEqual(_step("fx_ar_bill").apply(self.ctx), (1, 0))
        self.assertEqual(self.paths(), ["v1/vouchers/create", "v1/vouchers/verify"])
        created = self.server.posts[0][1]
        self.assertEqual((created["type"], created["head"]["iExchRate"]), ("ar_bill", 6.5))
        self.assertEqual(self.server.posts[1][1], {"type": "ar_bill", "id": 1000000001, "action": "verify"})
        self.assertTrue(any("FROM Ap_Vouch WHERE Auto_ID = 1000000001" in q for q in self.server.sqls))

    def test_marked_prepay_reused_and_not_verified_again(self) -> None:
        self.server.answers += [("N'seed:ap_prepay'", ["id"], [["1000000077"]]), ("COUNT(*) AS n", ["n"], [["1"]])]
        self.assertEqual(_step("ap_prepay").apply(self.ctx), (0, 1))
        self.assertEqual(self.server.posts, [])
        self.assertTrue(any("cFlag = N'AP' AND cVouchType = N'49'" in q for q in self.server.sqls))

    def test_apwo_invoice_reviewed_then_ap_audited(self) -> None:
        self.server.answers += [
            ("N'SEEDINV003'", ["id"], [["1000000055"]]),
            ("cPBVVerifier", ["checker", "verifier"], [["", ""]]),
        ]
        self.assertEqual(_step("apwo_invoice").apply(self.ctx), (0, 1))
        actions = [body["action"] for _path, body in self.server.posts]
        self.assertEqual(actions, ["verify", "arap_verify"])
        self.assertEqual({body["type"] for _path, body in self.server.posts}, {"purchase_invoice"})

    def test_audited_invoice_not_touched(self) -> None:
        self.server.answers += [
            ("N'SEEDINV003'", ["id"], [["1000000055"]]),
            ("cPBVVerifier", ["checker", "verifier"], [["op001", "op001"]]),
        ]
        self.assertEqual(_step("apwo_invoice").apply(self.ctx), (0, 1))
        self.assertEqual(self.server.posts, [])

    def test_inbound_needs_its_order(self) -> None:
        with self.assertRaises(SeedError) as caught:
            _step("apwo_in").apply(self.ctx)
        self.assertIn("apwo_po", str(caught.exception))

    def test_lookups_pass_guard(self) -> None:
        self.server.answers.append(("FROM exch", ["rate"], [["7.100000"]]))
        for name in ("apwo_po", "apwo_in", "apwo_invoice", "ar_prepay", "ap_prepay", "fx_rate", "fx_ar_receipt", "fx_ap_bill"):
            _step(name).probe(self.ctx)
        for query in self.server.sqls:
            self.assertEqual(sql_block_reason("SET NOCOUNT ON;\n" + query), "", query)


class OrderTest(unittest.TestCase):
    def test_new_steps_follow_the_old_ones(self) -> None:
        names = [step.name for step in ALL_STEPS]
        docs = [step.name for step in DOC_STEPS]
        added = ["apwo_po", "apwo_in", "apwo_invoice", "ar_prepay", "ap_prepay", "fx_rate", "fx_ar_receipt", "fx_ar_bill",
                 "fx_ap_payment", "fx_ap_bill"]
        self.assertEqual(docs[-len(added):], added)
        self.assertLess(names.index("arwo_invoice"), names.index("apwo_po"))
        self.assertLess(names.index("po_inv_invoice"), names.index("apwo_po"))
        self.assertLess(names.index("currency"), names.index("fx_rate"))
        self.assertEqual(names[-1], "close_prior_months")
        self.assertEqual(len(set(names)), len(names))


if __name__ == "__main__":
    unittest.main()
