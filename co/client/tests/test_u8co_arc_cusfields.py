"""客户字段 arc_customer_fields：正文、expect、预演、幂等键、本地校验和命令行。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_gl_arc import _call
from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _assert_signed, _argv, _run_main, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_idem import WRITE_ROUTES

_CODE = "C900001"
_ROUTE = "/u8co/v1/archives/customer_fields"
_FIELDS = {"abbrname": "示例简称", "district": "33", "end_date": "2026-10-20", "sa_protocol": "A001"}
_EXPECT = {"abbrname": "示例客户有限公司", "district": "", "end_date": "", "sa_protocol": ""}


class ArcCustomerFieldsTests(unittest.TestCase):
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
        path, sent = self._send(lambda client: client.arc_customer_fields(_call(), _CODE, dict(_FIELDS)))
        self.assertEqual(path, _ROUTE)
        self.assertEqual(list(sent), _AUTH + ["code", "fields"])
        self.assertEqual(sent["code"], _CODE)
        self.assertEqual(sent["fields"], _FIELDS)

    def test_expect_with_empty_strings_and_key(self) -> None:
        path, sent = self._send(
            lambda client: client.arc_customer_fields(
                _call(), _CODE, dict(_FIELDS), expect=dict(_EXPECT), idempotency_key="cusfields-1"
            )
        )
        self.assertEqual(path, _ROUTE)
        self.assertEqual(sent["expect"], _EXPECT)
        self.assertEqual(sent["idempotency_key"], "cusfields-1")

    def test_clearing_values_are_sent(self) -> None:
        fields = {"district": "", "end_date": ""}
        _path, sent = self._send(lambda client: client.arc_customer_fields(_call(), _CODE, fields))
        self.assertEqual(sent["fields"], fields)

    def test_sa_protocol_alone_and_clearing_pass_the_local_check(self) -> None:
        for fields, expect in (({"sa_protocol": "A001"}, {"sa_protocol": ""}), ({"sa_protocol": ""}, {"sa_protocol": "A001"})):
            _path, sent = self._send(
                lambda client, f=fields, e=expect: client.arc_customer_fields(_call(), _CODE, dict(f), expect=dict(e))
            )
            self.assertEqual(sent["fields"], fields)
            self.assertEqual(sent["expect"], expect)

    def test_dry_and_keyed_clients(self) -> None:
        _path, sent = self._send(lambda client: client.dry().arc_customer_fields(_call(), _CODE, dict(_FIELDS)))
        self.assertIs(sent["dry_run"], True)
        _path, sent = self._send(
            lambda client: client.keyed("cusfields-2").arc_customer_fields(_call(), _CODE, dict(_FIELDS))
        )
        self.assertEqual(sent["idempotency_key"], "cusfields-2")

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        call = _call()
        bad = (
            (lambda: client.arc_customer_fields(call, "", dict(_FIELDS)), "code"),
            (lambda: client.arc_customer_fields(call, _CODE, {}), "fields"),
            (lambda: client.arc_customer_fields(call, _CODE, ["abbrname"]), "fields"),
            (lambda: client.arc_customer_fields(call, _CODE, {"cusname": "x"}), "cusname"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": ""}), "abbrname"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "  "}), "abbrname"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "甲" * 61}), "abbrname"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "甲\n乙"}), "abbrname"),
            (lambda: client.arc_customer_fields(call, _CODE, {"district": "1" * 13}), "district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"district": " 33"}), "district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"district": 33}), "district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A" * 21}), "sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": " A001"}), "sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A001 "}), "sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A\n001"}), "sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": 1}), "sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A001"}, expect={"sa_protocol": "A" * 21}), "expect"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A001"}, expect={"sa_protocol": " A001"}), "expect.sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A001"}, expect={"sa_protocol": "A001 "}), "expect.sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A001"}, expect={"sa_protocol": "A\n001"}), "expect.sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"district": "33"}, expect={"district": "33 "}), "expect.district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"district": "33"}, expect={"district": "3\t3"}), "expect.district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"end_date": "2026/10/20"}), "end_date"),
            (lambda: client.arc_customer_fields(call, _CODE, {"end_date": "2026-02-30"}), "end_date"),
            (lambda: client.arc_customer_fields(call, _CODE, {"end_date": None}), "end_date"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "x"}, expect={"district": ""}), "district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "x"}, expect={"abbrname": 1}), "expect"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "x"}, expect={"abbrname": "y" * 61}), "expect"),
            (lambda: client.arc_customer_fields(call, _CODE, {"end_date": "2026-10-20"}, expect={"end_date": "x"}), "end_date"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "x"}, idempotency_key="has space"), "idempotency_key"),
        )
        for invoke, word in bad:
            with self.subTest(word=word), self.assertRaises(ValueError) as caught:
                invoke()
            self.assertIn(word, str(caught.exception))

    def test_route_is_a_write(self) -> None:
        self.assertIn("/v1/archives/customer_fields", WRITE_ROUTES)


class CliTests(unittest.TestCase):
    def _file(self, data: object) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, handle.name)
        with handle:
            json.dump(data, handle, ensure_ascii=False)
        return handle.name

    def test_command_takes_dry_run_and_key(self) -> None:
        self.assertIn("arc-customer-fields", WRITE_COMMANDS)
        self.assertIn("arc-customer-fields", KEY_COMMANDS)
        parsed = _parser().parse_args(
            _argv("arc-customer-fields", "--code", _CODE, "--file", "edit.json", "--dry-run", "--idempotency-key", "k1")
        )
        self.assertEqual((parsed.code, parsed.file), (_CODE, "edit.json"))
        self.assertTrue(parsed.dry_run)
        self.assertEqual(parsed.idempotency_key, "k1")

    def test_main_reads_fields_and_expect_from_the_file(self) -> None:
        seen: dict[str, object] = {}

        def _op(self: U8CoClient, call: U8Call, code: str, fields: dict, **extra: object) -> dict[str, object]:
            del call
            seen["args"] = (code, fields, extra.get("expect"), self._dry)
            return {"ok": True, "code": code, "changed": True, "before": {}, "after": fields}

        path = self._file({"fields": _FIELDS, "expect": _EXPECT})
        argv = _argv("arc-customer-fields", "--code", _CODE, "--file", path, "--dry-run")
        self.assertEqual(_run_main(argv, "arc_customer_fields", _op), 0)
        self.assertEqual(seen["args"], (_CODE, _FIELDS, _EXPECT, True))
        path = self._file({"fields": _FIELDS})
        self.assertEqual(_run_main(_argv("arc-customer-fields", "--code", _CODE, "--file", path), "arc_customer_fields", _op), 0)
        self.assertEqual(seen["args"], (_CODE, _FIELDS, None, False))

    def test_file_with_other_keys_is_refused(self) -> None:
        path = self._file({"fields": _FIELDS, "archive": "customer"})
        argv = _argv("arc-customer-fields", "--code", _CODE, "--file", path)
        with self.assertRaises(SystemExit):
            _run_main(argv, "arc_customer_fields", lambda *_a, **_k: {})


if __name__ == "__main__":
    unittest.main()
