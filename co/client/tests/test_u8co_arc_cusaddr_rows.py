"""客户收货地址新增 / 删除 arc_customer_address_add、arc_customer_address_delete：正文、预演、幂等键、本地校验和命令行。
只监听 127.0.0.1。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_gl_arc import _call
from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _assert_signed, _argv, _run_main, _Running
from co.client.u8co_arc_cus import CustomerAddressAdd, CustomerAddressDelete
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_idem import WRITE_ROUTES

_CODE = "C900001"
_FIELDS = {"name": "示例车间二", "unit": "张三", "default": True}
_EXPECT = {"name": "示例车间二", "unit": "张三", "default": True}


def _add(**over: Any) -> CustomerAddressAdd:
    values: dict[str, Any] = {"code": _CODE, "address": "02", "fields": dict(_FIELDS)}
    values.update(over)
    return CustomerAddressAdd(**values)


def _drop(**over: Any) -> CustomerAddressDelete:
    values: dict[str, Any] = {"code": _CODE, "address": "02", "expect": dict(_EXPECT)}
    values.update(over)
    return CustomerAddressDelete(**values)


class ArcCustomerAddressRowsTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_add_body(self) -> None:
        path, sent = self._send(lambda client: client.arc_customer_address_add(_call(), _add()))
        self.assertEqual(path, "/u8co/v1/archives/customer_address_add")
        self.assertEqual(list(sent), _AUTH + ["code", "address", "fields"])
        self.assertEqual((sent["code"], sent["address"]), (_CODE, "02"))
        self.assertEqual(sent["fields"], _FIELDS)

    def test_add_name_only_and_false_default(self) -> None:
        _path, sent = self._send(lambda c: c.arc_customer_address_add(_call(), _add(fields={"name": "示例车间二"})))
        self.assertEqual(sent["fields"], {"name": "示例车间二"})
        fields = {"name": "示例车间二", "unit": "", "default": False}
        _path, sent = self._send(lambda c: c.arc_customer_address_add(_call(), _add(fields=fields)))
        self.assertEqual(sent["fields"], fields)

    def test_delete_body(self) -> None:
        path, sent = self._send(lambda client: client.arc_customer_address_delete(_call(), _drop()))
        self.assertEqual(path, "/u8co/v1/archives/customer_address_delete")
        self.assertEqual(list(sent), _AUTH + ["code", "address", "expect"])
        self.assertEqual(sent["expect"], _EXPECT)

    def test_delete_accepts_empty_current_values(self) -> None:
        expect = {"name": "", "unit": "", "default": False}
        _path, sent = self._send(lambda c: c.arc_customer_address_delete(_call(), _drop(expect=expect)))
        self.assertEqual(sent["expect"], expect)

    def test_dry_and_keyed_clients(self) -> None:
        for method, arg in (("arc_customer_address_add", _add()), ("arc_customer_address_delete", _drop())):
            _path, sent = self._send(lambda c: getattr(c.dry(), method)(_call(), arg))
            self.assertIs(sent["dry_run"], True)
            _path, sent = self._send(lambda c: getattr(c.keyed("rows-1"), method)(_call(), arg))
            self.assertEqual(sent["idempotency_key"], "rows-1")
            _path, sent = self._send(lambda c: getattr(c, method)(_call(), arg, idempotency_key="rows-2"))
            self.assertEqual(sent["idempotency_key"], "rows-2")

    def test_add_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad = (
            (_add(code=""), "code"),
            (_add(address=""), "address"),
            (_add(address="A" * 31), "address"),
            (_add(address=" 02"), "address"),
            (_add(address="02 "), "address"),
            (_add(fields={}), "fields"),
            (_add(fields="name"), "fields"),
            (_add(fields={"unit": "张三"}), "name"),
            (_add(fields={"name": ""}), "name"),
            (_add(fields={"name": "   "}), "name"),
            (_add(fields={"name": "三" * 256}), "fields.name"),
            (_add(fields={"name": 1}), "fields.name"),
            (_add(fields={"name": "x", "unit": "张\n三"}), "fields.unit"),
            (_add(fields={"name": "x", "default": "true"}), "fields.default"),
            (_add(fields={"name": "x", "default": 1}), "fields.default"),
            (_add(fields={"name": "x", "code": "03"}), "code"),
        )
        for add, word in bad:
            with self.subTest(word=word, fields=add.fields), self.assertRaises(ValueError) as caught:
                client.arc_customer_address_add(_call(), add)
            self.assertIn(word, str(caught.exception))

    def test_delete_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad = (
            (_drop(code=""), "code"),
            (_drop(address=""), "address"),
            (_drop(address="A" * 31), "address"),
            (_drop(expect={}), "expect"),
            (_drop(expect="x"), "expect"),
            (_drop(expect={"name": "x", "unit": ""}), "default"),
            (_drop(expect={"name": "x", "default": False}), "unit"),
            (_drop(expect={"unit": "", "default": False}), "name"),
            (_drop(expect={"name": "x", "unit": "", "default": False, "code": "02"}), "code"),
            (_drop(expect={"name": 1, "unit": "", "default": False}), "expect.name"),
            (_drop(expect={"name": "x", "unit": "", "default": "false"}), "expect.default"),
            (_drop(expect={"name": "x" * 256, "unit": "", "default": False}), "expect.name"),
        )
        for drop, word in bad:
            with self.subTest(word=word, expect=drop.expect), self.assertRaises(ValueError) as caught:
                client.arc_customer_address_delete(_call(), drop)
            self.assertIn(word, str(caught.exception))

    def test_routes_are_writes(self) -> None:
        for route in ("customer_address_add", "customer_address_delete"):
            self.assertIn("/v1/archives/" + route, WRITE_ROUTES)


class CliTests(unittest.TestCase):
    def _file(self, data: object) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, handle.name)
        with handle:
            json.dump(data, handle, ensure_ascii=False)
        return handle.name

    def test_commands_take_dry_run_and_key(self) -> None:
        for name in ("arc-customer-address-add", "arc-customer-address-delete"):
            self.assertIn(name, WRITE_COMMANDS)
            self.assertIn(name, KEY_COMMANDS)
            parsed = _parser().parse_args(
                _argv(name, "--code", _CODE, "--address", "02", "--file", "x.json", "--dry-run", "--idempotency-key", "k1")
            )
            self.assertEqual((parsed.code, parsed.address, parsed.file), (_CODE, "02", "x.json"))
            self.assertTrue(parsed.dry_run)
            self.assertEqual(parsed.idempotency_key, "k1")

    def test_main_add_builds_the_request_from_the_file(self) -> None:
        seen: dict[str, object] = {}

        def _op(self: U8CoClient, call: U8Call, add: CustomerAddressAdd, **extra: object) -> dict[str, object]:
            del call, extra
            seen["args"] = (add, self._dry)
            return {"ok": True, "code": add.code, "changed": True, "before": [], "after": []}

        path = self._file({"fields": _FIELDS})
        argv = _argv("arc-customer-address-add", "--code", _CODE, "--address", "02", "--file", path, "--dry-run")
        self.assertEqual(_run_main(argv, "arc_customer_address_add", _op), 0)
        self.assertEqual(seen["args"], (_add(), True))

    def test_main_delete_builds_the_request_from_the_file(self) -> None:
        seen: dict[str, object] = {}

        def _op(self: U8CoClient, call: U8Call, drop: CustomerAddressDelete, **extra: object) -> dict[str, object]:
            del call, extra
            seen["args"] = (drop, self._dry)
            return {"ok": True, "code": drop.code, "changed": True, "before": [], "after": []}

        path = self._file({"expect": _EXPECT})
        argv = _argv("arc-customer-address-delete", "--code", _CODE, "--address", "02", "--file", path)
        self.assertEqual(_run_main(argv, "arc_customer_address_delete", _op), 0)
        self.assertEqual(seen["args"], (_drop(), False))

    def test_wrong_file_keys_are_refused(self) -> None:
        for name, data in (
            ("arc-customer-address-add", {"expect": _EXPECT}),
            ("arc-customer-address-add", {"fields": _FIELDS, "expect": _EXPECT}),
            ("arc-customer-address-delete", {"fields": _FIELDS}),
            ("arc-customer-address-delete", {"expect": _EXPECT, "fields": _FIELDS}),
        ):
            argv = _argv(name, "--code", _CODE, "--address", "02", "--file", self._file(data))
            with self.subTest(name=name, keys=sorted(data)), self.assertRaises(SystemExit):
                _run_main(argv, name.replace("-", "_"), lambda *_a, **_k: {})


if __name__ == "__main__":
    unittest.main()
