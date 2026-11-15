"""总账红字冲销（/v1/gl/vouchers/reverse）的请求正文、预演、幂等键和命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import _AUTH, SECRET, _argv, _assert_signed, make_call, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_client import U8CoClient
from co.client.u8co_gl_reverse import GlReverseAsk
from co.client.u8co_idem import WRITE_ROUTES
from co.client.u8co_gl_arc import GlKey

_KEY = GlKey(12, "转", 9)
_FIELDS = ["period", "sign", "no"]


class GlReverseClientTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[dict[str, Any], dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        return sent, captured

    def test_wire_body_minimal(self) -> None:
        sent, captured = self._send(lambda client: client.gl_reverse(make_call(), GlReverseAsk(_KEY)))
        self.assertEqual(captured["path"], "/u8co/v1/gl/vouchers/reverse")
        self.assertEqual(list(sent), _AUTH + _FIELDS)
        self.assertEqual((sent["period"], sent["sign"], sent["no"]), (12, "转", 9))

    def test_wire_body_with_year_and_date(self) -> None:
        ask = GlReverseAsk(_KEY, 2025, "2026-01-31")
        sent, _captured = self._send(lambda client: client.gl_reverse(make_call(), ask, idempotency_key="rev-9"))
        self.assertEqual(list(sent), _AUTH + _FIELDS + ["fiscal_year", "voucher_date", "idempotency_key"])
        self.assertEqual((sent["fiscal_year"], sent["voucher_date"]), (2025, "2026-01-31"))

    def test_dry_client_adds_dry_run(self) -> None:
        sent, _captured = self._send(lambda client: client.dry().gl_reverse(make_call(), GlReverseAsk(_KEY)))
        self.assertIs(sent["dry_run"], True)

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad = (
            (GlReverseAsk(GlKey(13, "转", 9)), "period"),
            (GlReverseAsk(GlKey(12, "", 9)), "sign"),
            (GlReverseAsk(GlKey(12, "转", 0)), "no"),
            (GlReverseAsk(_KEY, 99), "fiscal_year"),
            (GlReverseAsk(_KEY, None, "2026/01/31"), "voucher_date"),
        )
        for ask, word in bad:
            with self.subTest(ask=ask), self.assertRaises(ValueError) as caught:
                client.gl_reverse(make_call(), ask)
            self.assertIn(word, str(caught.exception))

    def test_route_and_command_are_writes(self) -> None:
        self.assertIn("/v1/gl/vouchers/reverse", WRITE_ROUTES)
        self.assertIn("gl-reverse", WRITE_COMMANDS)
        self.assertIn("gl-reverse", KEY_COMMANDS)

    def test_parse(self) -> None:
        base = ("--period", "12", "--sign", "转", "--no", "9")
        parsed = _parser().parse_args(_argv("gl-reverse", *base))
        self.assertEqual((parsed.period, parsed.sign, parsed.no), (12, "转", 9))
        self.assertEqual((parsed.fiscal_year, parsed.voucher_date, parsed.dry_run), (None, None, False))
        parsed = _parser().parse_args(
            _argv("gl-reverse", *base, "--fiscal-year", "2025", "--voucher-date", "2026-01-31", "--dry-run")
        )
        self.assertEqual((parsed.fiscal_year, parsed.voucher_date, parsed.dry_run), (2025, "2026-01-31", True))
        parsed = _parser().parse_args(_argv("gl-reverse", *base, "--idempotency-key", "rev-9"))
        self.assertEqual(parsed.idempotency_key, "rev-9")


if __name__ == "__main__":
    unittest.main()
