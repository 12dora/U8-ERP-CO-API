"""期初余额（opening_balance）、账套体检（account_readiness）与附件列表（gl/vouchers/attachments/list、vouchers/attachments/list）：
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
from co.client.u8co_cli_open import opening_query
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_open import OpeningQuery
from co.client.u8co_gl_arc import GlKey


def _doc_call() -> U8Call:
    return U8Call("801", "2026", "op001", _call().password, "2026-09-27", 7)


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

    def test_opening_balance(self) -> None:
        path, sent = self._send(lambda c: c.report_opening_balance(_call(), OpeningQuery("stock")))
        self.assertEqual(path, "/u8co/v1/reports/opening_balance")
        self.assertEqual(list(sent), _AUTH + ["module"])
        query = OpeningQuery("stock", wh="07", inv="I1", batch="B1", nonzero=False, after="x", limit=1000)
        _path, sent = self._send(lambda c: c.report_opening_balance(_call(), query))
        self.assertEqual(list(sent), _AUTH + ["module", "wh", "inv", "batch", "nonzero", "after", "limit"])
        arap = OpeningQuery("arap", side="ap", partner="V001", code_prefix="2202")
        _path, sent = self._send(lambda c: c.report_opening_balance(_call(), arap))
        self.assertEqual(list(sent), _AUTH + ["module", "side", "partner", "code_prefix"])
        gl = OpeningQuery("gl", fiscal_year=2024, code_prefix="1122", leaf_only=True, dim="customer")
        _path, sent = self._send(lambda c: c.report_opening_balance(_call(), gl))
        self.assertEqual(list(sent), _AUTH + ["module", "fiscal_year", "code_prefix", "leaf_only", "dim"])

    def test_account_readiness(self) -> None:
        path, sent = self._send(lambda c: c.report_account_readiness(_call()))
        self.assertEqual(path, "/u8co/v1/reports/account_readiness")
        self.assertEqual(list(sent), _AUTH)
        _path, sent = self._send(lambda c: c.report_account_readiness(_call(), "2026-03-31"))
        self.assertEqual(list(sent), _AUTH + ["as_of"])
        self.assertEqual(sent["as_of"], "2026-03-31")

    def test_attachments(self) -> None:
        path, sent = self._send(lambda c: c.gl_attachments(_call(), GlKey(9, "转", 3)))
        self.assertEqual(path, "/u8co/v1/gl/vouchers/attachments/list")
        self.assertEqual(list(sent), _AUTH + ["period", "sign", "no"])
        path, sent = self._send(lambda c: c.voucher_attachments(_doc_call(), "purchase_order"))
        self.assertEqual(path, "/u8co/v1/vouchers/attachments/list")
        self.assertEqual((sent["type"], sent["id"]), ("purchase_order", 7))


class ValidationTests(unittest.TestCase):
    def _refused(self, invoke: Callable[[U8CoClient], object], text: str) -> None:
        with self.assertRaises(ValueError) as caught:
            invoke(_offline())
        self.assertIn(text, str(caught.exception))

    def test_opening_rules(self) -> None:
        cases = (
            (OpeningQuery("ia"), "module"),
            (OpeningQuery("arap"), "side"),
            (OpeningQuery("stock", side="ar"), "side"),
            (OpeningQuery("stock", fiscal_year=2024), "fiscal_year"),
            (OpeningQuery("gl", partner="C1"), "partner"),
            (OpeningQuery("arap", side="ar", wh="01"), "wh"),
            (OpeningQuery("gl", dim="item"), "dim"),
            (OpeningQuery("gl", code_prefix="11%"), "code_prefix"),
            (OpeningQuery("gl", fiscal_year=1999), "fiscal_year"),
            (OpeningQuery("stock", limit=1001), "limit"),
            (OpeningQuery("stock", nonzero=1), "布尔"),  # type: ignore[arg-type]
        )
        for query, text in cases:
            self._refused(lambda c, q=query: c.report_opening_balance(_call(), q), text)

    def test_readiness_rules(self) -> None:
        self._refused(lambda c: c.report_account_readiness(_call(), "2026-02-30"), "as_of")
        self._refused(lambda c: c.report_account_readiness(_call(), "20260331"), "as_of")

    def test_attachment_rules(self) -> None:
        self._refused(lambda c: c.gl_attachments(_call(), GlKey(13, "转", 1)), "period")
        self._refused(lambda c: c.voucher_attachments(_call(), "purchase_order"), "id")
        self._refused(lambda c: c.voucher_attachments(_doc_call(), "nope"), "类型")


class CliTests(unittest.TestCase):
    def test_parse_opening(self) -> None:
        argv = _argv("report-opening-balance", "--module", "gl", "--dim", "vendor", "--leaf-only", "--include-zero")
        query = opening_query(_parser().parse_args(argv))
        self.assertEqual(query, OpeningQuery("gl", dim="vendor", leaf_only=True, nonzero=False))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-opening-balance", "--module", "ia"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("attachments", "--type", "sale_order"))

    def test_main_wires_opening_and_attachments(self) -> None:
        seen: dict[str, Any] = {}

        def _fake(name: str) -> Callable[..., dict[str, Any]]:
            def invoke(self: U8CoClient, call: U8Call, query: object = None) -> dict[str, Any]:
                seen[name] = (call.doc_id, query)
                return {"ok": True}

            return invoke

        cases = (
            (_argv("report-opening-balance", "--module", "arap", "--side", "ar"), "report_opening_balance",
             (None, OpeningQuery("arap", side="ar"))),
            (_argv("gl-attachments", "--period", "9", "--sign", "转", "--no", "3"), "gl_attachments",
             (None, GlKey(9, "转", 3))),
            (_argv("attachments", "--type", "sale_order", "--id", "5"), "voucher_attachments", (5, "sale_order")),
            (_argv("report-account-readiness", "--as-of", "2026-03-31"), "report_account_readiness",
             (None, "2026-03-31")),
        )
        for argv, method, want in cases:
            self.assertEqual(_run_main(argv, method, _fake(method)), 0)
            self.assertEqual(seen[method], want)


if __name__ == "__main__":
    unittest.main()
