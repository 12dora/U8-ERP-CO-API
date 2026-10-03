"""采购手工结算（vouchers/create，type=purchase_settle，第一级）的客户端请求体与行数。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _assert_signed, _Running
from co.client.u8co_client import U8Call, U8CoClient, VoucherDraft
from co.client.u8co_rows import _create_rows

_KIND = "purchase_settle"
_HEAD = {"settle_date": "2026-09-30"}
_LINES = [
    {"in_line_id": 11, "invoice_line_id": 21, "quantity": 1, "amount": 10.5},
    {"in_line_id": 12, "quantity": 2},
    {"in_line_id": 13, "quantity": -2},
]


def _call() -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-10-03", None, "")


class PuSettleManTests(unittest.TestCase):
    def test_create_body(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).create_voucher(_call(), VoucherDraft(_KIND, _HEAD, _LINES))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/create")
        self.assertEqual(list(sent), _AUTH + ["type", "head", "lines"])
        self.assertEqual((sent["type"], sent["head"], sent["lines"]), (_KIND, _HEAD, _LINES))

    def test_line_counts(self) -> None:
        many = [{"in_line_id": i, "quantity": 1} for i in range(1, 401)]
        self.assertEqual(len(_create_rows(_KIND, many)), 400)
        with self.assertRaises(ValueError):
            _create_rows(_KIND, many + [{"in_line_id": 401, "quantity": 1}])
        with self.assertRaises(ValueError):
            _create_rows("other_in", many[:201])
        self.assertEqual(len(_create_rows("other_in", many[:200])), 200)


if __name__ == "__main__":
    unittest.main()
