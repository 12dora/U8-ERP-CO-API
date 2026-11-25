"""采购入库参照到货单用例（pu_arrival_in）的登记、标签、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import pu_arrival_in as ai
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.pu_arrival_in_sql import arr_valid_sql, pu_arr_in_samples, rd_link_sql
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
    """按 SQL 片段回答挑单与回读；valid / link 是逐次弹出的到货行累计入库、入库单关联。"""

    def __init__(self, valid: list[str], link: list[str]) -> None:
        self.valid = list(valid)
        self.link = list(link)
        self.pick = "ROW:01|V01|D01|INV-1|01\n"
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(fields)))
        doc = 700 + len(self.calls)
        if route.endswith("/generate") and fields.get("type") == "purchase_in":
            if fields.get("lines") and fields["lines"][0]["quantity"] == 3:
                return 409, {"ok": False, "code": "state_mismatch", "message": "超过可生单数量"}
            return 200, {"ok": True, "id": doc, "source_type": fields.get("source_type")}
        if route.endswith("/create") or route.endswith("/generate"):
            return 200, {"ok": True, "id": doc}
        return 200, {"ok": True}

    def sql(self, query: str) -> str:
        if "s.fValidInQuan" in query:
            return self.valid.pop(0)
        if "b.iArrsId" in query:
            return self.link.pop(0)
        answers = (
            ("FROM dbo.Warehouse AS w", self.pick),
            ("bPTHavePO", "ROW:1\n"),
            ("FROM dbo.PO_Podetails AS d", "ROW:801\n"),
            ("FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID =", "ROW:901\n"),
        )
        for needle, answer in answers:
            if needle in query:
                return answer
        raise AssertionError("没有准备回答：" + query[:80])


def _env() -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=object(), operator=operator, year="2026", today=_TODAY, bag={})


def _run(client: _Replay) -> tuple[str, str]:
    def invoke(_client: object, route: str, fields: dict) -> tuple[int, dict]:
        return client.call(route, fields)

    def sql(_env: object, query: str, _label: str) -> str:
        return client.sql(query)

    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(sp, "invoke", invoke):
        return ai.PU_ARR_IN_OPS["pu_arr_in"](_env(), _SPEC, _STEP)  # type: ignore[arg-type]


def _routes(client: _Replay) -> list[str]:
    return [f"{route.rsplit('/', 1)[1]}:{fields.get('type')}" for route, fields in client.calls]


_VALID = ["ROW:0.0000\n", "ROW:1.0000\n", "ROW:0.0000\n", "ROW:2.0000\n", "ROW:0.0000\n"]
_LINK = ["ROW:采购到货单|703|1|1.0000\n", "ROW:采购到货单|703|1|2.0000\n"]


class RegistrationTests(unittest.TestCase):
    def test_registered(self) -> None:
        (spec,) = ai.pu_arr_in_cases()
        self.assertEqual(known_names().count(spec.name), 1)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertTrue(spec.optional and spec.routes)
        tags = auto_tags(spec)
        self.assertTrue({"new:purchase_order", "new:arrival", "new:purchase_return", "new:purchase_in", "stock"} <= tags)

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in pu_arr_in_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("s.Autoid = 77", arr_valid_sql(77))
        self.assertIn("b.iArrsId = 88", rd_link_sql(77, 88))
        with self.assertRaises(ValueError):
            rd_link_sql(0, 1)


class FlowTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        client = _Replay(_VALID, _LINK)
        status, detail = _run(client)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_routes(client), [
            "create:purchase_order", "verify:purchase_order", "generate:arrival", "verify:arrival",
            "generate:purchase_in", "generate:purchase_in", "verify:purchase_in", "verify:purchase_in",
            "delete:purchase_in", "generate:purchase_in", "delete:purchase_in",
            "verify:arrival", "delete:arrival", "verify:purchase_order", "delete:purchase_order",
        ])
        over, part, whole = client.calls[4][1], client.calls[5][1], client.calls[9][1]
        self.assertEqual((over["id"], over["source_type"], over["lines"]), (703, "arrival", [{"source_line_id": 901, "quantity": 3}]))
        self.assertEqual(part["lines"], [{"source_line_id": 901, "quantity": 1}])
        self.assertEqual(part["head"], {"cWhCode": "01"})
        self.assertNotIn("lines", whole)
        self.assertEqual([fields.get("action") for _route, fields in client.calls[6:8]], ["verify", "unverify"])

    def test_writeback_not_restored_fails_and_cleans_up(self) -> None:
        client = _Replay(["ROW:0.0000\n", "ROW:1.0000\n", "ROW:1.0000\n"], _LINK[:1])
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("删除入库单后", detail)
        self.assertEqual(_routes(client)[-4:], ["verify:arrival", "delete:arrival", "verify:purchase_order", "delete:purchase_order"])

    def test_wrong_link_fails(self) -> None:
        client = _Replay(["ROW:0.0000\n"], ["ROW:采购订单|0|0|0.0000\n"])
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("来源|到货单|行数|数量", detail)
        self.assertEqual(_routes(client)[-6:-4], ["generate:purchase_in", "delete:purchase_in"])

    def test_no_pick_is_na_without_writes(self) -> None:
        client = _Replay([], [])
        client.pick = "\n"
        status, _detail = _run(client)
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
