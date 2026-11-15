"""固定资产报表（fa_changes、fa_depreciation）与只读单据类型（ia_adjust、inventory_price_adjust）：正文和本地校验。
只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _assert_signed, _Running
from co.client.tests.test_u8co_reports import _call, _offline
from co.client.u8co_client import U8CoClient
from co.client.u8co_kinds import (
    ARAP_KINDS,
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    KIND_NAMES,
    UPDATABLE_KINDS,
    VERIFIABLE_KINDS,
)
from co.client.u8co_reports_fa import FaChangesQuery, FaDeprQuery

_READ_ONLY = ("ia_adjust", "inventory_price_adjust")


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

    def test_fa_changes(self) -> None:
        path, sent = self._send(lambda c: c.report_fa_changes(_call(), FaChangesQuery()))
        self.assertEqual(path, "/u8co/v1/reports/fa_changes")
        self.assertEqual(list(sent), _AUTH)
        full = FaChangesQuery(2026, 4, "00021", "00005", 1, "opaque", 50)
        _path, sent = self._send(lambda c: c.report_fa_changes(_call(), full))
        self.assertEqual(
            list(sent), _AUTH + ["fiscal_year", "period", "card", "code", "change_type", "after", "limit"]
        )

    def test_fa_depreciation(self) -> None:
        query = FaDeprQuery(period=8, card="00021", nonzero=False, limit=1000)
        path, sent = self._send(lambda c: c.report_fa_depreciation(_call(), query))
        self.assertEqual(path, "/u8co/v1/reports/fa_depreciation")
        self.assertEqual(list(sent), _AUTH + ["period", "card", "nonzero", "limit"])
        self.assertIs(sent["nonzero"], False)


class ValidationTests(unittest.TestCase):
    def _refused(self, invoke: Callable[[U8CoClient], object], text: str) -> None:
        with self.assertRaises(ValueError) as caught:
            invoke(_offline())
        self.assertIn(text, str(caught.exception))

    def test_fa_rules(self) -> None:
        self._refused(lambda c: c.report_fa_changes(_call(), FaChangesQuery(period=13)), "period")
        self._refused(lambda c: c.report_fa_changes(_call(), FaChangesQuery(fiscal_year=1999)), "fiscal_year")
        self._refused(lambda c: c.report_fa_changes(_call(), FaChangesQuery(card="a%")), "card")
        self._refused(lambda c: c.report_fa_changes(_call(), FaChangesQuery(code="12345678901")), "code")
        self._refused(lambda c: c.report_fa_changes(_call(), FaChangesQuery(change_type=0)), "change_type")
        self._refused(lambda c: c.report_fa_depreciation(_call(), FaDeprQuery(nonzero=1)), "布尔")  # type: ignore[arg-type]
        self._refused(lambda c: c.report_fa_depreciation(_call(), FaDeprQuery(limit=1001)), "limit")


class KindTests(unittest.TestCase):
    def test_read_only_kinds_listed_but_not_writable(self) -> None:
        self.assertEqual(KIND_NAMES[-len(ARAP_KINDS):], ARAP_KINDS)
        for name in _READ_ONLY:
            self.assertIn(name, KIND_NAMES)
            for group in (CREATABLE_KINDS, DELETABLE_KINDS, UPDATABLE_KINDS, VERIFIABLE_KINDS):
                self.assertNotIn(name, group)


if __name__ == "__main__":
    unittest.main()
