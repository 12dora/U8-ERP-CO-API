"""形态转换单、调拨申请单、盘点单的类型集合、正文和命令行 --type。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_arap_last, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _draft, _edit_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherDraft, VoucherEdit
from co.client.u8co_kinds import (
    CLOSABLE_KINDS,
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    GENERATABLE_KINDS,
    KIND_NAMES,
    STOCK_MISC_KINDS,
    UPDATABLE_KINDS,
    VERIFIABLE_KINDS,
)

_HEAD = {"dAVDate": "2026-09-28", "cDepCode": "D01"}
_LINES = [
    {"cInvCode": "A001", "cWhCode": "01", "bAVType": "转换前", "iGroupNO": 1, "iAVQuantity": 2},
    {"cInvCode": "A002", "cWhCode": "01", "bAVType": "转换后", "iGroupNO": 1, "iAVQuantity": 2},
]
_EDIT = [{"op": "update", "line_id": 7, "iTVQuantity": 3}]


def _call(doc_id: int, action: str = "") -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", doc_id, action)


class StockMiscTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        self.assertEqual(STOCK_MISC_KINDS, ("shape_change", "transfer_request", "stock_check"))
        _assert_arap_last(self)
        for kind in STOCK_MISC_KINDS:
            for group in (KIND_NAMES, CREATABLE_KINDS, DELETABLE_KINDS):
                self.assertIn(kind, group)
            self.assertNotIn(kind, CLOSABLE_KINDS)
            self.assertNotIn(kind, GENERATABLE_KINDS)
        self.assertIn("shape_change", UPDATABLE_KINDS)
        self.assertIn("transfer_request", UPDATABLE_KINDS)
        self.assertNotIn("stock_check", UPDATABLE_KINDS)
        # 盘点单审核暂不支持（U8 生成盘盈盘亏单时报类型不匹配）。
        self.assertIn("shape_change", VERIFIABLE_KINDS)
        self.assertIn("transfer_request", VERIFIABLE_KINDS)
        self.assertNotIn("stock_check", VERIFIABLE_KINDS)
        self.assertEqual(VERIFIABLE_KINDS, tuple(name for name in KIND_NAMES if name in VERIFIABLE_KINDS))

    def test_create_body(self) -> None:
        with _Running() as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            client.create_voucher(_call(0), VoucherDraft("shape_change", _HEAD, _LINES))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/create")
        self.assertEqual(list(sent), _AUTH + ["type", "head", "lines"])
        self.assertEqual((sent["type"], sent["head"], sent["lines"]), ("shape_change", _HEAD, _LINES))

    def test_update_verify_delete_bodies(self) -> None:
        with _Running() as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            client.update_voucher(_call(9), VoucherEdit("transfer_request", None, _EDIT))
            updated = json.loads(bridge.capture["body"].decode("utf-8"))
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).verify_voucher(_call(9, "unverify"), "transfer_request")
            verified = json.loads(bridge.capture["body"].decode("utf-8"))
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).delete_voucher(_call(9), "stock_check")
            deleted = json.loads(bridge.capture["body"].decode("utf-8"))
            path = bridge.capture["path"]
        self.assertEqual((updated["type"], updated["id"], updated["lines"]), ("transfer_request", 9, _EDIT))
        self.assertEqual((verified["type"], verified["action"]), ("transfer_request", "unverify"))
        self.assertEqual((path, deleted["type"], deleted["id"]), ("/u8co/v1/vouchers/delete", "stock_check", 9))

    def test_stock_check_cannot_update(self) -> None:
        with self.assertRaises(ValueError):
            U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=1).update_voucher(
                _call(9), VoucherEdit("stock_check", {"cCVMemo": "x"}, None)
            )

    def test_cli_accepts_kinds(self) -> None:
        with _JsonFile({"head": _HEAD, "lines": _LINES}) as path:
            made = _draft(_parser().parse_args(_argv("create", "--type", "shape_change", "--file", path)))
        with _JsonFile({"lines": _EDIT}) as path:
            argv = _argv("update", "--type", "transfer_request", "--id", "9", "--file", path)
            edit = _edit_draft(_parser().parse_args(argv))
        self.assertEqual((made.kind, made.head, made.lines), ("shape_change", _HEAD, _LINES))
        self.assertEqual((edit.kind, edit.lines), ("transfer_request", _EDIT))
        with self.assertRaises(SystemExit):
            _parser().parse_args(_argv("update", "--type", "stock_check", "--id", "9", "--file", "x.json"))


if __name__ == "__main__":
    unittest.main()
