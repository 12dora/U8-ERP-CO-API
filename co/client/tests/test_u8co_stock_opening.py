"""期初结存单（stock_opening，U8 单据类型 34）的类型集合、正文和命令行 --type。不能修改（删除后重新录入）。
只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_arap_last, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherDraft, VoucherEdit
from co.client.u8co_kinds import (
    CLOSABLE_KINDS,
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    GENERATABLE_KINDS,
    KIND_NAMES,
    UPDATABLE_KINDS,
    VERIFIABLE_KINDS,
)

_KIND = "stock_opening"
_HEAD = {"cWhCode": "01", "cMemo": "期初"}
_LINES = [{"cInvCode": "A001", "iQuantity": 5, "iUnitCost": 2.5, "cBatch": "B1"}]
_EDIT = [{"op": "update", "line_id": 7, "iQuantity": 3}]


def _call(doc_id: int, action: str = "") -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", doc_id, action)


class StockOpeningTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        _assert_arap_last(self)
        for group in (KIND_NAMES, CREATABLE_KINDS, DELETABLE_KINDS, VERIFIABLE_KINDS):
            self.assertIn(_KIND, group)
        self.assertNotIn(_KIND, UPDATABLE_KINDS)
        self.assertNotIn(_KIND, CLOSABLE_KINDS)
        self.assertNotIn(_KIND, GENERATABLE_KINDS)
        self.assertEqual(VERIFIABLE_KINDS, tuple(name for name in KIND_NAMES if name in VERIFIABLE_KINDS))

    def test_create_body(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).create_voucher(_call(0), VoucherDraft(_KIND, _HEAD, _LINES))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/create")
        self.assertEqual(list(sent), _AUTH + ["type", "head", "lines"])
        self.assertEqual((sent["type"], sent["head"], sent["lines"]), (_KIND, _HEAD, _LINES))

    def test_update_refused_without_calling(self) -> None:
        client = U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=1)
        with self.assertRaisesRegex(ValueError, "不支持此操作"):
            client.update_voucher(_call(9), VoucherEdit(_KIND, None, _EDIT))

    def test_verify_delete_bodies(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).verify_voucher(_call(9, "unverify"), _KIND)
            verified = json.loads(bridge.capture["body"].decode("utf-8"))
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).delete_voucher(_call(9), _KIND)
            deleted = json.loads(bridge.capture["body"].decode("utf-8"))
            path = bridge.capture["path"]
        self.assertEqual((verified["type"], verified["action"]), (_KIND, "unverify"))
        self.assertEqual((path, deleted["type"], deleted["id"]), ("/u8co/v1/vouchers/delete", _KIND, 9))

    def test_cli_accepts_kind(self) -> None:
        with _JsonFile({"head": _HEAD, "lines": _LINES}) as path:
            made = _draft(_parser().parse_args(_argv("create", "--type", _KIND, "--file", path)))
        self.assertEqual((made.kind, made.head, made.lines), (_KIND, _HEAD, _LINES))

    def test_cli_update_rejects_kind(self) -> None:
        with _JsonFile({"lines": _EDIT}) as path, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("update", "--type", _KIND, "--id", "9", "--file", path))


if __name__ == "__main__":
    unittest.main()
