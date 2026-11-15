"""期初记账（/v1/openings/post，含存货核算 ia）、应收应付期初单据（/v1/openings/arap）的请求正文、预演、幂等键和命令行解析。
只监听 127.0.0.1。"""

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

_KEY = "pu-open-2024"


def _capture(case: unittest.TestCase, invoke: Callable[[U8CoClient], object]) -> tuple[dict[str, Any], dict[str, Any]]:
    with _Running() as bridge:
        invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
        captured = bridge.capture
    _assert_signed(case, captured)
    sent = json.loads(captured["body"].decode("utf-8"))
    case.assertNotIn("password", sent)
    return sent, captured


class OpeningsPostTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[dict[str, Any], dict[str, Any]]:
        return _capture(self, invoke)

    def test_wire_body_post_and_unpost(self) -> None:
        for action in ("post", "unpost"):
            sent, captured = self._send(lambda client, act=action: client.post_openings(make_call(), "pu", act))
            self.assertEqual(captured["path"], "/u8co/v1/openings/post")
            self.assertEqual(list(sent), _AUTH + ["module", "action"])
            self.assertEqual((sent["module"], sent["action"]), ("pu", action))

    def test_wire_body_ia(self) -> None:
        sent, _captured = self._send(lambda client: client.post_openings(make_call(), "ia", "unpost"))
        self.assertEqual(list(sent), _AUTH + ["module", "action"])
        self.assertEqual((sent["module"], sent["action"]), ("ia", "unpost"))

    def test_defaults_are_pu_post(self) -> None:
        sent, _captured = self._send(lambda client: client.post_openings(make_call()))
        self.assertEqual((sent["module"], sent["action"]), ("pu", "post"))

    def test_dry_client_adds_dry_run(self) -> None:
        sent, _captured = self._send(lambda client: client.dry().post_openings(make_call()))
        self.assertEqual(list(sent), _AUTH + ["module", "action", "dry_run"])
        self.assertIs(sent["dry_run"], True)

    def test_keyed_client_adds_the_key(self) -> None:
        sent, _captured = self._send(lambda client: client.keyed(_KEY).post_openings(make_call()))
        self.assertEqual(sent["idempotency_key"], _KEY)

    def test_rejects_bad_module_and_action(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError) as module:
            client.post_openings(make_call(), "st", "post")
        self.assertIn("pu", str(module.exception))
        self.assertIn("ia", str(module.exception))
        with self.assertRaises(ValueError) as action:
            client.post_openings(make_call(), "pu", "close")
        self.assertIn("post 或 unpost", str(action.exception))

    def test_route_is_a_write_route_and_command_takes_flags(self) -> None:
        self.assertIn("/v1/openings/post", WRITE_ROUTES)
        self.assertIn("openings-post", WRITE_COMMANDS)
        self.assertIn("openings-post", KEY_COMMANDS)

    def test_parse(self) -> None:
        parsed = _parser().parse_args(_argv("openings-post", "--module", "pu"))
        self.assertEqual((parsed.module, parsed.unpost, parsed.dry_run), ("pu", False, False))
        parsed = _parser().parse_args(_argv("openings-post", "--module", "pu", "--unpost", "--dry-run"))
        self.assertEqual((parsed.unpost, parsed.dry_run), (True, True))
        parsed = _parser().parse_args(_argv("openings-post", "--module", "ia", "--unpost"))
        self.assertEqual((parsed.module, parsed.unpost), ("ia", True))
        keyed = _parser().parse_args(_argv("openings-post", "--module", "pu", "--idempotency-key", _KEY))
        self.assertEqual(keyed.idempotency_key, _KEY)
        for bad in ((), ("--module", "st"), ("--module", "IA"), ("--module", "pu", "--action", "post")):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                _parser().parse_args(_argv("openings-post", *bad))

    def test_main_wires_post_and_unpost(self) -> None:
        seen: list[tuple[str, str]] = []

        def _post(self: U8CoClient, call: U8Call, module: str = "pu", action: str = "post") -> dict[str, Any]:
            seen.append((module, action))
            return {"ok": True}

        self.assertEqual(_run_main(_argv("openings-post", "--module", "pu"), "post_openings", _post), 0)
        self.assertEqual(_run_main(_argv("openings-post", "--module", "pu", "--unpost"), "post_openings", _post), 0)
        self.assertEqual(seen, [("pu", "post"), ("pu", "unpost")])


_ARAP_CREATE = ("--side", "ar", "--action", "create", "--partner", "C001", "--amount", "-120.5", "--account", "112201")


class OpeningsArapTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[dict[str, Any], dict[str, Any]]:
        return _capture(self, invoke)

    def test_wire_body_create(self) -> None:
        sent, captured = self._send(
            lambda client: client.openings_arap(
                make_call(), "ap", "create", partner="V001", amount=300, account="220201", currency="美元", exch_rate=7.1
            )
        )
        self.assertEqual(captured["path"], "/u8co/v1/openings/arap")
        keys = ["side", "action", "partner", "amount", "account", "currency", "exch_rate"]
        self.assertEqual(list(sent), _AUTH + keys)
        self.assertEqual((sent["side"], sent["action"], sent["amount"], sent["exch_rate"]), ("ap", "create", 300, 7.1))

    def test_wire_body_by_id(self) -> None:
        for action in ("delete", "verify", "unverify"):
            sent, _captured = self._send(lambda client, act=action: client.openings_arap(make_call(), "ar", act, 41))
            self.assertEqual(list(sent), _AUTH + ["side", "action", "id"])
            self.assertEqual((sent["action"], sent["id"]), (action, 41))

    def test_dry_and_keyed_clients(self) -> None:
        sent, _captured = self._send(lambda client: client.dry().openings_arap(make_call(), "ar", "verify", 41))
        self.assertIs(sent["dry_run"], True)
        sent, _captured = self._send(lambda client: client.keyed(_KEY).openings_arap(make_call(), "ar", "delete", 41))
        self.assertEqual(sent["idempotency_key"], _KEY)

    def test_rejects_bad_shapes_locally(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad = (
            (("gl", "verify", 1), {}, "ar 或 ap"),
            (("ar", "close", 1), {}, "action"),
            (("ar", "verify", None), {}, "需要单据 id"),
            (("ar", "verify", 0), {}, "需要单据 id"),
            (("ar", "delete", 1), {"partner": "C001"}, "只收 id"),
            (("ar", "create", 1), {"partner": "C001", "amount": 1, "account": "1122"}, "不收 id"),
            (("ar", "create", None), {"partner": "C001", "account": "1122"}, "amount"),
            (("ar", "create", None), {"partner": "C001", "amount": 1, "account": "1122", "date": "x"}, "不认识"),
        )
        for args, create, word in bad:
            with self.subTest(args=args, create=create), self.assertRaises(ValueError) as caught:
                client.openings_arap(make_call(), *args, **create)
            self.assertIn(word, str(caught.exception))

    def test_route_is_a_write_route_and_command_takes_flags(self) -> None:
        self.assertIn("/v1/openings/arap", WRITE_ROUTES)
        self.assertIn("openings-arap", WRITE_COMMANDS)
        self.assertIn("openings-arap", KEY_COMMANDS)

    def test_parse(self) -> None:
        parsed = _parser().parse_args(_argv("openings-arap", *_ARAP_CREATE, "--exch-rate", "1"))
        self.assertEqual((parsed.side, parsed.action, parsed.amount, parsed.exch_rate), ("ar", "create", -120.5, 1.0))
        by_id = ("--side", "ap", "--action", "verify", "--id", "41", "--dry-run")
        parsed = _parser().parse_args(_argv("openings-arap", *by_id))
        self.assertEqual((parsed.id, parsed.dry_run, parsed.partner), (41, True, None))
        for bad in (("--side", "gl", "--action", "verify"), ("--side", "ar"), ("--side", "ar", "--action", "close")):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                _parser().parse_args(_argv("openings-arap", *bad))

    def test_main_wires_create_and_verify(self) -> None:
        seen: list[tuple[Any, ...]] = []

        def _arap(self: U8CoClient, call: U8Call, side: str, action: str, doc_id: Any = None, **create: Any) -> dict:
            seen.append((side, action, doc_id, create))
            return {"ok": True}

        self.assertEqual(_run_main(_argv("openings-arap", *_ARAP_CREATE), "openings_arap", _arap), 0)
        verify = ("--side", "ap", "--action", "verify", "--id", "41")
        self.assertEqual(_run_main(_argv("openings-arap", *verify), "openings_arap", _arap), 0)
        create = {"partner": "C001", "amount": -120.5, "account": "112201"}
        self.assertEqual(seen, [("ar", "create", None, create), ("ap", "verify", 41, {})])


if __name__ == "__main__":
    unittest.main()
