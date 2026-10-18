"""客户专管业务员 arc_customer_person：正文、expect 空串、预演、幂等键和本地校验。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_gl_arc import _call
from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _assert_signed, _argv, _run_main, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_idem import WRITE_ROUTES

_CODE = "C900001"
_PERSON = "EX040001"


class ArcCustomerPersonTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_minimal_body(self) -> None:
        path, sent = self._send(lambda client: client.arc_customer_person(_call(), _CODE, _PERSON))
        self.assertEqual(path, "/u8co/v1/archives/customer_person")
        self.assertEqual(list(sent), _AUTH + ["code", "person"])
        self.assertEqual((sent["code"], sent["person"]), (_CODE, _PERSON))
        self.assertNotIn("expect", sent)
        self.assertNotIn("archive", sent)

    def test_expect_empty_string_is_sent(self) -> None:
        path, sent = self._send(lambda client: client.arc_customer_person(_call(), _CODE, _PERSON, expect=""))
        self.assertEqual(path, "/u8co/v1/archives/customer_person")
        self.assertEqual(sent["expect"], "")

    def test_expect_value_and_idempotency_key(self) -> None:
        path, sent = self._send(
            lambda client: client.arc_customer_person(
                _call(), _CODE, _PERSON, expect="EX040002", idempotency_key="pperson-1"
            )
        )
        self.assertEqual(path, "/u8co/v1/archives/customer_person")
        self.assertEqual(sent["expect"], "EX040002")
        self.assertEqual(sent["idempotency_key"], "pperson-1")

    def test_dry_client_adds_dry_run(self) -> None:
        path, sent = self._send(lambda client: client.dry().arc_customer_person(_call(), _CODE, _PERSON))
        self.assertEqual(path, "/u8co/v1/archives/customer_person")
        self.assertIs(sent["dry_run"], True)

    def test_keyed_client_adds_idempotency_key(self) -> None:
        path, sent = self._send(lambda client: client.keyed("pperson-2").arc_customer_person(_call(), _CODE, _PERSON))
        self.assertEqual(path, "/u8co/v1/archives/customer_person")
        self.assertEqual(sent["idempotency_key"], "pperson-2")

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        call = _call()
        bad = (
            (lambda: client.arc_customer_person(call, "", _PERSON), "code"),
            (lambda: client.arc_customer_person(call, _CODE, ""), "person"),
            (lambda: client.arc_customer_person(call, _CODE, "E" * 21), "person"),
            (lambda: client.arc_customer_person(call, _CODE, _PERSON, expect="E" * 21), "expect"),
        )
        for invoke, word in bad:
            with self.subTest(word=word), self.assertRaises(ValueError) as caught:
                invoke()
            self.assertIn(word, str(caught.exception))

    def test_route_is_a_write(self) -> None:
        self.assertIn("/v1/archives/customer_person", WRITE_ROUTES)


class CliTests(unittest.TestCase):
    def test_command_takes_dry_run_and_key(self) -> None:
        self.assertIn("arc-customer-person", WRITE_COMMANDS)
        self.assertIn("arc-customer-person", KEY_COMMANDS)
        parsed = _parser().parse_args(
            _argv("arc-customer-person", "--code", _CODE, "--person", _PERSON, "--expect", "EX040002")
        )
        self.assertEqual((parsed.code, parsed.person, parsed.expect), (_CODE, _PERSON, "EX040002"))
        self.assertFalse(parsed.dry_run)
        self.assertIsNone(parsed.idempotency_key)
        dry = _parser().parse_args(
            _argv("arc-customer-person", "--code", _CODE, "--person", _PERSON, "--dry-run")
        )
        self.assertTrue(dry.dry_run)
        keyed = _parser().parse_args(
            _argv("arc-customer-person", "--code", _CODE, "--person", _PERSON, "--idempotency-key", "pperson-cli")
        )
        self.assertEqual(keyed.idempotency_key, "pperson-cli")

    def test_main_wires_flags(self) -> None:
        seen: dict[str, object] = {}

        def _op(self: U8CoClient, call: U8Call, code: str, person: str, **extra: object) -> dict[str, object]:
            del call
            seen["args"] = (code, person, extra.get("expect"), self._dry)
            return {"ok": True, "code": code, "before": None, "after": person, "dept_before": None, "dept_after": "D901"}

        argv = _argv("arc-customer-person", "--code", _CODE, "--person", _PERSON, "--expect", "")
        self.assertEqual(_run_main(argv, "arc_customer_person", _op), 0)
        self.assertEqual(seen["args"], (_CODE, _PERSON, "", False))
        argv = _argv("arc-customer-person", "--code", _CODE, "--person", _PERSON, "--dry-run")
        self.assertEqual(_run_main(argv, "arc_customer_person", _op), 0)
        self.assertEqual(seen["args"][3], True)


if __name__ == "__main__":
    unittest.main()
