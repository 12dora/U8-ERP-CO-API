"""无来源销售出库单（sale_out，来源库存）：类型名单、新增正文和命令行 --type。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherDraft
from co.client.u8co_kinds import (
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    GENERATABLE_KINDS,
    SRCLESS_CREATE_KINDS,
    UPDATABLE_KINDS,
)

_HEAD = {"cWhCode": "05", "cCusCode": "C001", "cDepCode": "D01", "cRdCode": "101", "cSTCode": "XS"}
_LINES = [{"cInvCode": "A001", "iQuantity": 2}]


class SaleOutSourcelessTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        self.assertIn("sale_out", CREATABLE_KINDS)
        self.assertIn("sale_out", DELETABLE_KINDS)
        self.assertIn("sale_out", UPDATABLE_KINDS)
        self.assertIn("sale_out", GENERATABLE_KINDS)  # 参照发货单生单不变
        # 走库存的 StockDom 路径，不在 SrcLessReq 名单里。
        self.assertNotIn("sale_out", SRCLESS_CREATE_KINDS)
        self.assertEqual(len(CREATABLE_KINDS), len(set(CREATABLE_KINDS)))

    def test_create_body(self) -> None:
        with _Running() as bridge:
            call = U8Call("803", "2026", "op001", PASSWORD, "2026-09-28", 0)
            U8CoClient(bridge.base_url, SECRET, timeout=5).create_voucher(call, VoucherDraft("sale_out", _HEAD, _LINES))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/create")
        self.assertEqual(list(sent), _AUTH + ["type", "head", "lines"])
        self.assertEqual((sent["type"], sent["head"], sent["lines"]), ("sale_out", _HEAD, _LINES))

    def test_cli_create_type(self) -> None:
        with _JsonFile({"head": _HEAD, "lines": _LINES}) as path:
            draft = _draft(_parser().parse_args(_argv("create", "--type", "sale_out", "--file", path)))
        self.assertEqual((draft.kind, draft.head, draft.lines), ("sale_out", _HEAD, _LINES))


if __name__ == "__main__":
    unittest.main()
