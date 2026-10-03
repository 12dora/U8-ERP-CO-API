"""存货核算（/v1/ia/post、/v1/ia/period_end）的请求正文、预演、幂等键和命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest import mock
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
from co.client.u8co_ia import IaMonth, is_long_call
from co.client.u8co_idem import WRITE_ROUTES

_KEY = "ia-2026-09"
_POST = IaMonth(2026, 9, "post")
_RUN = IaMonth(2026, 9, "run")
_FIELDS = ["fiscal_year", "period", "action"]
_BASE = ("--fiscal-year", "2026", "--period", "9")


class IaClientTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[dict[str, Any], dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        return sent, captured

    def test_wire_body_post_and_unpost(self) -> None:
        for action in ("post", "unpost"):
            req = IaMonth(2026, 9, action)
            sent, captured = self._send(lambda client, r=req: client.ia_post(make_call(), r))
            self.assertEqual(captured["path"], "/u8co/v1/ia/post")
            self.assertEqual(list(sent), _AUTH + _FIELDS)
            self.assertEqual((sent["fiscal_year"], sent["period"], sent["action"]), (2026, 9, action))

    def test_post_sends_on_uncosted_last(self) -> None:
        req = IaMonth(2026, 9, "post", "skip")
        sent, _captured = self._send(lambda client: client.ia_post(make_call(), req))
        self.assertEqual(list(sent), _AUTH + _FIELDS + ["on_uncosted"])
        self.assertEqual(sent["on_uncosted"], "skip")

    def test_wire_body_period_end(self) -> None:
        for action in ("run", "cancel"):
            req = IaMonth(2026, 9, action)
            sent, captured = self._send(lambda client, r=req: client.ia_period_end(make_call(), r))
            self.assertEqual(captured["path"], "/u8co/v1/ia/period_end")
            self.assertEqual(list(sent), _AUTH + _FIELDS)
            self.assertEqual(sent["action"], action)

    def test_dry_client_adds_dry_run(self) -> None:
        sent, _captured = self._send(lambda client: client.dry().ia_period_end(make_call(), _RUN))
        self.assertEqual(list(sent), _AUTH + _FIELDS + ["dry_run"])
        self.assertIs(sent["dry_run"], True)

    def test_keyed_client_adds_the_key(self) -> None:
        sent, _captured = self._send(lambda client: client.keyed(_KEY).ia_post(make_call(), _POST))
        self.assertEqual(sent["idempotency_key"], _KEY)

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad_post = (
            (IaMonth(26, 9, "post"), "fiscal_year"),
            (IaMonth(2026, 0, "post"), "period"),
            (IaMonth(2026, 13, "post"), "period"),
            (IaMonth(2026, 9, "run"), "action"),
            (IaMonth(2026, 9, "post", "manual"), "on_uncosted"),
            (IaMonth(2026, 9, "unpost", "skip"), "on_uncosted"),
        )
        for req, word in bad_post:
            with self.subTest(req=req), self.assertRaises(ValueError) as caught:
                client.ia_post(make_call(), req)
            self.assertIn(word, str(caught.exception))
        for req, word in ((IaMonth(2026, 9, "post"), "action"), (IaMonth(2026, 9, "run", "skip"), "on_uncosted")):
            with self.subTest(req=req), self.assertRaises(ValueError) as caught:
                client.ia_period_end(make_call(), req)
            self.assertIn(word, str(caught.exception))

    def test_routes_are_write_routes_and_commands_take_flags(self) -> None:
        for route in ("/v1/ia/post", "/v1/ia/period_end"):
            self.assertIn(route, WRITE_ROUTES)
        for name in ("ia-post", "ia-period-end"):
            self.assertIn(name, WRITE_COMMANDS)
            self.assertIn(name, KEY_COMMANDS)

    def test_parse(self) -> None:
        parsed = _parser().parse_args(_argv("ia-post", *_BASE))
        self.assertEqual((parsed.fiscal_year, parsed.period, parsed.unpost, parsed.on_uncosted), (2026, 9, False, None))
        parsed = _parser().parse_args(_argv("ia-post", *_BASE, "--on-uncosted", "skip", "--dry-run"))
        self.assertEqual((parsed.on_uncosted, parsed.dry_run), ("skip", True))
        parsed = _parser().parse_args(_argv("ia-period-end", *_BASE, "--cancel", "--idempotency-key", _KEY))
        self.assertEqual((parsed.cancel, parsed.idempotency_key), (True, _KEY))
        for command, bad in (
            ("ia-post", ("--period", "9")),
            ("ia-post", (*_BASE, "--on-uncosted", "manual")),
            ("ia-post", ("--fiscal-year", "2026", "--period", "13")),
            ("ia-period-end", (*_BASE, "--unpost")),
        ):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                _parser().parse_args(_argv(command, *bad))

    def test_main_wires_post_and_period_end(self) -> None:
        seen: list[tuple[str, IaMonth]] = []

        def _post(self: U8CoClient, call: U8Call, req: IaMonth) -> dict[str, Any]:
            seen.append(("post", req))
            return {"ok": True}

        def _end(self: U8CoClient, call: U8Call, req: IaMonth) -> dict[str, Any]:
            seen.append(("end", req))
            return {"ok": True}

        self.assertEqual(_run_main(_argv("ia-post", *_BASE, "--on-uncosted", "skip"), "ia_post", _post), 0)
        self.assertEqual(_run_main(_argv("ia-post", *_BASE, "--unpost"), "ia_post", _post), 0)
        self.assertEqual(_run_main(_argv("ia-period-end", *_BASE), "ia_period_end", _end), 0)
        self.assertEqual(_run_main(_argv("ia-period-end", *_BASE, "--cancel"), "ia_period_end", _end), 0)
        want = [
            ("post", IaMonth(2026, 9, "post", "skip")),
            ("post", IaMonth(2026, 9, "unpost")),
            ("end", _RUN),
            ("end", IaMonth(2026, 9, "cancel")),
        ]
        self.assertEqual(seen, want)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            _run_main(_argv("ia-post", *_BASE, "--unpost", "--on-uncosted", "skip"), "ia_post", _post)


class LongTimeoutTests(unittest.TestCase):
    def test_long_routes(self) -> None:
        cases = (
            ("/v1/ia/post", {}, True),
            ("/v1/ia/period_end", {}, True),
            ("/v1/periods/close", {"module": "ia"}, True),
            ("/v1/periods/close", {"through": True}, True),
            ("/v1/periods/close", {"module": "gl"}, False),
            ("/v1/openings/post", {"module": "ia"}, True),
            ("/v1/openings/post", {"module": "pu"}, False),
            ("/v1/vouchers/create", {"module": "ia"}, False),
        )
        for route, fields, want in cases:
            with self.subTest(route=route, fields=fields):
                self.assertIs(is_long_call(route, fields), want)

    def test_call_uses_the_long_timeout_only_for_long_routes(self) -> None:
        seen: list[float] = []

        def _exchange(_self: U8CoClient, _method: str, _path: str, _body: object, _headers: object, timeout: float):
            seen.append(timeout)
            return 200, b'{"ok":true}'

        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=90)
        with mock.patch.object(U8CoClient, "_exchange", _exchange):
            client.ia_post(make_call(), _POST)
            client.dry().ia_period_end(make_call(), _RUN)
            client.call("/v1/periods/close", {"password": "pw", "module": "ia", "action": "close"})
            client.call("/v1/periods/close", {"password": "pw", "module": "gl", "action": "close"})
            client.login_check(make_call())
            client.health()
        self.assertEqual(seen, [1000, 1000, 1000, 90, 90, 90])
        short = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=120, long_timeout=60)
        with mock.patch.object(U8CoClient, "_exchange", _exchange):
            short.ia_post(make_call(), _POST)
        self.assertEqual(seen[-1], 120)


if __name__ == "__main__":
    unittest.main()
