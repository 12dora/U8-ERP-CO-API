"""库存与销售支持报表用例（report_stock）的登记和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.report_stock import (
    batch_stock,
    customer_credit,
    position_stock,
    price_list,
    stock_rpt_cases,
    stock_ledger,
    stock_summary,
)
from co.e2e.tests import support

_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_CUR = "/v1/stock/current"
_SUM = "/v1/reports/stock_summary"
_LED = "/v1/reports/stock_ledger"
_NAMES = (
    "report-stock-summary", "report-stock-ledger", "report-position-stock", "report-batch-stock",
    "report-customer-credit", "report-price-list",
)


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag={})


def _page(rows: list[dict], **extra: object) -> dict:
    body = {"ok": True, "items": rows, "next": None}
    body.update(extra)
    return body


_PICK = _page([{"inv_code": "I1", "wh_code": "01", "qty": 30.0}])
_STOCK = _page([{"qty": 20.0}, {"qty": 10.0}])
_SUMMARY = _page([{"inv_code": "I1", "wh_code": "01", "opening": 25.0, "in_qty": 10.0, "out_qty": 5.0, "closing": 30.0}])


def _ledger(rows: list[dict], carry: float, nxt: object, closing: object = None) -> dict:
    return _page(rows, inv="I1", opening=25.0, carry=carry, closing=closing, next=nxt)


class StockCaseTests(unittest.TestCase):
    def test_cases_registered_with_ops(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in stock_rpt_cases()), _NAMES)
        for spec in stock_rpt_cases():
            self.assertEqual(names.count(spec.name), 1, spec.name)
            self.assertTrue(spec.requires_login)
            self.assertTrue(all(ops_run.has_op(step.sql_name) for step in spec.steps))

    def test_summary_matches_current_stock(self) -> None:
        client = _Replay({_CUR: [_PICK, _STOCK], _SUM: [_SUMMARY]})
        self.assertEqual(stock_summary(_env(client), _SPEC, _STEP)[0], "PASS")
        sent = client.calls[1][1]
        self.assertEqual((sent["inv"], sent["wh"], sent["date_from"]), ("I1", "01", "2026-01-01"))
        wrong = _Replay({_CUR: [_PICK, _page([{"qty": 29.0}])], _SUM: [_SUMMARY]})
        self.assertIn("现存量", stock_summary(_env(wrong), _SPEC, _STEP)[1])
        bad = _page([dict(_SUMMARY["items"][0], in_qty=11.0)])
        self.assertIn("期初", stock_summary(_env(_Replay({_CUR: [_PICK], _SUM: [bad]})), _SPEC, _STEP)[1])
        self.assertEqual(stock_summary(_env(_Replay({_CUR: [_page([])]})), _SPEC, _STEP)[0], "N/A")

    def test_ledger_pages_are_continuous(self) -> None:
        first = [{"in_qty": 10.0, "out_qty": 0.0, "balance": 35.0}]
        second = [{"in_qty": 0.0, "out_qty": 5.0, "balance": 30.0}]
        pages = [_ledger(first, 25.0, "tok"), _ledger(second, 35.0, None, 30.0)]
        client = _Replay({_CUR: [_PICK], _SUM: [_SUMMARY], _LED: pages})
        self.assertEqual(stock_ledger(_env(client), _SPEC, _STEP)[0], "PASS")
        self.assertEqual(client.calls[-1][1]["after"], "tok")
        cases = (
            ([_ledger(first, 24.0, None, 34.0)], "期初"),
            ([_ledger([dict(first[0], balance=36.0)], 25.0, None, 36.0)], "逐行"),
            ([_ledger(first, 25.0, None, 99.0)], "closing"),
            ([_ledger(first, 25.0, "t"), _ledger(second, 34.0, None, 29.0)], "carry"),
        )
        for answers, text in cases:
            client = _Replay({_CUR: [_PICK], _SUM: [_SUMMARY], _LED: answers})
            status, detail = stock_ledger(_env(client), _SPEC, _STEP)
            self.assertEqual(status, "FAIL")
            self.assertIn(text, detail)

    def test_position_and_batch(self) -> None:
        pos = _Replay({"/v1/reports/position_stock": [_page([{"position": "5F01", "qty": 2.0}])]})
        self.assertEqual(position_stock(_env(pos), _SPEC, _STEP)[0], "PASS")
        zero = _Replay({"/v1/reports/position_stock": [_page([{"position": "5F01", "qty": 0.0}])]})
        self.assertEqual(position_stock(_env(zero), _SPEC, _STEP)[0], "FAIL")
        row = {"wh_code": "01", "inv_code": "I1", "batch": "B1", "qty": 30.0}
        client = _Replay({"/v1/reports/batch_stock": [_page([row])], _CUR: [_STOCK]})
        self.assertEqual(batch_stock(_env(client), _SPEC, _STEP)[0], "PASS")
        self.assertEqual(client.calls[1][1]["batch"], "B1")
        client = _Replay({"/v1/reports/batch_stock": [_page([row])], _CUR: [_page([{"qty": 1.0}])]})
        self.assertEqual(batch_stock(_env(client), _SPEC, _STEP)[0], "FAIL")

    def test_credit_used_follows_formula(self) -> None:
        formula = {"order": True, "dispatch": True, "invoice": False, "ar": True, "expense": False}
        row = {"order": 1.0, "dispatch": 2.0, "invoice": 4.0, "ar": 8.0, "expense": 16.0, "used": 11.0,
               "controlled": False, "available": None}
        payload = _page([row], formula=formula, check_point="save", ar_enabled=True)
        client = _Replay({"/v1/reports/customer_credit": [payload]})
        self.assertEqual(customer_credit(_env(client), _SPEC, _STEP)[0], "PASS")
        off = dict(payload, ar_enabled=False)
        self.assertIn("used", customer_credit(_env(_Replay({"/v1/reports/customer_credit": [off]})), _SPEC, _STEP)[1])

    def test_price_list_kinds(self) -> None:
        route = "/v1/reports/price_list"
        empty = [_page([], kind=kind, as_of="2026-09-28") for kind in ("customer", "inventory", "vendor")]
        client = _Replay({route: empty})
        self.assertEqual(price_list(_env(client), _SPEC, _STEP)[0], "N/A")
        self.assertEqual([call[1]["kind"] for call in client.calls], ["customer", "inventory", "vendor"])
        row = {"id": 1, "inv_code": "I1", "start_date": "2026-01-01", "end_date": None, "tax_included": True}
        full = [_page([row], kind=kind, as_of="2026-09-28") for kind in ("customer", "inventory", "vendor")]
        self.assertEqual(price_list(_env(_Replay({route: full})), _SPEC, _STEP)[0], "PASS")
        wrong = [_page([], kind="inventory", as_of="2026-09-28")]
        self.assertEqual(price_list(_env(_Replay({route: wrong})), _SPEC, _STEP)[0], "FAIL")


if __name__ == "__main__":
    unittest.main()
