"""生单来的单据和应收应付的修改正文和命令行 --type。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _edit_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherEdit
from co.client.u8co_kinds import CLOSABLE_KINDS, KIND_NAMES, MORE_UPDATABLE_KINDS, UPDATABLE_KINDS

_MORE = (
    "dispatch", "sale_return", "sale_invoice", "sale_out", "product_in", "material_out",
    "arrival", "purchase_return", "purchase_invoice", "ar_receipt", "ap_payment", "ar_bill", "ap_bill",
)
_HEAD = {"cMemo": "改"}
_EDIT = [{"op": "update", "line_id": 7, "iQuantity": 3}, {"op": "delete", "line_id": 8}]


class MoreUpdateTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        self.assertEqual(MORE_UPDATABLE_KINDS, _MORE)
        self.assertEqual(len(UPDATABLE_KINDS), len(set(UPDATABLE_KINDS)))
        self.assertLessEqual(set(UPDATABLE_KINDS), set(KIND_NAMES))
        for kind in _MORE + ("sale_order", "purchase_order", "purchase_in", "purchase_requisition"):
            self.assertIn(kind, UPDATABLE_KINDS)
        for kind in _MORE:
            if kind != "arrival":  # 到货单可关闭
                self.assertNotIn(kind, CLOSABLE_KINDS)
        self.assertIn("arrival", CLOSABLE_KINDS)
        self.assertIn("production_order", UPDATABLE_KINDS)  # 生产订单修改
        self.assertIn("qm_product_check", UPDATABLE_KINDS)  # 检验单可修改
        self.assertNotIn("stock_opening", UPDATABLE_KINDS)

    def test_update_body(self) -> None:
        for kind in ("dispatch", "sale_return", "ar_bill"):
            with _Running() as bridge:
                call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 9)
                U8CoClient(bridge.base_url, SECRET, timeout=5).update_voucher(call, VoucherEdit(kind, _HEAD, _EDIT))
                captured = bridge.capture
            _assert_signed(self, captured)
            sent = json.loads(captured["body"].decode("utf-8"))
            self.assertEqual(captured["path"], "/u8co/v1/vouchers/update")
            self.assertEqual(list(sent), _AUTH + ["type", "id", "head", "lines"])
            self.assertEqual((sent["type"], sent["id"], sent["head"], sent["lines"]), (kind, 9, _HEAD, _EDIT))

    def test_production_order_update_body(self) -> None:
        # 生产订单修改照常转发（表头只收 remark，行 op 只能是 update，由桥和 API 校验）。
        head = {"remark": "改"}
        lines = [{"op": "update", "line_id": 7, "qty": 2}]
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 9)
            U8CoClient(bridge.base_url, SECRET, timeout=5).update_voucher(call, VoucherEdit("production_order", head, lines))
            captured = bridge.capture
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual((sent["type"], sent["id"], sent["head"], sent["lines"]), ("production_order", 9, head, lines))

    def test_arrival_close_body(self) -> None:
        # 到货单关闭、打开照常转发，按行时 line_ids 是 Autoid。
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 9)
            U8CoClient(bridge.base_url, SECRET, timeout=5).close_voucher(call, "arrival", "close", [31])
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/close")
        self.assertEqual((sent["type"], sent["id"], sent["action"], sent["line_ids"]), ("arrival", 9, "close", [31]))
        parsed = _parser().parse_args(_argv("close", "--type", "arrival", "--id", "9", "--action", "open"))
        self.assertEqual((parsed.type, parsed.action), ("arrival", "open"))

    def test_cli_accepts_the_new_types(self) -> None:
        for kind in _MORE:
            with _JsonFile({"head": _HEAD, "lines": _EDIT}) as path:
                edit = _edit_draft(_parser().parse_args(_argv("update", "--type", kind, "--id", "9", "--file", path)))
            self.assertEqual((edit.kind, edit.head, edit.lines), (kind, _HEAD, _EDIT))


if __name__ == "__main__":
    unittest.main()
