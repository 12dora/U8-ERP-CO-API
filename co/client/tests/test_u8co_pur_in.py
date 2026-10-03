"""契约 B6：无来源采购入库单（purchase_in）的新增、修改正文和命令行 --type。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _draft, _edit_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherDraft, VoucherEdit
from co.client.u8co_kinds import CREATABLE_KINDS, GENERATABLE_KINDS, UPDATABLE_KINDS

_HEAD = {"cWhCode": "01", "cVenCode": "V001", "cMemo": "无来源入库"}
_LINES = [{"cInvCode": "A001", "iQuantity": 2, "iUnitCost": 10.5, "iTaxRate": 13}]
_EDIT = [{"op": "update", "line_id": 7, "iQuantity": 3}]


class PurchaseInScratchTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        self.assertIn("purchase_in", CREATABLE_KINDS)
        self.assertIn("purchase_in", UPDATABLE_KINDS)
        self.assertIn("purchase_in", GENERATABLE_KINDS)

    def test_create_body(self) -> None:
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 0)
            U8CoClient(bridge.base_url, SECRET, timeout=5).create_voucher(call, VoucherDraft("purchase_in", _HEAD, _LINES))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/create")
        self.assertEqual(list(sent), _AUTH + ["type", "head", "lines"])
        self.assertEqual((sent["type"], sent["head"], sent["lines"]), ("purchase_in", _HEAD, _LINES))

    def test_update_body(self) -> None:
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 9)
            U8CoClient(bridge.base_url, SECRET, timeout=5).update_voucher(call, VoucherEdit("purchase_in", None, _EDIT))
            captured = bridge.capture
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/update")
        self.assertEqual((sent["type"], sent["id"], sent["lines"]), ("purchase_in", 9, _EDIT))
        self.assertNotIn("head", sent)

    def test_cli_accepts_purchase_in(self) -> None:
        with _JsonFile({"head": _HEAD, "lines": _LINES}) as path:
            made = _draft(_parser().parse_args(_argv("create", "--type", "purchase_in", "--file", path)))
        with _JsonFile({"lines": _EDIT}) as path:
            edit = _edit_draft(_parser().parse_args(_argv("update", "--type", "purchase_in", "--id", "9", "--file", path)))
        self.assertEqual((made.kind, made.head, made.lines), ("purchase_in", _HEAD, _LINES))
        self.assertEqual((edit.kind, edit.lines), ("purchase_in", _EDIT))


if __name__ == "__main__":
    unittest.main()
