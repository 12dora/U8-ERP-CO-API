"""客户开票单位 arc_customer_invoice_company_add / _delete / arc_customer_invoice_company：正文、预演、幂等键、本地校验和命令行。
只监听 127.0.0.1。
"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_gl_arc import _call
from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _assert_signed, _argv, _run_main, _Running
from co.client.u8co_arc_invoice import CustomerInvoiceAdd, CustomerInvoiceRow
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_idem import WRITE_ROUTES

_CODE = "C900001:C900002"
_ROUTES = (
    ("arc_customer_invoice_company_add", "customer_invoice_company_add"),
    ("arc_customer_invoice_company_delete", "customer_invoice_company_delete"),
    ("arc_customer_invoice_company", "customer_invoice_company"),
)


def _add(**over: Any) -> CustomerInvoiceAdd:
    values: dict[str, Any] = {"code": _CODE}
    values.update(over)
    return CustomerInvoiceAdd(**values)


def _row(**over: Any) -> CustomerInvoiceRow:
    values: dict[str, Any] = {"code": _CODE, "bdefault": False}
    values.update(over)
    return CustomerInvoiceRow(**values)


class ArcCustomerInvoiceTests(unittest.TestCase):
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
        path, sent = self._send(lambda c: c.arc_customer_invoice_company_add(_call(), _add()))
        self.assertEqual(path, "/u8co/v1/archives/customer_invoice_company_add")
        self.assertEqual(list(sent), _AUTH + ["code"])
        self.assertEqual(sent["code"], _CODE)

    def test_add_default_and_ufts(self) -> None:
        add = _add(default=True, customer_ufts="7891234")
        _path, sent = self._send(lambda c: c.arc_customer_invoice_company_add(_call(), add))
        self.assertEqual(list(sent), _AUTH + ["code", "default", "expect"])
        self.assertIs(sent["default"], True)
        self.assertEqual(sent["expect"], {"customer_ufts": "7891234"})
        _path, sent = self._send(lambda c: c.arc_customer_invoice_company_add(_call(), _add(default=False)))
        self.assertIs(sent["default"], False)

    def test_delete_and_default_bodies(self) -> None:
        for method, route in _ROUTES[1:]:
            path, sent = self._send(lambda c: getattr(c, method)(_call(), _row()))
            self.assertEqual(path, "/u8co/v1/archives/" + route)
            self.assertEqual(list(sent), _AUTH + ["code", "expect"])
            self.assertEqual(sent["expect"], {"bdefault": False})
            _path, sent = self._send(lambda c: getattr(c, method)(_call(), _row(bdefault=True, customer_ufts="55")))
            self.assertEqual(sent["expect"], {"bdefault": True, "customer_ufts": "55"})

    def test_dry_and_keyed_clients(self) -> None:
        for method, _route in _ROUTES:
            arg = _add() if method.endswith("_add") else _row()
            _path, sent = self._send(lambda c: getattr(c.dry(), method)(_call(), arg))
            self.assertIs(sent["dry_run"], True)
            _path, sent = self._send(lambda c: getattr(c.keyed("inv-1"), method)(_call(), arg))
            self.assertEqual(sent["idempotency_key"], "inv-1")
            _path, sent = self._send(lambda c: getattr(c, method)(_call(), arg, idempotency_key="inv-2"))
            self.assertEqual(sent["idempotency_key"], "inv-2")

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad_codes = (
            "", "C900001", "C900001:", ":C900002", "C900001: C900002",
            "C900001:" + "C" * 21, "C" * 21 + ":C900002", "C9\n:C2",
        )
        for code in bad_codes:
            with self.subTest(code=code), self.assertRaises(ValueError):
                client.arc_customer_invoice_company_add(_call(), _add(code=code))
            with self.subTest(code=code), self.assertRaises(ValueError):
                client.arc_customer_invoice_company(_call(), _row(code=code))
        for add, word in (
            (_add(default="true"), "default"),
            (_add(default=1), "default"),
            (_add(customer_ufts="12a"), "customer_ufts"),
            (_add(customer_ufts=""), "customer_ufts"),
            (_add(customer_ufts="9" * 20), "customer_ufts"),
        ):
            with self.subTest(word=word, add=add), self.assertRaises(ValueError) as caught:
                client.arc_customer_invoice_company_add(_call(), add)
            self.assertIn(word, str(caught.exception))
        for row, word in (
            (_row(bdefault="false"), "bdefault"),
            (_row(bdefault=None), "bdefault"),
            (_row(bdefault=0), "bdefault"),
            (_row(customer_ufts=5), "customer_ufts"),
        ):
            with self.subTest(word=word, row=row), self.assertRaises(ValueError) as caught:
                client.arc_customer_invoice_company_delete(_call(), row)
            self.assertIn(word, str(caught.exception))

    def test_routes_are_writes(self) -> None:
        for _method, route in _ROUTES:
            self.assertIn("/v1/archives/" + route, WRITE_ROUTES)


class CliTests(unittest.TestCase):
    def test_commands_take_dry_run_and_key(self) -> None:
        names = (
            "arc-customer-invoice-company-add",
            "arc-customer-invoice-company-delete",
            "arc-customer-invoice-company",
        )
        for name in names:
            self.assertIn(name, WRITE_COMMANDS)
            self.assertIn(name, KEY_COMMANDS)
            extra = [] if name.endswith("-add") else ["--expect-bdefault", "true"]
            parsed = _parser().parse_args(
                _argv(name, "--code", _CODE, *extra, "--customer-ufts", "55", "--dry-run", "--idempotency-key", "k1")
            )
            self.assertEqual((parsed.code, parsed.customer_ufts), (_CODE, "55"))
            self.assertTrue(parsed.dry_run)
            self.assertEqual(parsed.idempotency_key, "k1")

    def test_expect_bdefault_is_required_and_boolean(self) -> None:
        for name in ("arc-customer-invoice-company-delete", "arc-customer-invoice-company"):
            with self.subTest(name=name, case="missing"), self.assertRaises(SystemExit):
                _parser().parse_args(_argv(name, "--code", _CODE))
            with self.subTest(name=name, case="bad"), self.assertRaises(SystemExit):
                _parser().parse_args(_argv(name, "--code", _CODE, "--expect-bdefault", "yes"))

    def test_main_builds_the_requests(self) -> None:
        seen: dict[str, object] = {}

        def _op(self: U8CoClient, call: U8Call, arg: object, **extra: object) -> dict[str, object]:
            del call, extra
            seen["args"] = (arg, self._dry)
            return {"ok": True, "code": _CODE, "changed": True, "before": [], "after": []}

        argv = _argv("arc-customer-invoice-company-add", "--code", _CODE, "--default", "--dry-run")
        self.assertEqual(_run_main(argv, "arc_customer_invoice_company_add", _op), 0)
        self.assertEqual(seen["args"], (_add(default=True), True))
        argv = _argv("arc-customer-invoice-company-delete", "--code", _CODE, "--expect-bdefault", "false")
        self.assertEqual(_run_main(argv, "arc_customer_invoice_company_delete", _op), 0)
        self.assertEqual(seen["args"], (_row(), False))
        argv = _argv(
            "arc-customer-invoice-company", "--code", _CODE, "--expect-bdefault", "true", "--customer-ufts", "9"
        )
        self.assertEqual(_run_main(argv, "arc_customer_invoice_company", _op), 0)
        self.assertEqual(seen["args"], (_row(bdefault=True, customer_ufts="9"), False))


if __name__ == "__main__":
    unittest.main()
