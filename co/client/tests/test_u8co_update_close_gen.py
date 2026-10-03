"""修改、关闭、生单的请求正文和命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from unittest import mock

from co.client import u8co_client, u8co_errors, u8co_kinds
from co.client.u8co_cli import (
    _edit_draft,
    _file_parts,
    _gen_draft,
    _parse_line_ids,
    _parser,
)
from co.client.u8co_client import (
    CLOSABLE_KINDS,
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    GENERATABLE_KINDS,
    KIND_NAMES,
    VERIFIABLE_KINDS,
    U8Call,
    U8CoClient,
    UPDATABLE_KINDS,
    SignedRequest,
    VoucherDraft,
    VoucherEdit,
    VoucherGen,
    derive_keys,
    main,
    sign,
)

SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
PASSWORD = "测试Pass#01"
_K_MAC = derive_keys(SECRET)[0]
_AUTH = ["acc", "year", "operator", "password_enc", "date"]
_NEW_LOADS = ("transfer", "sale_invoice", "purchase_invoice", "production_order")
_PREVIOUS = (
    "sale_order", "dispatch", "purchase_order", "arrival",
    "purchase_in", "other_in", "other_out", "product_in", "material_out", "sale_out",
    "qm_incoming_check", "qm_product_check", "qm_incoming_reject", "qm_product_reject",
)


_ARAP_BASE = ("ar_receipt", "ap_payment", "ar_bill", "ap_bill")
_PHASE3_CREATABLE = ("sale_order", "other_in", "other_out", "purchase_order", "transfer")
_PHASE3_DELETABLE = _PHASE3_CREATABLE + (
    "dispatch", "arrival", "sale_out", "purchase_in", "sale_invoice",
    "material_out", "product_in", "purchase_invoice",
)
_PHASE3_GENERATABLE = (
    "dispatch", "sale_out", "purchase_in", "sale_invoice", "material_out", "product_in", "purchase_invoice",
    "arrival",
)


def _assert_arap_last(case: unittest.TestCase) -> None:
    """应收应付类型排在 KIND_NAMES 最后：最早的四种在前，退款类型接在其后。"""
    arap = u8co_kinds.ARAP_KINDS
    case.assertEqual(arap[: len(_ARAP_BASE)], _ARAP_BASE)
    case.assertEqual(KIND_NAMES[-len(arap):], arap)


def make_call(doc_id: int | None = None) -> U8Call:
    return U8Call("998", "2026", "op001", PASSWORD, "2026-09-26", doc_id)


def _header(headers: dict[str, str], name: str) -> str | None:
    folded = name.lower()
    for key, value in headers.items():
        if key.lower() == folded:
            return value
    return None


def _assert_signed(case: unittest.TestCase, captured: dict[str, Any]) -> None:
    signed = SignedRequest(
        "POST",
        captured["path"],
        captured["body"],
        _header(captured["headers"], "X-U8co-Ts") or "",
        _header(captured["headers"], "X-U8co-Nonce") or "",
    )
    case.assertEqual(_header(captured["headers"], "X-U8co-Sig"), sign(_K_MAC, signed))
    case.assertNotIn(PASSWORD.encode("utf-8"), captured["body"])
    case.assertIn(b"password_enc", captured["body"])


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server = self.server
        assert isinstance(server, _Bridge)
        length = int(self.headers.get("Content-Length", "0"))
        server.capture = {
            "path": self.path,
            "headers": {key: value for key, value in self.headers.items()},
            "body": self.rfile.read(length) if length else b"",
        }
        payload = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class _Bridge(ThreadingHTTPServer):
    def __init__(self) -> None:
        self.capture: dict[str, Any] = {}
        super().__init__(("127.0.0.1", 0), _Handler)


class _Running:
    def __init__(self) -> None:
        self.httpd = _Bridge()
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _Running:
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def base_url(self) -> str:
        port = self.httpd.server_address[1]
        return f"http://127.0.0.1:{port}/u8co"

    @property
    def capture(self) -> dict[str, Any]:
        return self.httpd.capture


class _JsonFile:
    def __init__(self, data: dict[str, Any]) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.path = self._dir.name + "/voucher.json"
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)

    def __enter__(self) -> str:
        return self.path

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._dir.cleanup()


def _argv(command: str, *extra: str) -> list[str]:
    base = [
        command,
        "--base-url", "http://127.0.0.1/u8co",
        "--acc", "801",
        "--year", "2026",
        "--operator", "op001",
        "--date", "2026-09-27",
    ]
    return base + list(extra)


def _run_main(argv: list[str], method: str, fake: Callable[..., dict[str, Any]]) -> int:
    stdout = io.StringIO()
    with (
        mock.patch("co.client.u8co_cli._secret", return_value=SECRET),
        mock.patch("co.client.u8co_cli._password", return_value=PASSWORD),
        mock.patch(f"co.client.u8co_cli.U8CoClient.{method}", fake),
        contextlib.redirect_stdout(stdout),
    ):
        return main(argv)


class KindTests(unittest.TestCase):
    def test_legacy_names_stay_on_client(self) -> None:
        self.assertIs(u8co_client.U8CoError, u8co_errors.U8CoError)
        self.assertIs(u8co_client.U8CoLoginFailed, u8co_errors.U8CoLoginFailed)
        self.assertIs(u8co_client.parse_payload, u8co_errors.parse_payload)
        self.assertIs(u8co_client.error_from, u8co_errors.error_from)
        self.assertIs(u8co_client.KIND_NAMES, u8co_kinds.KIND_NAMES)
        self.assertIs(u8co_client.CREATABLE_KINDS, u8co_kinds.CREATABLE_KINDS)
        self.assertIs(u8co_client.WORKFLOW_KINDS, u8co_kinds.WORKFLOW_KINDS)
        self.assertIs(u8co_client.VERIFIABLE_KINDS, u8co_kinds.VERIFIABLE_KINDS)

    def test_update_kind_sets(self) -> None:
        for name in _PREVIOUS + _NEW_LOADS:
            self.assertIn(name, KIND_NAMES)
        groups = (CREATABLE_KINDS, DELETABLE_KINDS, UPDATABLE_KINDS, CLOSABLE_KINDS, GENERATABLE_KINDS)
        for group in (KIND_NAMES,) + groups:
            self.assertEqual(len(group), len(set(group)))
            self.assertLessEqual(set(group), set(KIND_NAMES))
        _assert_arap_last(self)
        # 新类型只在末尾追加：已有的前缀和相对次序不变。
        self.assertEqual(CREATABLE_KINDS[:10], _PHASE3_CREATABLE + _ARAP_BASE + ("purchase_in",))
        self.assertEqual(DELETABLE_KINDS[:17], _PHASE3_DELETABLE + _ARAP_BASE)
        self.assertEqual(
            UPDATABLE_KINDS[:7],
            ("sale_order", "purchase_order", "other_in", "other_out", "transfer", "purchase_in", "purchase_requisition"),
        )
        self.assertEqual(CLOSABLE_KINDS[:2], ("sale_order", "purchase_order"))
        self.assertEqual(GENERATABLE_KINDS[:8], _PHASE3_GENERATABLE)
        more = (
            "dispatch", "sale_return", "sale_invoice", "sale_out", "product_in", "material_out",
            "arrival", "purchase_return", "purchase_invoice",
        ) + _ARAP_BASE
        self.assertEqual(u8co_kinds.MORE_UPDATABLE_KINDS, more)
        self.assertLessEqual(set(more), set(UPDATABLE_KINDS))
        # 应收应付（含退款）可新增、删除、修改、审核，不能关闭、生单。
        for kind in u8co_kinds.ARAP_KINDS:
            for group in (CREATABLE_KINDS, DELETABLE_KINDS, UPDATABLE_KINDS, VERIFIABLE_KINDS):
                self.assertIn(kind, group)
            self.assertNotIn(kind, CLOSABLE_KINDS)
            self.assertNotIn(kind, GENERATABLE_KINDS)

    def test_verifiable_kinds(self) -> None:
        # 契约 B1 起采购发票也可审核（采购复核）。
        self.assertEqual(VERIFIABLE_KINDS, tuple(name for name in KIND_NAMES if name in VERIFIABLE_KINDS))
        self.assertIn("purchase_invoice", VERIFIABLE_KINDS)
        self.assertLessEqual(set(VERIFIABLE_KINDS), set(KIND_NAMES))


class WireTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], None]) -> tuple[dict[str, Any], dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            sent = json.loads(bridge.capture["body"].decode("utf-8"))
            _assert_signed(self, bridge.capture)
            captured = bridge.capture
        self.assertNotIn("password", sent)
        return sent, captured

    def test_load_new_kinds(self) -> None:
        for kind in _NEW_LOADS:
            sent, captured = self._send(lambda client, kind=kind: client.load_voucher(make_call(8), kind))
            self.assertEqual(captured["path"], "/u8co/v1/vouchers/load")
            self.assertEqual(sent["type"], kind)
            self.assertEqual(list(sent), _AUTH + ["type", "id"])

    def test_verify_voucher_skips_sql_only_kinds(self) -> None:
        call = U8Call("998", "2026", "op001", PASSWORD, "2026-09-26", 8, "verify")
        sent, captured = self._send(lambda client: client.verify_voucher(call, "sale_invoice"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/verify")
        self.assertEqual(sent["type"], "sale_invoice")
        self.assertEqual(sent["action"], "verify")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "action"])
        # 契约 B1：采购发票的审核是采购复核，照常发给桥。
        sent, _ = self._send(lambda client: client.verify_voucher(call, "purchase_invoice"))
        self.assertEqual(sent["type"], "purchase_invoice")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "action"])
        client = U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError) as caught:
            client.verify_voucher(call, "no_such_kind")
        self.assertIn("未知", str(caught.exception))

    def test_update_wire_body(self) -> None:
        draft = VoucherEdit(
            "sale_order",
            {"cMemo": "改备注"},
            [{"op": "update", "line_id": 7, "iQuantity": 2}],
        )
        sent, captured = self._send(lambda client: client.update_voucher(make_call(15), draft))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/update")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "head", "lines"])
        self.assertEqual(sent["id"], 15)
        self.assertEqual(sent["head"]["cMemo"], "改备注")
        self.assertEqual(sent["lines"][0]["line_id"], 7)
        self.assertIn("改备注".encode(), captured["body"])

    def test_update_omits_empty_side(self) -> None:
        head_only = VoucherEdit("purchase_order", {"cMemo": "只改头"}, None)
        sent, _captured = self._send(lambda client: client.update_voucher(make_call(4), head_only))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "head"])
        lines_only = VoucherEdit("transfer", None, [{"op": "add", "cInvCode": "A", "iTVQuantity": 2}])
        sent, _captured = self._send(lambda client: client.update_voucher(make_call(4), lines_only))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "lines"])
        self.assertNotIn("head", sent)

    def test_update_requires_content_and_kind(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError) as empty:
            client.update_voucher(make_call(4), VoucherEdit("other_in", None, []))
        self.assertIn("没有要修改的内容", str(empty.exception))
        with self.assertRaises(ValueError) as kind:
            client.update_voucher(make_call(4), VoucherEdit("qm_incoming_inspect", {"cMemo": "x"}, None))
        self.assertIn("不支持", str(kind.exception))

    def test_close_wire_body(self) -> None:
        sent, captured = self._send(
            lambda client: client.close_voucher(make_call(3), "sale_order", "close", [4, 9]),
        )
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/close")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "action", "line_ids"])
        self.assertEqual(sent["action"], "close")
        self.assertEqual(sent["line_ids"], [4, 9])
        sent, _captured = self._send(lambda client: client.close_voucher(make_call(3), "purchase_order", "open"))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "action"])
        self.assertNotIn("line_ids", sent)

    def test_close_rejects_bad_action_and_ids(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError) as action:
            client.close_voucher(make_call(3), "sale_order", "verify")
        self.assertIn("close", str(action.exception))
        with self.assertRaises(ValueError):
            client.close_voucher(make_call(3), "other_in", "close")
        with self.assertRaises(ValueError):
            client.close_voucher(make_call(3), "sale_order", "open", [1, 1])
        with self.assertRaises(ValueError):
            client.close_voucher(make_call(3), "sale_order", "open", [0])
        with self.assertRaises(ValueError):
            client.close_voucher(make_call(3), "sale_order", "open", [2147483648])
        with self.assertRaises(ValueError):
            client.close_voucher(make_call(3), "sale_order", "open", list(range(1, 202)))

    def test_generate_dispatch_wire_body(self) -> None:
        draft = VoucherGen(
            "dispatch",
            {"dDate": "2026-09-27", "cWhCode": "01"},
            [{"source_line_id": 15, "quantity": 1, "cWhCode": "02"}],
        )
        sent, captured = self._send(lambda client: client.generate_voucher(make_call(100), draft))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "head", "lines"])
        self.assertEqual(sent["type"], "dispatch")
        self.assertEqual(sent["id"], 100)
        self.assertEqual(sent["lines"][0]["quantity"], 1)
        self.assertEqual(sent["lines"][0]["source_line_id"], 15)

    def test_generate_sale_out_omits_lines(self) -> None:
        sent, _captured = self._send(
            lambda client: client.generate_voucher(make_call(100), VoucherGen("sale_out", None, None)),
        )
        self.assertEqual(list(sent), _AUTH + ["type", "id"])
        self.assertNotIn("lines", sent)
        self.assertNotIn("head", sent)
        sent, _captured = self._send(
            lambda client: client.generate_voucher(make_call(100), VoucherGen("sale_out", {"dDate": "2026-09-27"}, None)),
        )
        self.assertEqual(list(sent), _AUTH + ["type", "id", "head"])
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError) as caught:
            client.generate_voucher(make_call(100), VoucherGen("sale_out", None, []))
        self.assertIn("lines 必须是 1 到 200 行", str(caught.exception))
        with self.assertRaises(ValueError):
            client.generate_voucher(make_call(100), VoucherGen("purchase_in", {"cWhCode": "01"}, None))

    def test_delete_extended_kinds(self) -> None:
        kinds = ("dispatch", "arrival", "sale_out", "purchase_in", "purchase_order", "transfer")
        for kind in kinds:
            sent, captured = self._send(lambda client, kind=kind: client.delete_voucher(make_call(9), kind))
            self.assertEqual(captured["path"], "/u8co/v1/vouchers/delete")
            self.assertEqual(sent["type"], kind)
            self.assertEqual(list(sent), _AUTH + ["type", "id"])

    def test_delete_refuses_qm_kind(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError) as caught:
            client.delete_voucher(make_call(1), "no_such_kind")
        self.assertIn("未知的单据类型", str(caught.exception))

    def test_create_new_kinds(self) -> None:
        for kind in ("purchase_order", "transfer"):
            draft = VoucherDraft(kind, {"cMemo": "n"}, [{"cInvCode": "A", "iQuantity": 1}])
            sent, captured = self._send(lambda client, draft=draft: client.create_voucher(make_call(), draft))
            self.assertEqual(captured["path"], "/u8co/v1/vouchers/create")
            self.assertEqual(sent["type"], kind)
            self.assertNotIn("id", sent)
            self.assertEqual(list(sent), _AUTH + ["type", "head", "lines"])


class CliTests(unittest.TestCase):
    def test_parse_update_close_generate(self) -> None:
        with _JsonFile({"head": {"cMemo": "改"}, "lines": [{"op": "add", "cInvCode": "A"}]}) as path:
            update = _parser().parse_args(_argv("update", "--type", "sale_order", "--id", "15", "--file", path))
            draft = _edit_draft(update)
        self.assertEqual(update.command, "update")
        self.assertEqual(draft.kind, "sale_order")
        self.assertEqual(draft.head["cMemo"], "改")
        close = _parser().parse_args(
            _argv("close", "--type", "purchase_order", "--id", "3", "--action", "open", "--line-ids", "1, 2"),
        )
        self.assertEqual(_parse_line_ids(close.line_ids), [1, 2])
        self.assertIsNone(_parse_line_ids(None))
        with _JsonFile({"lines": [{"source_line_id": 8, "quantity": 1}]}) as path:
            generated = _parser().parse_args(_argv("generate", "--type", "purchase_in", "--id", "9", "--file", path))
            gen = _gen_draft(generated)
        self.assertIsNone(gen.head)
        self.assertEqual(gen.lines[0]["source_line_id"], 8)

    def test_generate_sale_out_file_lines_optional(self) -> None:
        with _JsonFile({}) as path:
            parsed = _parser().parse_args(_argv("generate", "--type", "sale_out", "--id", "9", "--file", path))
            draft = _gen_draft(parsed)
        self.assertIsNone(draft.lines)
        self.assertIsNone(draft.head)
        with _JsonFile({"lines": [{"source_line_id": 8, "quantity": 1}]}) as path:
            parsed = _parser().parse_args(_argv("generate", "--type", "sale_out", "--id", "9", "--file", path))
            draft = _gen_draft(parsed)
        self.assertEqual(draft.lines, [{"source_line_id": 8, "quantity": 1}])

    def test_choices_reject_wrong_kind(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("update", "--type", "stock_opening", "--id", "1", "--file", "x"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("close", "--type", "other_in", "--id", "1", "--action", "close"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("delete", "--type", "no_such_kind", "--id", "1"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("verify", "--type", "no_such_kind", "--id", "1", "--action", "verify"))
            reviewed = _parser().parse_args(
                _argv("verify", "--type", "purchase_invoice", "--id", "1", "--action", "unverify"),
            )
            self.assertEqual(reviewed.type, "purchase_invoice")
            loaded = _parser().parse_args(_argv("load", "--type", "production_order", "--id", "1"))
            mo = _parser().parse_args(_argv("verify", "--type", "production_order", "--id", "1", "--action", "unverify"))
            checked = _parser().parse_args(_argv("verify", "--type", "sale_invoice", "--id", "4", "--action", "verify"))
            created = _parser().parse_args(
                _argv("create", "--type", "transfer", "--head-json", "{}", "--lines-json", "[]"),
            )
        self.assertEqual(loaded.type, "production_order")
        self.assertEqual(mo.action, "unverify")
        self.assertEqual(checked.type, "sale_invoice")
        self.assertEqual(checked.action, "verify")
        self.assertEqual(created.type, "transfer")

    def test_create_file_still_needs_both_keys(self) -> None:
        with _JsonFile({"head": {"cMemo": "x"}}) as path:
            with self.assertRaises(SystemExit) as caught:
                _file_parts(path)
        self.assertIn("head 和 lines", str(caught.exception))

    def test_line_ids_must_be_decimal(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            _parse_line_ids("1,a")
        self.assertIn("正整数", str(caught.exception))

    def test_main_wires_update_close_generate(self) -> None:
        seen: dict[str, Any] = {}

        def _update(self: U8CoClient, call: U8Call, draft: VoucherEdit) -> dict[str, Any]:
            seen["update"] = (call.doc_id, draft)
            return {"ok": True, "id": call.doc_id}

        def _close(self: U8CoClient, call: U8Call, kind: str, action: str, line_ids: object = None) -> dict[str, Any]:
            seen["close"] = (call.doc_id, kind, action, line_ids)
            return {"ok": True}

        def _generate(self: U8CoClient, call: U8Call, draft: VoucherGen) -> dict[str, Any]:
            seen["generate"] = (call.doc_id, draft)
            return {"ok": True}

        with _JsonFile({"head": {"cMemo": "改"}}) as path:
            code = _run_main(_argv("update", "--type", "other_out", "--id", "15", "--file", path), "update_voucher", _update)
        self.assertEqual(code, 0)
        self.assertEqual(seen["update"][0], 15)
        self.assertEqual(seen["update"][1].kind, "other_out")
        self.assertEqual(seen["update"][1].head["cMemo"], "改")
        code = _run_main(
            _argv("close", "--type", "sale_order", "--id", "3", "--action", "close", "--line-ids", "4,5"),
            "close_voucher",
            _close,
        )
        self.assertEqual(code, 0)
        self.assertEqual(seen["close"], (3, "sale_order", "close", [4, 5]))
        with _JsonFile({"head": {"dDate": "2026-09-27"}}) as path:
            code = _run_main(
                _argv("generate", "--type", "sale_out", "--id", "9", "--file", path),
                "generate_voucher",
                _generate,
            )
        self.assertEqual(code, 0)
        self.assertEqual(seen["generate"][0], 9)
        self.assertIsNone(seen["generate"][1].lines)
        self.assertEqual(seen["generate"][1].head["dDate"], "2026-09-27")

    def test_main_delete_accepts_dispatch(self) -> None:
        seen: dict[str, Any] = {}

        def _delete(self: U8CoClient, call: U8Call, kind: str) -> dict[str, Any]:
            seen["kind"] = kind
            seen["id"] = call.doc_id
            return {"ok": True}

        code = _run_main(_argv("delete", "--type", "dispatch", "--id", "6"), "delete_voucher", _delete)
        self.assertEqual(code, 0)
        self.assertEqual(seen["kind"], "dispatch")
        self.assertEqual(seen["id"], 6)


if __name__ == "__main__":
    unittest.main()
