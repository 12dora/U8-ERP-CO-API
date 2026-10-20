"""客户收货地址 arc_customer_address：正文、expect、预演、幂等键、本地校验和命令行。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_gl_arc import _call
from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _assert_signed, _argv, _run_main, _Running
from co.client.u8co_arc_cus import CustomerAddressEdit
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_idem import WRITE_ROUTES

_CODE = "C900001"
_ROUTE = "/u8co/v1/archives/customer_address"
_FIELDS = {"code": "01", "name": "示例车间", "unit": "张三", "default": True}
_EXPECT = {"name": "3车间", "unit": "", "default": False}


def _edit(**over: Any) -> CustomerAddressEdit:
    values: dict[str, Any] = {"code": _CODE, "address": "1", "fields": dict(_FIELDS), "expect": dict(_EXPECT)}
    values.update(over)
    return CustomerAddressEdit(**values)


class ArcCustomerAddressTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_full_body(self) -> None:
        path, sent = self._send(lambda client: client.arc_customer_address(_call(), _edit()))
        self.assertEqual(path, _ROUTE)
        self.assertEqual(list(sent), _AUTH + ["code", "address", "fields", "expect"])
        self.assertEqual((sent["code"], sent["address"]), (_CODE, "1"))
        self.assertEqual(sent["fields"], _FIELDS)
        self.assertEqual(sent["expect"], _EXPECT)

    def test_code_only_needs_no_expect(self) -> None:
        edit = _edit(fields={"code": "01"}, expect=None)
        _path, sent = self._send(lambda client: client.arc_customer_address(_call(), edit))
        self.assertNotIn("expect", sent)

    def test_empty_strings_and_false_are_sent(self) -> None:
        edit = _edit(fields={"name": "", "unit": "", "default": False}, expect={"name": "示例车间", "unit": "张三", "default": True})
        _path, sent = self._send(lambda client: client.arc_customer_address(_call(), edit))
        self.assertEqual(sent["fields"], {"name": "", "unit": "", "default": False})

    def test_dry_and_keyed_clients(self) -> None:
        _path, sent = self._send(lambda client: client.dry().arc_customer_address(_call(), _edit()))
        self.assertIs(sent["dry_run"], True)
        _path, sent = self._send(lambda client: client.keyed("cusaddr-1").arc_customer_address(_call(), _edit()))
        self.assertEqual(sent["idempotency_key"], "cusaddr-1")
        _path, sent = self._send(
            lambda client: client.arc_customer_address(_call(), _edit(), idempotency_key="cusaddr-2")
        )
        self.assertEqual(sent["idempotency_key"], "cusaddr-2")

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        call = _call()
        bad = (
            (_edit(code=""), "code"),
            (_edit(address=""), "address"),
            (_edit(address="A" * 31), "address"),
            (_edit(fields={}, expect=None), "fields"),
            (_edit(fields="name"), "fields"),
            (_edit(fields={"bdefault": True}), "bdefault"),
            (_edit(fields={"code": ""}, expect=None), "fields.code"),
            (_edit(fields={"code": " 01"}, expect=None), "fields.code"),
            (_edit(fields={"code": "A" * 31}, expect=None), "fields.code"),
            (_edit(fields={"name": "三" * 256}, expect={"name": "x"}), "fields.name"),
            (_edit(fields={"name": 1}, expect={"name": "x"}), "fields.name"),
            (_edit(fields={"unit": "张\n三"}, expect={"unit": "x"}), "fields.unit"),
            (_edit(fields={"default": "true"}, expect={"default": False}), "fields.default"),
            (_edit(fields={"default": 1}, expect={"default": False}), "fields.default"),
            (_edit(fields={"name": "示例车间"}, expect=None), "expect"),
            (_edit(fields={"name": "示例车间", "unit": "张三"}, expect={"name": "x"}), "unit"),
            (_edit(fields={"name": "示例车间"}, expect={"name": "x", "unit": "y"}), "unit"),
            (_edit(fields={"name": "示例车间"}, expect={"name": 1}), "expect.name"),
            (_edit(fields={"default": True}, expect={"default": "false"}), "expect.default"),
            (_edit(fields={"code": "01"}, expect={"code": "1"}), "code"),
        )
        for edit, word in bad:
            with self.subTest(word=word, fields=edit.fields), self.assertRaises(ValueError) as caught:
                client.arc_customer_address(call, edit)
            self.assertIn(word, str(caught.exception))

    def test_route_is_a_write(self) -> None:
        self.assertIn("/v1/archives/customer_address", WRITE_ROUTES)


class CliTests(unittest.TestCase):
    def _file(self, data: object) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, handle.name)
        with handle:
            json.dump(data, handle, ensure_ascii=False)
        return handle.name

    def test_command_takes_dry_run_and_key(self) -> None:
        self.assertIn("arc-customer-address", WRITE_COMMANDS)
        self.assertIn("arc-customer-address", KEY_COMMANDS)
        parsed = _parser().parse_args(
            _argv(
                "arc-customer-address", "--code", _CODE, "--address", "1", "--file", "edit.json",
                "--dry-run", "--idempotency-key", "k1",
            )
        )
        self.assertEqual((parsed.code, parsed.address, parsed.file), (_CODE, "1", "edit.json"))
        self.assertTrue(parsed.dry_run)
        self.assertEqual(parsed.idempotency_key, "k1")

    def test_main_builds_the_edit_from_the_file(self) -> None:
        seen: dict[str, object] = {}

        def _op(self: U8CoClient, call: U8Call, edit: CustomerAddressEdit, **extra: object) -> dict[str, object]:
            del call, extra
            seen["args"] = (edit, self._dry)
            return {"ok": True, "code": edit.code, "changed": True, "before": [], "after": []}

        path = self._file({"fields": _FIELDS, "expect": _EXPECT})
        argv = _argv("arc-customer-address", "--code", _CODE, "--address", "1", "--file", path, "--dry-run")
        self.assertEqual(_run_main(argv, "arc_customer_address", _op), 0)
        self.assertEqual(seen["args"], (_edit(), True))
        path = self._file({"fields": {"code": "01"}})
        argv = _argv("arc-customer-address", "--code", _CODE, "--address", "1", "--file", path)
        self.assertEqual(_run_main(argv, "arc_customer_address", _op), 0)
        self.assertEqual(seen["args"], (_edit(fields={"code": "01"}, expect=None), False))

    def test_file_without_fields_is_refused(self) -> None:
        path = self._file({"expect": _EXPECT})
        argv = _argv("arc-customer-address", "--code", _CODE, "--address", "1", "--file", path)
        with self.assertRaises(SystemExit):
            _run_main(argv, "arc_customer_address", lambda *_a, **_k: {})


if __name__ == "__main__":
    unittest.main()
