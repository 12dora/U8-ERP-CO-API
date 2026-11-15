"""月末结账（/v1/periods/close）的请求正文、预演、幂等键和命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import (
    _AUTH,
    SECRET,
    _argv,
    _assert_signed,
    make_call,
    _run_main,
    _Running,
)
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_idem import WRITE_ROUTES
from co.client.u8co_periods import PeriodClose

_KEY = "close-2026-09"
_GL = PeriodClose("gl", 2026, 9)
_THROUGH = PeriodClose("", 2026, 8, through=True)
_THROUGH_9 = PeriodClose("", 2026, 9, through=True)
_FIELDS = ["module", "fiscal_year", "period", "action"]


class PeriodsCloseTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[dict[str, Any], dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        return sent, captured

    def test_wire_body_close_and_reopen(self) -> None:
        for action in ("close", "reopen"):
            req = PeriodClose("gl", 2026, 9, action)
            sent, captured = self._send(lambda client, r=req: client.close_period(make_call(), r))
            self.assertEqual(captured["path"], "/u8co/v1/periods/close")
            self.assertEqual(list(sent), _AUTH + _FIELDS)
            got = (sent["module"], sent["fiscal_year"], sent["period"], sent["action"])
            self.assertEqual(got, ("gl", 2026, 9, action))

    def test_through_omits_module(self) -> None:
        sent, _captured = self._send(lambda client: client.close_period(make_call(), _THROUGH))
        self.assertEqual(list(sent), _AUTH + ["fiscal_year", "period", "action", "through"])
        self.assertIs(sent["through"], True)

    def test_dry_client_adds_dry_run(self) -> None:
        sent, _captured = self._send(lambda client: client.dry().close_period(make_call(), _GL))
        self.assertEqual(list(sent), _AUTH + _FIELDS + ["dry_run"])
        self.assertIs(sent["dry_run"], True)

    def test_keyed_client_adds_the_key(self) -> None:
        sent, _captured = self._send(lambda client: client.keyed(_KEY).close_period(make_call(), _GL))
        self.assertEqual(sent["idempotency_key"], _KEY)

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad = (
            (PeriodClose("xx", 2026, 9), "module"),
            (PeriodClose("", 2026, 9), "module"),
            (PeriodClose("gl", 26, 9), "fiscal_year"),
            (PeriodClose("gl", 2026, 13), "period"),
            (PeriodClose("gl", 2026, 9, "post"), "action"),
            (PeriodClose("", 2026, 9, "reopen", True), "through"),
        )
        for req, word in bad:
            with self.subTest(req=req), self.assertRaises(ValueError) as caught:
                client.close_period(make_call(), req)
            self.assertIn(word, str(caught.exception))

    def test_route_is_a_write_route_and_command_takes_flags(self) -> None:
        self.assertIn("/v1/periods/close", WRITE_ROUTES)
        self.assertIn("periods-close", WRITE_COMMANDS)
        self.assertIn("periods-close", KEY_COMMANDS)

    def test_parse(self) -> None:
        base = ("--fiscal-year", "2026", "--period", "9")
        parsed = _parser().parse_args(_argv("periods-close", "--module", "gl", *base))
        self.assertEqual((parsed.module, parsed.fiscal_year, parsed.period, parsed.reopen), ("gl", 2026, 9, False))
        self.assertEqual((parsed.through, parsed.dry_run), (False, False))
        parsed = _parser().parse_args(_argv("periods-close", "--through", *base, "--dry-run"))
        self.assertEqual((parsed.module, parsed.through, parsed.dry_run), (None, True, True))
        keyed = _parser().parse_args(_argv("periods-close", "--module", "pu", *base, "--idempotency-key", _KEY))
        self.assertEqual(keyed.idempotency_key, _KEY)
        for bad in (
            base,
            ("--module", "gl"),
            ("--module", "xx", *base),
            ("--module", "gl", "--through", *base),
            ("--module", "gl", "--fiscal-year", "2026", "--period", "13"),
        ):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                _parser().parse_args(_argv("periods-close", *bad))

    def test_main_wires_close_reopen_and_through(self) -> None:
        seen: list[PeriodClose] = []

        def _close(self: U8CoClient, call: U8Call, req: PeriodClose) -> dict[str, Any]:
            seen.append(req)
            return {"ok": True}

        base = ("--fiscal-year", "2026", "--period", "9")
        self.assertEqual(_run_main(_argv("periods-close", "--module", "gl", *base), "close_period", _close), 0)
        reopen = _argv("periods-close", "--module", "ap", *base, "--reopen")
        self.assertEqual(_run_main(reopen, "close_period", _close), 0)
        self.assertEqual(_run_main(_argv("periods-close", "--through", *base), "close_period", _close), 0)
        want = [PeriodClose("gl", 2026, 9), PeriodClose("ap", 2026, 9, "reopen"), _THROUGH_9]
        self.assertEqual(seen, want)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            _run_main(_argv("periods-close", "--through", *base, "--reopen"), "close_period", _close)


if __name__ == "__main__":
    unittest.main()
