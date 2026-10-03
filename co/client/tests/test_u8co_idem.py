"""幂等键：库方法把 idempotency_key 放进签名正文，命令行 --idempotency-key 传到库方法；全部写路由都可带键
（client.keyed(key)、全部写命令的 --idempotency-key）。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _run_main, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherDraft, VoucherGen
from co.client.u8co_idem import IDEMPOTENT_ROUTES, WRITE_ROUTES, check_key, key_fields, with_key
from co.client.u8co_cli_assist import KEY_COMMANDS, dry_client
from co.client.u8co_assist import WRITE_ROUTES as ASSIST_WRITE_ROUTES
from co.client.u8co_gl_arc import ArcRecord, GlDraft
from co.client.tests.test_u8co_gl_arc import _LINES, _call

_KEY = "order-2026-0001"


class KeyTests(unittest.TestCase):
    def test_valid_keys(self) -> None:
        self.assertEqual(check_key(_KEY), _KEY)
        self.assertEqual(check_key("~" * 128), "~" * 128)

    def test_bad_keys(self) -> None:
        for bad in ("", "a b", "k" * 129, "键", 5, None):
            with self.assertRaises(ValueError):
                check_key(bad)

    def test_with_key_leaves_fields_alone_without_key(self) -> None:
        self.assertEqual(with_key({"a": 1}, None), {"a": 1})
        self.assertEqual(with_key({"a": 1}, "k"), {"a": 1, "idempotency_key": "k"})

    def test_routes_are_the_write_routes(self) -> None:
        # 唯一来源是写路由表（同桥 WriteGate.Writes），u8co_assist 照旧导出同一个对象。
        self.assertIs(IDEMPOTENT_ROUTES, WRITE_ROUTES)
        self.assertIs(ASSIST_WRITE_ROUTES, WRITE_ROUTES)
        # 含 openings/post、periods/close、ia/post、ia/period_end、openings/arap、应收应付处理 7 个、票据登记 / 删除 / 处理 3 个、
        # arap/bad_debt、gl/vouchers/unpost、gl/vouchers/reverse、gl/transfer/pnl、gl/transfer/custom。
        self.assertEqual(len(WRITE_ROUTES), 54)
        self.assertIn("/v1/arap/bad_debt", WRITE_ROUTES)
        for route in ("/v1/notes/create", "/v1/notes/delete", "/v1/notes/process"):
            self.assertIn(route, WRITE_ROUTES)
        for route in ("/v1/vouchers/create", "/v1/vouchers/verify", "/v1/vouchers/delete", "/v1/gl/vouchers/void",
                      "/v1/workflow/submit", "/v1/arap/writeoff/auto", "/v1/sale-orders/verify"):
            self.assertIn(route, IDEMPOTENT_ROUTES)
        for route in ("/v1/vouchers/load", "/v1/idempotency/get", "/v1/vouchers/search", "/v1/meta/fields"):
            self.assertNotIn(route, IDEMPOTENT_ROUTES)

    def test_key_fields(self) -> None:
        self.assertEqual(key_fields("/v1/vouchers/verify", {"id": 1}, _KEY), {"id": 1, "idempotency_key": _KEY})
        self.assertEqual(key_fields("/v1/vouchers/load", {"id": 1}, _KEY), {"id": 1})
        self.assertEqual(key_fields("/v1/vouchers/create", {"idempotency_key": _KEY}, _KEY), {"idempotency_key": _KEY})
        with self.assertRaises(ValueError):
            key_fields("/v1/vouchers/create", {"idempotency_key": "other"}, _KEY)
        with self.assertRaises(ValueError) as plan:
            key_fields("/v1/arap/writeoff/auto", {"flag": "AR", "dry_run": True}, _KEY)
        self.assertIn("试算", str(plan.exception))
        self.assertEqual(key_fields("/v1/arap/writeoff/auto", {"dry_run": False}, _KEY)["idempotency_key"], _KEY)


class WireTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        return captured["path"], json.loads(captured["body"].decode("utf-8"))

    def test_create_carries_the_key_last(self) -> None:
        draft = VoucherDraft("sale_order", {"cCusCode": "C001"}, [{"cInvCode": "A", "iQuantity": 1}])
        path, sent = self._send(lambda client: client.create_voucher(_call(), draft, idempotency_key=_KEY))
        self.assertEqual(path, "/u8co/v1/vouchers/create")
        self.assertEqual(list(sent), _AUTH + ["type", "head", "lines", "idempotency_key"])
        self.assertEqual(sent["idempotency_key"], _KEY)
        self.assertNotIn("caller", sent)

    def test_create_without_key_is_unchanged(self) -> None:
        draft = VoucherDraft("sale_order", {"cCusCode": "C001"}, [{"cInvCode": "A", "iQuantity": 1}])
        _path, sent = self._send(lambda client: client.create_voucher(_call(), draft))
        self.assertEqual(list(sent), _AUTH + ["type", "head", "lines"])

    def test_generate_gl_and_archive(self) -> None:
        call = U8Call("801", "2026", "op001", "pw", "2026-09-27", 9)
        gen = VoucherGen("sale_out", None, None)
        path, sent = self._send(lambda client: client.generate_voucher(call, gen, idempotency_key=_KEY))
        self.assertEqual(path, "/u8co/v1/vouchers/generate")
        self.assertEqual(sent["idempotency_key"], _KEY)
        draft = GlDraft({"sign": "转", "date": "2026-09-27"}, _LINES)
        path, sent = self._send(lambda client: client.gl_create(_call(), draft, idempotency_key=_KEY))
        self.assertEqual(path, "/u8co/v1/gl/vouchers/create")
        self.assertEqual(sent["idempotency_key"], _KEY)
        record = ArcRecord("customer", "C900001", {"name": "客户甲"})
        path, sent = self._send(lambda client: client.arc_create(_call(), record, idempotency_key=_KEY))
        self.assertEqual(path, "/u8co/v1/archives/create")
        self.assertEqual(sent["idempotency_key"], _KEY)

    def test_keyed_client_on_every_write(self) -> None:
        call = U8Call("801", "2026", "op001", "pw", "2026-09-27", 9, "verify")
        path, sent = self._send(lambda client: client.keyed(_KEY).verify_voucher(call, "sale_order"))
        self.assertEqual(path, "/u8co/v1/vouchers/verify")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "action", "idempotency_key"])
        path, sent = self._send(lambda client: client.keyed(_KEY).delete_voucher(call, "sale_order"))
        self.assertEqual((path, sent["idempotency_key"]), ("/u8co/v1/vouchers/delete", _KEY))
        path, sent = self._send(lambda client: client.keyed(_KEY).verify_sale_order(call))
        self.assertEqual((path, sent["idempotency_key"]), ("/u8co/v1/sale-orders/verify", _KEY))
        draft = VoucherDraft("sale_order", {"cCusCode": "C001"}, [{"cInvCode": "A", "iQuantity": 1}])
        path, sent = self._send(lambda client: client.keyed(_KEY).create_voucher(_call(), draft, idempotency_key=_KEY))
        self.assertEqual(sent["idempotency_key"], _KEY)
        _path, sent = self._send(lambda client: client.keyed(_KEY).load_voucher(call, "sale_order"))
        self.assertNotIn("idempotency_key", sent)

    def test_keyed_client_leaves_original(self) -> None:
        client = U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)
        keyed = client.keyed(_KEY)
        self.assertIsNot(keyed, client)
        self.assertEqual((keyed._idem_key, client._idem_key), (_KEY, None))
        with self.assertRaises(ValueError):
            client.keyed("a b")
        # 带键的客户端再预演：本地拒绝，不发请求。
        with self.assertRaises(ValueError):
            keyed.dry().delete_voucher(U8Call("801", "2026", "op001", "pw", "2026-09-27", 9), "sale_order")

    def test_bad_key_never_leaves_the_process(self) -> None:
        client = U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)
        draft = VoucherDraft("sale_order", {"cCusCode": "C001"}, [{"cInvCode": "A"}])
        with self.assertRaises(ValueError):
            client.create_voucher(_call(), draft, idempotency_key="a b")


class CliTests(unittest.TestCase):
    def test_flag_on_every_write_command(self) -> None:
        created = _parser().parse_args(
            _argv("create", "--type", "sale_order", "--file", "x", "--idempotency-key", _KEY)
        )
        self.assertEqual(created.idempotency_key, _KEY)
        generated = _parser().parse_args(_argv("generate", "--type", "dispatch", "--id", "5", "--file", "x"))
        self.assertIsNone(generated.idempotency_key)
        gl = _parser().parse_args(_argv("gl-create", "--file", "x", "--idempotency-key", _KEY))
        self.assertEqual(gl.idempotency_key, _KEY)
        arc = _parser().parse_args(
            _argv("arc-create", "--archive", "customer", "--code", "C1", "--file", "x", "--idempotency-key", _KEY)
        )
        self.assertEqual(arc.idempotency_key, _KEY)
        deleted = _parser().parse_args(_argv("delete", "--type", "sale_order", "--id", "5", "--idempotency-key", _KEY))
        self.assertEqual(deleted.idempotency_key, _KEY)
        legacy = _parser().parse_args(
            _argv("sale-order", "--id", "5", "--action", "verify", "--idempotency-key", _KEY)
        )
        self.assertEqual(legacy.idempotency_key, _KEY)
        parser = _parser()
        subs = next(action for action in parser._actions if action.dest == "command").choices
        for name in KEY_COMMANDS:
            self.assertIn("--idempotency-key", subs[name]._option_string_actions, name)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            _parser().parse_args(_argv("load", "--type", "sale_order", "--id", "5", "--idempotency-key", _KEY))

    def test_dry_client_keys(self) -> None:
        client = U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)
        parsed = _parser().parse_args(_argv("verify", "--type", "sale_order", "--id", "5", "--action", "verify",
                                            "--idempotency-key", _KEY))
        self.assertEqual(dry_client(client, parsed)._idem_key, _KEY)
        parsed = _parser().parse_args(_argv("verify", "--type", "sale_order", "--id", "5", "--action", "verify"))
        self.assertIs(dry_client(client, parsed), client)

    def test_main_passes_the_key(self) -> None:
        seen: dict[str, Any] = {}

        def _create(self: U8CoClient, call: U8Call, draft: VoucherDraft, idempotency_key: str | None = None):
            seen["create"] = idempotency_key
            return {"ok": True}

        def _gl(self: U8CoClient, call: U8Call, draft: GlDraft, idempotency_key: str | None = None):
            seen["gl"] = idempotency_key
            return {"ok": True}

        with _JsonFile({"head": {"cCusCode": "C001"}, "lines": [{"cInvCode": "A"}]}) as path:
            argv = _argv("create", "--type", "sale_order", "--file", path, "--idempotency-key", _KEY)
            self.assertEqual(_run_main(argv, "create_voucher", _create), 0)
        self.assertEqual(seen["create"], _KEY)
        with _JsonFile({"head": {"sign": "转"}, "lines": _LINES}) as path:
            self.assertEqual(_run_main(_argv("gl-create", "--file", path), "gl_create", _gl), 0)
        self.assertIsNone(seen["gl"])

    def test_main_keys_other_writes(self) -> None:
        seen: dict[str, Any] = {}

        def _delete(self: U8CoClient, call: U8Call, kind: str) -> dict[str, Any]:
            seen["key"] = self._idem_key
            return {"ok": True}

        argv = _argv("delete", "--type", "sale_order", "--id", "5", "--idempotency-key", _KEY)
        self.assertEqual(_run_main(argv, "delete_voucher", _delete), 0)
        self.assertEqual(seen["key"], _KEY)
        self.assertEqual(_run_main(_argv("delete", "--type", "sale_order", "--id", "5"), "delete_voucher", _delete), 0)
        self.assertIsNone(seen["key"])


if __name__ == "__main__":
    unittest.main()
