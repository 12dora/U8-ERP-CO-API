"""库存与销售支持报表（stock_ledger、stock_summary、position_stock、batch_stock、customer_credit、price_list）：
正文、本地校验和命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _argv, _assert_signed, _run_main, _Running
from co.client.tests.test_u8co_reports import _call, _offline
from co.client.u8co_cli import _parser
from co.client.u8co_cli_reports_stock import stock_ledger_query, stock_place_query, stock_summary_query
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_reports_stock import (
    CustomerCreditQuery,
    PriceListQuery,
    StockLedgerQuery,
    StockPlaceQuery,
    StockSummaryQuery,
)


class WireTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_stock_ledger(self) -> None:
        path, sent = self._send(lambda c: c.report_stock_ledger(_call(), StockLedgerQuery("I1", "2026-09-01")))
        self.assertEqual(path, "/u8co/v1/reports/stock_ledger")
        self.assertEqual(list(sent), _AUTH + ["inv", "date_from"])
        full = StockLedgerQuery("I1", "2026-09-01", "2026-09-28", "01", "B1", True, "opaque", 1000)
        _path, sent = self._send(lambda c: c.report_stock_ledger(_call(), full))
        self.assertEqual(
            list(sent),
            _AUTH + ["inv", "wh", "batch", "date_from", "date_to", "include_unverified", "after", "limit"],
        )

    def test_stock_summary(self) -> None:
        query = StockSummaryQuery("2026-09-01", inv_class="0102", by_wh=False, nonzero=False)
        path, sent = self._send(lambda c: c.report_stock_summary(_call(), query))
        self.assertEqual(path, "/u8co/v1/reports/stock_summary")
        self.assertEqual(list(sent), _AUTH + ["date_from", "inv_class", "by_wh", "nonzero"])
        self.assertIs(sent["by_wh"], False)

    def test_position_and_batch(self) -> None:
        path, sent = self._send(lambda c: c.report_position_stock(_call(), StockPlaceQuery(wh="05", position="5F01")))
        self.assertEqual(path, "/u8co/v1/reports/position_stock")
        self.assertEqual(list(sent), _AUTH + ["wh", "position"])
        query = StockPlaceQuery(inv="I1", expiring_before="2026-12-31", nonzero=False, limit=5)
        path, sent = self._send(lambda c: c.report_batch_stock(_call(), query))
        self.assertEqual(path, "/u8co/v1/reports/batch_stock")
        self.assertEqual(list(sent), _AUTH + ["inv", "expiring_before", "nonzero", "limit"])

    def test_credit_and_price(self) -> None:
        query = CustomerCreditQuery(("C001", "C002"), True, "x", 200)
        path, sent = self._send(lambda c: c.report_customer_credit(_call(), query))
        self.assertEqual(path, "/u8co/v1/reports/customer_credit")
        self.assertEqual(list(sent), _AUTH + ["customer", "controlled_only", "after", "limit"])
        self.assertEqual(sent["customer"], ["C001", "C002"])
        price = PriceListQuery("vendor", vendor="V001", all_dates=True)
        path, sent = self._send(lambda c: c.report_price_list(_call(), price))
        self.assertEqual(path, "/u8co/v1/reports/price_list")
        self.assertEqual(list(sent), _AUTH + ["kind", "vendor", "all_dates"])


class ValidationTests(unittest.TestCase):
    def _refused(self, invoke: Callable[[U8CoClient], object], text: str) -> None:
        with self.assertRaises(ValueError) as caught:
            invoke(_offline())
        self.assertIn(text, str(caught.exception))

    def test_stock_rules(self) -> None:
        self._refused(lambda c: c.report_stock_ledger(_call(), StockLedgerQuery("", "2026-09-01")), "inv")
        self._refused(lambda c: c.report_stock_ledger(_call(), StockLedgerQuery("I1", "2026-9-1")), "date_from")
        late = StockLedgerQuery("I1", "2026-09-02", "2026-09-01")
        self._refused(lambda c: c.report_stock_ledger(_call(), late), "date_from")
        self._refused(lambda c: c.report_stock_ledger(_call(), StockLedgerQuery("I1", "2026-09-01", limit=1001)), "limit")
        self._refused(lambda c: c.report_stock_summary(_call(), StockSummaryQuery("2026-09-01", inv_class="01%")), "inv_class")
        self._refused(lambda c: c.report_stock_summary(_call(), StockSummaryQuery("2026-09-01", by_wh=1)), "布尔")
        self._refused(lambda c: c.report_position_stock(_call(), StockPlaceQuery(expiring_before="2026-12-31")), "expiring")
        self._refused(lambda c: c.report_batch_stock(_call(), StockPlaceQuery(position="5F")), "position")
        self._refused(lambda c: c.report_batch_stock(_call(), StockPlaceQuery(expiring_before="2026-13-01")), "expiring_before")

    def test_sales_rules(self) -> None:
        self._refused(lambda c: c.report_customer_credit(_call(), CustomerCreditQuery(())), "customer")
        self._refused(lambda c: c.report_customer_credit(_call(), CustomerCreditQuery(("C",) * 21)), "customer")
        self._refused(lambda c: c.report_customer_credit(_call(), CustomerCreditQuery(limit=201)), "limit")
        self._refused(lambda c: c.report_price_list(_call(), PriceListQuery("purchase")), "kind")
        self._refused(lambda c: c.report_price_list(_call(), PriceListQuery("inventory", customer="C1")), "customer")
        self._refused(lambda c: c.report_price_list(_call(), PriceListQuery("customer", vendor="V1")), "vendor")
        both = PriceListQuery("vendor", as_of="2026-09-28", all_dates=True)
        self._refused(lambda c: c.report_price_list(_call(), both), "all_dates")


class CliTests(unittest.TestCase):
    def test_parse_stock(self) -> None:
        argv = _argv("report-stock-ledger", "--inv", "I1", "--date-from", "2026-09-01", "--include-unverified")
        self.assertEqual(stock_ledger_query(_parser().parse_args(argv)), StockLedgerQuery("I1", "2026-09-01", include_unverified=True))
        argv = _argv("report-stock-summary", "--date-from", "2026-09-01", "--no-wh", "--include-zero", "--inv-class", "01")
        query = stock_summary_query(_parser().parse_args(argv))
        self.assertEqual((query.by_wh, query.nonzero, query.inv_class), (False, False, "01"))
        place = stock_place_query(_parser().parse_args(_argv("report-batch-stock", "--expiring-before", "2026-12-31")))
        self.assertEqual(place, StockPlaceQuery(expiring_before="2026-12-31"))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-stock-ledger", "--date-from", "2026-09-01"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-price-list", "--kind", "purchase"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-position-stock", "--expiring-before", "2026-12-31"))

    def test_main_wires_stock_reports(self) -> None:
        seen: dict[str, Any] = {}

        def _fake(name: str) -> Callable[..., dict[str, Any]]:
            def invoke(self: U8CoClient, call: U8Call, query: object = None) -> dict[str, Any]:
                seen[name] = query
                return {"ok": True}

            return invoke

        cases = (
            (_argv("report-stock-ledger", "--inv", "I1", "--date-from", "2026-09-01"), "report_stock_ledger",
             StockLedgerQuery("I1", "2026-09-01")),
            (_argv("report-stock-summary", "--date-from", "2026-09-01"), "report_stock_summary",
             StockSummaryQuery("2026-09-01")),
            (_argv("report-position-stock", "--position", "5F"), "report_position_stock", StockPlaceQuery(position="5F")),
            (_argv("report-batch-stock"), "report_batch_stock", StockPlaceQuery()),
            (_argv("report-customer-credit", "--customer", "C1", "--controlled-only"), "report_customer_credit",
             CustomerCreditQuery(("C1",), True)),
            (_argv("report-price-list", "--kind", "customer", "--customer", "C1"), "report_price_list",
             PriceListQuery("customer", customer="C1")),
        )
        for argv, method, want in cases:
            self.assertEqual(_run_main(argv, method, _fake(method)), 0)
            self.assertEqual(seen[method], want)


if __name__ == "__main__":
    unittest.main()
