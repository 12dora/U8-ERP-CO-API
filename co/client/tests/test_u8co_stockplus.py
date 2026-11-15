"""销售出库生单行的批号 / 货位与拆行、无来源红字采购入库（表头 red）原样发给桥。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import _AUTH, SECRET, _assert_signed, make_call, _Running
from co.client.u8co_client import U8CoClient, VoucherDraft, VoucherGen

_SPLIT = [
    {"source_line_id": 15, "quantity": 1, "cposition": "A01"},
    {"source_line_id": 15, "quantity": 2, "cposition": "A02", "cbatch": "B1"},
]


class StockPlusWireTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], None]) -> tuple[dict[str, Any], dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            sent = json.loads(bridge.capture["body"].decode("utf-8"))
            _assert_signed(self, bridge.capture)
            captured = bridge.capture
        return sent, captured

    def test_generate_sale_out_split_lines_sent_unchanged(self) -> None:
        draft = VoucherGen("sale_out", None, _SPLIT)
        sent, captured = self._send(lambda client: client.generate_voucher(make_call(100), draft))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "lines"])
        self.assertEqual(sent["lines"], _SPLIT)

    def test_create_red_purchase_in_keeps_red_flag(self) -> None:
        head = {"cwhcode": "01", "cvencode": "V001", "red": True}
        lines = [{"cinvcode": "A001", "iquantity": 1}]
        draft = VoucherDraft("purchase_in", head, lines)
        sent, captured = self._send(lambda client: client.create_voucher(make_call(), draft))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/create")
        self.assertIs(sent["head"]["red"], True)
        self.assertEqual(sent["lines"], lines)

