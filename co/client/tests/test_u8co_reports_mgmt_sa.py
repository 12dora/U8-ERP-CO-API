"""经营管理报表（业务口径）mgmt/sales、mgmt/arap_terms：路径、正文和本地校验。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _assert_signed, _Running
from co.client.tests.test_u8co_reports import _call, _offline
from co.client.u8co_client import U8CoClient
from co.client.u8co_reports_mgmt_sa import MgmtArapQuery, MgmtSalesQuery


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

    def test_sales_minimal_and_full(self) -> None:
        path, sent = self._send(lambda c: c.report_mgmt_sales(_call(), MgmtSalesQuery(1, 8)))
        self.assertEqual(path, "/u8co/v1/reports/mgmt/sales")
        self.assertEqual(list(sent), _AUTH + ["period_from", "period_to"])
        full = MgmtSalesQuery(2, 3, 2026, ("period", "inventory"), 500, True)
        _path, sent = self._send(lambda c: c.report_mgmt_sales(_call(), full))
        self.assertEqual(
            list(sent), _AUTH + ["fiscal_year", "period_from", "period_to", "group_by", "top", "include_unverified"]
        )
        self.assertEqual(sent["group_by"], ["period", "inventory"])
        _path, sent = self._send(lambda c: c.report_mgmt_sales(_call(), MgmtSalesQuery(1, 1, group_by=())))
        self.assertEqual(sent["group_by"], [])

    def test_arap_terms(self) -> None:
        path, sent = self._send(lambda c: c.report_mgmt_arap_terms(_call(), MgmtArapQuery("ar")))
        self.assertEqual(path, "/u8co/v1/reports/mgmt/arap_terms")
        self.assertEqual(list(sent), _AUTH + ["side"])
        full = MgmtArapQuery("ap", "2026-08-31", ("2202",), (30, 90), 30, 50)
        _path, sent = self._send(lambda c: c.report_mgmt_arap_terms(_call(), full))
        self.assertEqual(
            list(sent), _AUTH + ["side", "as_of", "accounts", "buckets", "default_credit_days", "top"]
        )
        self.assertEqual(sent["buckets"], [30, 90])


class ValidationTests(unittest.TestCase):
    def _refused(self, invoke: Callable[[U8CoClient], object], text: str) -> None:
        with self.assertRaises(ValueError) as caught:
            invoke(_offline())
        self.assertIn(text, str(caught.exception))

    def test_sales_rules(self) -> None:
        self._refused(lambda c: c.report_mgmt_sales(_call(), MgmtSalesQuery(5, 4)), "period_from")
        self._refused(lambda c: c.report_mgmt_sales(_call(), MgmtSalesQuery(1, 2, group_by=("warehouse",))), "group_by")
        dup = MgmtSalesQuery(1, 2, group_by=("customer", "customer"))
        self._refused(lambda c: c.report_mgmt_sales(_call(), dup), "group_by")
        self._refused(lambda c: c.report_mgmt_sales(_call(), MgmtSalesQuery(1, 2, top=501)), "top")
        bad_flag = MgmtSalesQuery(1, 2, include_unverified=1)  # type: ignore[arg-type]
        self._refused(lambda c: c.report_mgmt_sales(_call(), bad_flag), "布尔")

    def test_arap_rules(self) -> None:
        self._refused(lambda c: c.report_mgmt_arap_terms(_call(), MgmtArapQuery("both")), "side")
        self._refused(lambda c: c.report_mgmt_arap_terms(_call(), MgmtArapQuery("ar", "2026/08/31")), "as_of")
        self._refused(lambda c: c.report_mgmt_arap_terms(_call(), MgmtArapQuery("ar", buckets=(60, 30))), "buckets")
        days = MgmtArapQuery("ar", default_credit_days=3651)
        self._refused(lambda c: c.report_mgmt_arap_terms(_call(), days), "default_credit_days")
        self._refused(lambda c: c.report_mgmt_arap_terms(_call(), MgmtArapQuery("ar", top=0)), "top")


if __name__ == "__main__":
    unittest.main()
