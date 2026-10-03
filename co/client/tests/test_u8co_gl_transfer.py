"""期间损益结转、自定义转账（/v1/gl/transfer/pnl、/v1/gl/transfer/custom）的请求正文、预演、幂等键和命令行解析。
只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import _AUTH, SECRET, _argv, _assert_signed, make_call, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_client import U8CoClient
from co.client.u8co_gl_transfer import GlTransferAsk
from co.client.u8co_idem import WRITE_ROUTES


class GlTransferClientTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[dict[str, Any], dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        return sent, captured

    def test_pnl_wire_body_minimal(self) -> None:
        sent, captured = self._send(lambda client: client.gl_transfer_pnl(make_call(), GlTransferAsk(2026, 9)))
        self.assertEqual(captured["path"], "/u8co/v1/gl/transfer/pnl")
        self.assertEqual(list(sent), _AUTH + ["fiscal_year", "period"])

    def test_custom_wire_body_full(self) -> None:
        ask = GlTransferAsk(2026, 9, "2026-09-30", "0001")
        sent, captured = self._send(lambda client: client.gl_transfer_custom(make_call(), ask, idempotency_key="tr-1"))
        self.assertEqual(captured["path"], "/u8co/v1/gl/transfer/custom")
        self.assertEqual(list(sent), _AUTH + ["fiscal_year", "period", "voucher_date", "tran_id", "idempotency_key"])

    def test_dry_with_exclude_existing(self) -> None:
        ask = GlTransferAsk(2025, 6, exclude_existing=True)
        sent, _captured = self._send(lambda client: client.dry().gl_transfer_pnl(make_call(), ask))
        self.assertIs(sent["dry_run"], True)
        self.assertIs(sent["exclude_existing"], True)

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad = (
            (GlTransferAsk(2026, 13), "period"),
            (GlTransferAsk(99, 9), "fiscal_year"),
            (GlTransferAsk(2026, 9, "2026/09/30"), "voucher_date"),
            (GlTransferAsk(2026, 9, "2026-10-01"), "voucher_date"),
            (GlTransferAsk(2026, 9, None, "00 1"), "tran_id"),
        )
        for ask, word in bad:
            with self.subTest(ask=ask), self.assertRaises(ValueError) as caught:
                client.gl_transfer_custom(make_call(), ask)
            self.assertIn(word, str(caught.exception))
        with self.assertRaises(ValueError):
            client.gl_transfer_pnl(make_call(), GlTransferAsk(2026, 9, None, "0001"))

    def test_routes_and_commands_are_registered(self) -> None:
        self.assertIn("/v1/gl/transfer/pnl", WRITE_ROUTES)
        self.assertIn("/v1/gl/transfer/custom", WRITE_ROUTES)
        for name in ("gl-transfer-pnl", "gl-transfer-custom"):
            self.assertIn(name, WRITE_COMMANDS)
            self.assertIn(name, KEY_COMMANDS)

    def test_cli_parses_custom(self) -> None:
        parsed = _parser().parse_args(
            _argv("gl-transfer-custom", "--fiscal-year", "2026", "--period", "9", "--tran-id", "0002", "--dry-run")
        )
        self.assertEqual((parsed.fiscal_year, parsed.period, parsed.tran_id), (2026, 9, "0002"))
        self.assertTrue(parsed.dry_run)
        self.assertFalse(parsed.exclude_existing)


if __name__ == "__main__":
    unittest.main()
