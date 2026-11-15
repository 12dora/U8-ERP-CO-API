"""写预演（client.dry() / --dry-run）、名称解析 resolve、幂等结果查询 idem-get、错误的 field / hint。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any
from unittest import mock

from co.client.tests.test_u8co_update_close_gen import (
    _AUTH,
    PASSWORD,
    SECRET,
    _argv,
    _assert_signed,
    make_call,
    _run_main,
    _Running,
)
from co.client.u8co_cli import _parser, main
from co.client.u8co_cli_assist import WRITE_COMMANDS, dry_client, parse_items
from co.client.u8co_client import U8Call, U8CoClient, VoucherDraft
from co.client.u8co_errors import U8CoBadRequest, U8CoError, error_from, parse_payload
from co.client.u8co_assist import bridge_route, dry_fields, idem_fields, resolve_fields


def _sent(bridge: _Running) -> dict[str, Any]:
    return json.loads(bridge.capture["body"].decode("utf-8"))


class DryRunTests(unittest.TestCase):
    def test_dry_client_adds_flag_and_leaves_original(self) -> None:
        with _Running() as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            preview = client.dry()
            self.assertIsNot(preview, client)
            preview.delete_voucher(make_call(9), "sale_order")
            sent = _sent(bridge)
            _assert_signed(self, bridge.capture)
            self.assertIs(sent["dry_run"], True)
            self.assertEqual(list(sent), _AUTH + ["type", "id", "dry_run"])
            client.delete_voucher(make_call(9), "sale_order")
            self.assertNotIn("dry_run", _sent(bridge))

    def test_dry_create_and_close(self) -> None:
        with _Running() as bridge:
            preview = U8CoClient(bridge.base_url, SECRET, timeout=5).dry()
            preview.create_voucher(make_call(), VoucherDraft("other_in", {"cWhCode": "01"}, [{"cInvCode": "A"}]))
            self.assertEqual(bridge.capture["path"], "/u8co/v1/vouchers/create")
            self.assertIs(_sent(bridge)["dry_run"], True)
            preview.close_voucher(make_call(3), "sale_order", "close", [1, 2])
            sent = _sent(bridge)
            self.assertEqual((sent["line_ids"], sent["dry_run"]), ([1, 2], True))

    def test_dry_refuses_legacy_and_idempotency_locally(self) -> None:
        with self.assertRaises(ValueError) as legacy:
            dry_fields("/v1/sale-orders/verify", {"id": 1})
        self.assertIn("旧路由", str(legacy.exception))
        with self.assertRaises(ValueError) as keyed:
            dry_fields("/v1/vouchers/create", {"idempotency_key": "k1"})
        self.assertIn("幂等键", str(keyed.exception))
        preview = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5).dry()
        with self.assertRaises(ValueError):
            preview.verify_sale_order(U8Call("998", "2026", "op001", PASSWORD, "2026-09-26", 1, "verify"))
        draft = VoucherDraft("other_in", {"cWhCode": "01"}, [{"cInvCode": "A"}])
        with self.assertRaises(ValueError):
            preview.create_voucher(make_call(), draft, idempotency_key="k1")

    def test_dry_client_leaves_reads_alone(self) -> None:
        with _Running() as bridge:
            preview = U8CoClient(bridge.base_url, SECRET, timeout=5).dry()
            preview.load_voucher(make_call(9), "sale_order")
            self.assertNotIn("dry_run", _sent(bridge))
            preview.resolve(make_call(), [{"archive": "customer", "q": "A"}])
            self.assertNotIn("dry_run", _sent(bridge))
        self.assertEqual(dry_fields("/v1/vouchers/load", {"a": 1}), {"a": 1})
        for route in ("/v1/gl/vouchers/void", "/v1/archives/delete", "/v1/workflow/submit", "/v1/arap/voucher/delete"):
            self.assertIs(dry_fields(route, {})["dry_run"], True, route)

    def test_dry_fields_copies(self) -> None:
        fields = {"type": "sale_order"}
        got = dry_fields("/v1/vouchers/delete", fields)
        self.assertEqual(got, {"type": "sale_order", "dry_run": True})
        self.assertNotIn("dry_run", fields)

    def test_flag_only_on_write_commands(self) -> None:
        parsed = _parser().parse_args(_argv("delete", "--type", "sale_order", "--id", "5", "--dry-run"))
        self.assertTrue(parsed.dry_run)
        plain = _parser().parse_args(_argv("delete", "--type", "sale_order", "--id", "5"))
        self.assertFalse(plain.dry_run)
        for name in ("gl-void", "arc-create", "wf-submit", "lock", "generate"):
            self.assertIn(name, WRITE_COMMANDS)
        reads = (
            ("load", "--type", "sale_order", "--id", "5"),
            ("sale-order", "--id", "5", "--action", "verify"),
            ("list", "--type", "sale_order"),
        )
        for name, *extra in reads:
            with self.subTest(name=name), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                _parser().parse_args(_argv(name, *extra, "--dry-run"))

    def test_dry_client_refuses_key(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        parsed = _parser().parse_args(
            _argv("create", "--type", "other_in", "--file", "x.json", "--dry-run", "--idempotency-key", "k1")
        )
        with self.assertRaises(SystemExit):
            dry_client(client, parsed)
        parsed = _parser().parse_args(_argv("delete", "--type", "sale_order", "--id", "5", "--dry-run"))
        self.assertTrue(dry_client(client, parsed)._dry)
        parsed = _parser().parse_args(_argv("delete", "--type", "sale_order", "--id", "5"))
        self.assertIs(dry_client(client, parsed), client)

    def test_main_passes_dry_run(self) -> None:
        seen: dict[str, Any] = {}

        def _delete(self: U8CoClient, call: U8Call, kind: str) -> dict[str, Any]:
            seen["dry"] = self._dry
            return {"ok": True, "dry_run": True}

        code = _run_main(_argv("delete", "--type", "sale_order", "--id", "5", "--dry-run"), "delete_voucher", _delete)
        self.assertEqual((code, seen["dry"]), (0, True))


class ResolveTests(unittest.TestCase):
    def test_wire_body(self) -> None:
        with _Running() as bridge:
            items = [{"archive": "customer", "q": "张三贸易"}, {"archive": "inventory", "q": "示例存货X1"}]
            U8CoClient(bridge.base_url, SECRET, timeout=5).resolve(make_call(), items, 5, True)
            sent = _sent(bridge)
            _assert_signed(self, bridge.capture)
            self.assertEqual(bridge.capture["path"], "/u8co/v1/archives/resolve")
            self.assertEqual(list(sent), _AUTH + ["items", "limit", "include_disabled"])
            self.assertEqual(sent["items"], items)
            self.assertEqual((sent["limit"], sent["include_disabled"]), (5, True))

    def test_defaults_omitted(self) -> None:
        fields = resolve_fields(make_call(), [{"archive": "customer", "q": "A"}], None, False)
        self.assertNotIn("limit", fields)
        self.assertNotIn("include_disabled", fields)

    def test_rejects_bad_items(self) -> None:
        for items in ([], "customer", [{"archive": "customer"}], [{"archive": "", "q": "A"}],
                      [{"archive": "customer", "q": "  "}], [{"archive": "customer", "q": "x" * 101}],
                      [{"archive": "customer", "q": "A"}] * 21):
            with self.subTest(items=items), self.assertRaises(ValueError):
                resolve_fields(make_call(), items, None, False)
        for limit in (0, 21, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                resolve_fields(make_call(), [{"archive": "customer", "q": "A"}], limit, False)

    def test_parse_items(self) -> None:
        self.assertEqual(parse_items(["customer=张三=贸易", " inventory = X1 "]),
                         [{"archive": "customer", "q": "张三=贸易"}, {"archive": "inventory", "q": "X1"}])
        for bad in (["customer"], ["=A"], ["customer= "]):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                parse_items(bad)

    def test_main_wires_resolve(self) -> None:
        seen: dict[str, Any] = {}

        def _resolve(self: U8CoClient, call: U8Call, items: object, limit: object, disabled: bool) -> dict[str, Any]:
            seen["args"] = (items, limit, disabled)
            return {"ok": True, "results": []}

        argv = _argv("resolve", "--item", "customer=张三", "--item", "vendor=李四", "--limit", "3")
        self.assertEqual(_run_main(argv, "resolve", _resolve), 0)
        items = [{"archive": "customer", "q": "张三"}, {"archive": "vendor", "q": "李四"}]
        self.assertEqual(seen["args"], (items, 3, False))


class IdemGetTests(unittest.TestCase):
    def test_wire_body(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).idempotency_get(make_call(), "/v1/gl/vouchers/create", "k-1")
            sent = _sent(bridge)
            self.assertEqual(bridge.capture["path"], "/u8co/v1/idempotency/get")
            self.assertEqual(list(sent), _AUTH + ["route", "idempotency_key"])
            self.assertEqual((sent["route"], sent["idempotency_key"]), ("/u8co/v1/gl/vouchers/create", "k-1"))

    def test_routes_and_caller(self) -> None:
        self.assertEqual(bridge_route("/u8co/v1/vouchers/create"), "/u8co/v1/vouchers/create")
        self.assertEqual(bridge_route("/v1/archives/create"), "/u8co/v1/archives/create")
        for bad in ("/v1/vouchers/load", "vouchers/create", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                bridge_route(bad)
        self.assertEqual(idem_fields(make_call(), "/v1/vouchers/create", "k", "app-1")["caller"], "app-1")
        for caller in ("", "x" * 201):
            with self.subTest(caller=caller), self.assertRaises(ValueError):
                idem_fields(make_call(), "/v1/vouchers/create", "k", caller)
        with self.assertRaises(ValueError):
            idem_fields(make_call(), "/v1/vouchers/create", "bad key", None)

    def test_main_wires_idem_get(self) -> None:
        seen: dict[str, Any] = {}

        def _get(self: U8CoClient, call: U8Call, route: str, key: str, caller: object) -> dict[str, Any]:
            seen["args"] = (route, key, caller)
            return {"ok": True, "found": False}

        argv = _argv("idem-get", "--route", "/v1/vouchers/generate", "--key", "k-9")
        self.assertEqual(_run_main(argv, "idempotency_get", _get), 0)
        self.assertEqual(seen["args"], ("/v1/vouchers/generate", "k-9", None))


class ErrorFieldTests(unittest.TestCase):
    def test_field_and_hint_kept(self) -> None:
        exc = error_from(400, {"code": "bad_request", "message": "含未知字段", "field": "lines.0.x", "hint": "见 meta"})
        self.assertIsInstance(exc, U8CoBadRequest)
        self.assertEqual((exc.field, exc.hint), ("lines.0.x", "见 meta"))
        self.assertEqual(exc.describe(), "bad_request: 含未知字段（字段 lines.0.x）\n提示：见 meta")

    def test_missing_or_bad_values_empty(self) -> None:
        exc = error_from(400, {"code": "bad_request", "message": "x", "field": 3, "hint": "h" * 301})
        self.assertEqual((exc.field, exc.hint), ("", ""))
        self.assertEqual(exc.describe(), "bad_request: x")
        plain = U8CoError(409, "state_mismatch", "m")
        self.assertEqual((plain.field, plain.hint), ("", ""))

    def test_parse_payload_carries_field(self) -> None:
        raw = json.dumps({"ok": False, "code": "bad_request", "message": "缺少字段 lines", "field": "lines"}).encode()
        with self.assertRaises(U8CoBadRequest) as caught:
            parse_payload(400, raw)
        self.assertEqual(caught.exception.field, "lines")

    def test_main_prints_field_and_hint(self) -> None:
        def _load(self: U8CoClient, call: U8Call, kind: str) -> dict[str, Any]:
            raise U8CoBadRequest(400, "bad_request", "含未知字段", "zz", "可写字段见 /v1/co/meta")

        stderr = io.StringIO()
        with (
            mock.patch("co.client.u8co_cli._secret", return_value=SECRET),
            mock.patch("co.client.u8co_cli._password", return_value=PASSWORD),
            mock.patch("co.client.u8co_cli.U8CoClient.load_voucher", _load),
            contextlib.redirect_stderr(stderr),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = main(_argv("load", "--type", "sale_order", "--id", "1"))
        self.assertEqual(code, 1)
        self.assertIn("（字段 zz）", stderr.getvalue())
        self.assertIn("提示：可写字段见 /v1/co/meta", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
