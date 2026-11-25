"""采购手工结算用例（pu_settle_manual）的登记、标签、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import pu_settle_manual as sm
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.pu_settle_manual_sql import prior_open_sql, pu_settle_man_samples, safe_day, settle_state_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_TODAY = "2026-10-03"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    """按 SQL 片段回答挑单与回读；rd / bill 是逐次弹出的入库行、发票行状态。"""

    def __init__(self, rd: list[str], bill: list[str], settle: str = "ROW:1|1.0000|1.00|0|0\n") -> None:
        self.rd = list(rd)
        self.bill = list(bill)
        self.settle = settle
        self.pick = "ROW:01|V01|D01|INV-1|01\n"
        self.prior = "ROW:0\n"
        self.havepo = "ROW:0\n"
        self.prices = ["ROW:2.00|1.000000\n"] * 3
        self.reply: tuple[int, dict] | None = None
        self.calls: list[tuple[str, dict]] = []
        self.over = (409, {"ok": False, "code": "state_mismatch", "message": "结算数量超过入库行的未结算数量"})

    def call(self, route: str, fields: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(fields)))
        kind = fields.get("type")
        if route.endswith("/create") and kind == "purchase_settle":
            if self.reply is not None:
                return self.reply
            if fields.get("dry_run"):
                return 200, {"ok": True, "dry_run": True}
            if fields["lines"][0].get("quantity") == 2:
                return self.over
            return 200, {"ok": True, "id": 2773, "lines": len(fields["lines"]), "settle_date": _TODAY}
        if route.endswith("/create") or route.endswith("/generate"):
            return 200, {"ok": True, "id": 700 + len(self.calls)}
        return 200, {"ok": True}

    def sql(self, query: str) -> str:
        answers = (
            ("FROM dbo.Warehouse AS w", self.pick),
            ("bPTHavePO", self.havepo),
            ("FROM dbo.GL_mend AS g", self.prior),
            ("FROM dbo.rdrecords01 AS b WHERE b.ID =", "ROW:501\n"),
            ("FROM dbo.PO_Podetails AS d", "ROW:801\n"),
            ("FROM dbo.PU_ArrivalVouchs AS s", "ROW:901\n"),
            ("FROM dbo.PurBillVouchs AS b WHERE b.PBVID =", "ROW:601\n"),
            ("FROM dbo.PurSettleVouchs AS s WHERE s.PSVID =", self.settle),
        )
        for needle, answer in answers:
            if needle in query:
                return answer
        if "b.iUnitCost" in query:
            return self.prices.pop(0)
        if "WHERE b.AutoID = " in query:
            return self.rd.pop(0)
        if "JOIN dbo.PurBillVouch AS h" in query:
            return self.bill.pop(0)
        raise AssertionError("没有准备回答：" + query[:80])


def _env() -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=object(), operator=operator, year="2026", today=_TODAY, bag={})


def _run(client: _Replay, op: str = "settle_man_partial") -> tuple[str, str]:
    def invoke(_client: object, route: str, fields: dict) -> tuple[int, dict]:
        return client.call(route, fields)

    def sql(_env: object, query: str, _label: str) -> str:
        return client.sql(query)

    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(sp, "invoke", invoke):
        return sm.PU_SETTLE_MAN_OPS[op](_env(), _SPEC, _STEP)  # type: ignore[arg-type]


def _routes(client: _Replay) -> list[str]:
    out = []
    for route, fields in client.calls:
        tail = route.rsplit("/", 1)[1]
        out.append(f"{tail}:{fields.get('type')}" + ("?dry" if fields.get("dry_run") else ""))
    return out


class RegistrationTests(unittest.TestCase):
    def test_registered_once(self) -> None:
        specs = sm.pu_settle_man_cases()
        self.assertEqual(tuple(spec.name for spec in specs), sm.CASE_NAMES + (sm.TIER1_CASE,))
        for spec in specs[:3]:
            self.assertEqual(known_names().count(spec.name), 1)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
            self.assertTrue(spec.optional and spec.routes)
            tags = auto_tags(spec)
            self.assertTrue({"new:purchase_settle", "w:purchase_settle", "new:purchase_in", "opening:pu"} <= tags)
            self.assertTrue({"new:purchase_order", "new:arrival", "new:purchase_return"} <= tags)

    def test_tier1_probe_registered(self) -> None:
        probe = sm.pu_settle_man_cases()[3]
        self.assertEqual(known_names().count(probe.name), 1)
        self.assertTrue(ops_run.has_op(probe.steps[0].sql_name))
        self.assertNotIn("new:purchase_settle", auto_tags(probe))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in pu_settle_man_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("s.PSVID = 77", settle_state_sql(77))
        self.assertIn("'2026-09-30'", prior_open_sql("2026-09-30"))
        with self.assertRaises(ValueError):
            safe_day("2026-09-30'; --")


class PartialTests(unittest.TestCase):
    def test_happy_path_settles_part_and_restores(self) -> None:
        client = _Replay(["ROW:0.0000|\n", "ROW:1.0000|2026-10-03\n", "ROW:0.0000|\n"], ["ROW:1.0000||\n", "ROW:0.0000||\n"])
        status, detail = _run(client)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_routes(client), [
            "create:purchase_in", "verify:purchase_in", "generate:purchase_invoice", "verify:purchase_invoice",
            "create:purchase_settle?dry", "create:purchase_settle", "create:purchase_settle",
            "delete:purchase_settle", "verify:purchase_invoice", "delete:purchase_invoice",
            "verify:purchase_in", "delete:purchase_in",
        ])
        settle = client.calls[5][1]
        self.assertEqual(settle["head"], {"settle_date": _TODAY})
        self.assertEqual(settle["lines"], [{"in_line_id": 501, "invoice_line_id": 601, "quantity": 1}])
        self.assertEqual((client.calls[7][1]["id"], client.calls[7][1]["date"]), (2773, _TODAY))

    def test_partial_rewriting_receipt_price_fails(self) -> None:
        client = _Replay(["ROW:0.0000|\n", "ROW:1.0000|2026-10-03\n"], ["ROW:1.0000||\n"])
        client.prices = ["ROW:2.00|1.000000\n", "ROW:1.00|1.000000\n"]
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("部分结算改写了入库行", detail)
        self.assertEqual(_routes(client)[-1], "delete:purchase_in")

    def test_delete_not_restoring_price_fails(self) -> None:
        client = _Replay(["ROW:0.0000|\n", "ROW:1.0000|2026-10-03\n", "ROW:0.0000|\n"], ["ROW:1.0000||\n", "ROW:0.0000||\n"])
        client.prices = ["ROW:2.00|1.000000\n", "ROW:2.00|1.000000\n", "ROW:2.00|0.000000\n"]
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("没有还原", detail)

    def test_havepo_builds_from_order(self) -> None:
        client = _Replay(["ROW:0.0000|\n", "ROW:1.0000|2026-10-03\n", "ROW:0.0000|\n"], ["ROW:1.0000||\n", "ROW:0.0000||\n"])
        client.havepo = "ROW:1\n"
        status, detail = _run(client)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_routes(client)[:5], [
            "create:purchase_order", "verify:purchase_order", "generate:purchase_in", "verify:purchase_in",
            "generate:purchase_invoice",
        ])
        gen = client.calls[2][1]
        self.assertEqual((gen["head"], gen["lines"]), ({"cWhCode": "01"}, [{"source_line_id": 801, "quantity": 2}]))
        self.assertEqual(_routes(client)[-4:], [
            "verify:purchase_in", "delete:purchase_in", "verify:purchase_order", "delete:purchase_order",
        ])

    def test_stamped_partial_invoice_fails_and_cleans_up(self) -> None:
        client = _Replay(["ROW:0.0000|\n", "ROW:1.0000|2026-10-03\n"], ["ROW:1.0000|2026-10-03|2026-10-03\n"])
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("不应写结算日期", detail)
        self.assertEqual(_routes(client)[-5:], [
            "delete:purchase_settle", "verify:purchase_invoice", "delete:purchase_invoice",
            "verify:purchase_in", "delete:purchase_in",
        ])

    def test_prior_open_month_is_na_without_writes(self) -> None:
        client = _Replay([], [])
        client.prior = "ROW:1\n"
        status, _detail = _run(client)
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


class OffsetTests(unittest.TestCase):
    def test_rd_offset(self) -> None:
        rd = ["ROW:1.0000|2026-10-03\n", "ROW:-1.0000|2026-10-03\n", "ROW:0.0000|\n", "ROW:0.0000|\n"]
        client = _Replay(rd, [], settle="ROW:2|0.0000|0.00|2|0\n")
        status, detail = _run(client, "settle_man_rd_offset")
        self.assertEqual(status, "PASS", detail)
        settle = next(fields for route, fields in client.calls if fields.get("type") == "purchase_settle")
        self.assertEqual(settle["lines"], [{"in_line_id": 501, "quantity": 1}, {"in_line_id": 501, "quantity": -1}])

    def test_rd_offset_havepo_red_from_return(self) -> None:
        rd = ["ROW:1.0000|2026-10-03\n", "ROW:-1.0000|2026-10-03\n", "ROW:0.0000|\n", "ROW:0.0000|\n"]
        client = _Replay(rd, [], settle="ROW:2|0.0000|0.00|2|0\n")
        client.havepo = "ROW:1\n"
        status, detail = _run(client, "settle_man_rd_offset")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_routes(client), [
            "create:purchase_order", "verify:purchase_order", "generate:arrival", "verify:arrival",
            "generate:purchase_in", "verify:purchase_in", "generate:purchase_return", "verify:purchase_return",
            "generate:purchase_in", "verify:purchase_in", "create:purchase_settle", "delete:purchase_settle",
            "verify:purchase_in", "delete:purchase_in", "verify:purchase_return", "delete:purchase_return",
            "verify:purchase_in", "delete:purchase_in", "verify:arrival", "delete:arrival",
            "verify:purchase_order", "delete:purchase_order",
        ])
        # 蓝字入库、退货单都参照到货单（703，到货行 901），红字入库参照退货单（707）。
        sources = [fields.get("source_type") for route, fields in client.calls if route.endswith("/generate")]
        self.assertEqual(sources, [None, "arrival", "arrival", "purchase_return"])
        blue, ret, red_in = client.calls[4][1], client.calls[6][1], client.calls[8][1]
        self.assertEqual((blue["id"], blue["lines"]), (703, [{"source_line_id": 901, "quantity": 1}]))
        self.assertEqual((ret["id"], ret["lines"]), (703, [{"source_line_id": 901, "quantity": 1}]))
        self.assertEqual(ret["head"], {"cWhCode": "01", "dDate": _TODAY})
        self.assertEqual((red_in["id"], red_in["lines"]), (707, [{"source_line_id": 901, "quantity": 1}]))

    def test_inv_offset(self) -> None:
        stamped = "|2026-10-03|2026-10-03\n"
        bill = ["ROW:1.0000" + stamped, "ROW:-1.0000" + stamped, "ROW:0.0000||\n", "ROW:0.0000||\n"]
        client = _Replay(["ROW:0.0000|\n", "ROW:0.0000|\n"], bill, settle="ROW:2|0.0000|-0.10|0|2\n")
        status, detail = _run(client, "settle_man_inv_offset")
        self.assertEqual(status, "PASS", detail)
        settle = next(fields for route, fields in client.calls if fields.get("type") == "purchase_settle")
        self.assertEqual([line["quantity"] for line in settle["lines"]], [1, -1])
        self.assertNotIn("in_line_id", settle["lines"][0])


class Tier1Tests(unittest.TestCase):
    """手工结算升为第一级，非测试账套预演到行校验 400 / 409 才 PASS，403 test_account_only FAIL。"""

    def _probe(self, reply: tuple[int, dict]) -> tuple[tuple[str, str], _Replay]:
        client = _Replay([], [])
        client.reply = reply
        return _run(client, "settle_man_tier1"), client

    def test_row_check_passes(self) -> None:
        (status, detail), client = self._probe((400, {"ok": False, "code": "bad_request", "message": "采购入库单行不存在"}))
        self.assertEqual(status, "PASS", detail)
        sent = client.calls[0][1]
        self.assertTrue(sent["dry_run"])
        self.assertEqual(sent["lines"], [{"in_line_id": 2147483647, "invoice_line_id": 2147483647, "quantity": 1}])
        self.assertEqual(len(client.calls), 1)

    def test_conflict_passes(self) -> None:
        (status, _detail), _client = self._probe((409, {"ok": False, "code": "state_mismatch", "message": "行不存在"}))
        self.assertEqual(status, "PASS")

    def test_test_account_gate_left_in_fails(self) -> None:
        text = "采购手工结算只对配置为测试账套的账套开放"
        (status, detail), _client = self._probe((403, {"ok": False, "code": "test_account_only", "message": text}))
        self.assertEqual(status, "FAIL")
        self.assertIn("第一级", detail)

    def test_other_status_fails(self) -> None:
        (status, _detail), _client = self._probe((200, {"ok": True}))
        self.assertEqual(status, "FAIL")


if __name__ == "__main__":
    unittest.main()
