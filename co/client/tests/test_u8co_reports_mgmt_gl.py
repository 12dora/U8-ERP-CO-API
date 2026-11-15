"""经营管理报表（总账口径）mgmt/pnl、mgmt/meta、mgmt/cash_stock：路径、正文和本地校验。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _assert_signed, _Running
from co.client.tests.test_u8co_reports import _call, _offline
from co.client.u8co_client import U8CoClient
from co.client.u8co_reports_mgmt_gl import MgmtCashQuery, MgmtPnlQuery


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

    def test_pnl_minimal_and_full(self) -> None:
        path, sent = self._send(lambda c: c.report_mgmt_pnl(_call(), MgmtPnlQuery(1, 8)))
        self.assertEqual(path, "/u8co/v1/reports/mgmt/pnl")
        self.assertEqual(list(sent), _AUTH + ["period_from", "period_to"])
        full = MgmtPnlQuery(3, 3, 2026, True, "leaf", ("dept", "item"), ("6",), "4103")
        _path, sent = self._send(lambda c: c.report_mgmt_pnl(_call(), full))
        self.assertEqual(
            list(sent),
            _AUTH + ["fiscal_year", "period_from", "period_to", "include_unposted", "detail", "dims", "pl_accounts",
                     "profit_account"],
        )
        self.assertEqual(sent["dims"], ["dept", "item"])
        self.assertIs(sent["include_unposted"], True)

    def test_meta(self) -> None:
        path, sent = self._send(lambda c: c.report_mgmt_meta(_call()))
        self.assertEqual(path, "/u8co/v1/reports/mgmt/meta")
        self.assertEqual(list(sent), _AUTH)
        _path, sent = self._send(lambda c: c.report_mgmt_meta(_call(), 2025))
        self.assertEqual(sent["fiscal_year"], 2025)

    def test_cash_stock(self) -> None:
        query = MgmtCashQuery(8, cash_accounts=("1001", "1002"), top=50, purchase_source="receipt", include_unverified=False)
        path, sent = self._send(lambda c: c.report_mgmt_cash_stock(_call(), query))
        self.assertEqual(path, "/u8co/v1/reports/mgmt/cash_stock")
        self.assertEqual(
            list(sent), _AUTH + ["period", "cash_accounts", "top", "purchase_source", "include_unverified"]
        )
        self.assertIs(sent["include_unverified"], False)


class ValidationTests(unittest.TestCase):
    def _refused(self, invoke: Callable[[U8CoClient], object], text: str) -> None:
        with self.assertRaises(ValueError) as caught:
            invoke(_offline())
        self.assertIn(text, str(caught.exception))

    def test_pnl_rules(self) -> None:
        self._refused(lambda c: c.report_mgmt_pnl(_call(), MgmtPnlQuery(4, 3)), "period_from")
        self._refused(lambda c: c.report_mgmt_pnl(_call(), MgmtPnlQuery(1, 13)), "period_to")
        self._refused(lambda c: c.report_mgmt_pnl(_call(), MgmtPnlQuery(1, 3, detail="grade")), "detail")
        self._refused(lambda c: c.report_mgmt_pnl(_call(), MgmtPnlQuery(1, 3, dims=("person",))), "dims")
        self._refused(lambda c: c.report_mgmt_pnl(_call(), MgmtPnlQuery(1, 3, dims=("dept", "dept"))), "dims")
        self._refused(lambda c: c.report_mgmt_pnl(_call(), MgmtPnlQuery(1, 3, pl_accounts=("6%",))), "pl_accounts")
        self._refused(lambda c: c.report_mgmt_pnl(_call(), MgmtPnlQuery(1, 3, pl_accounts=())), "pl_accounts")
        self._refused(lambda c: c.report_mgmt_pnl(_call(), MgmtPnlQuery(1, 3, include_unposted=1)), "布尔")  # type: ignore[arg-type]

    def test_meta_and_cash_rules(self) -> None:
        self._refused(lambda c: c.report_mgmt_meta(_call(), 1999), "fiscal_year")
        self._refused(lambda c: c.report_mgmt_cash_stock(_call(), MgmtCashQuery(0)), "period")
        self._refused(lambda c: c.report_mgmt_cash_stock(_call(), MgmtCashQuery(1, top=501)), "top")
        self._refused(lambda c: c.report_mgmt_cash_stock(_call(), MgmtCashQuery(1, purchase_source="order")), "purchase_source")
        self._refused(lambda c: c.report_mgmt_cash_stock(_call(), MgmtCashQuery(1, notes_accounts=("11 21",))), "notes_accounts")


if __name__ == "__main__":
    unittest.main()
