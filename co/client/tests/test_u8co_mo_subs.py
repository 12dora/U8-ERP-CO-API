"""生产订单修改行的 allocations（子件替代料）：客户端原样转发三层嵌套，其余类型、其余字段仍只收标量。只监听 127.0.0.1。"""

from __future__ import annotations

import unittest

from co.client.tests.test_u8co_bom import SECRET, _call, _sent
from co.client.u8co_client import U8CoClient, VoucherEdit

_KIND = "production_order"
_SUB = {"inv_code": "DEMO-002", "sequence": 1, "factor": 1.5, "replace_flag": False, "defines": {"define22": "文本A"}}
_LINES = [
    {"op": "update", "line_id": 11, "allocations": [{"sort_seq": 10, "inv_code": "DEMO-001", "substitutes": [_SUB]}]},
    {"op": "update", "line_id": 12, "qty": 2, "allocations": [{"sort_seq": 20, "inv_code": "DEMO-003", "substitutes": []}]},
]


class MoSubsWireTests(unittest.TestCase):
    def test_allocations_pass_through(self) -> None:
        sent, captured = _sent(lambda client: client.update_voucher(_call(9), VoucherEdit(_KIND, {}, _LINES)))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/update")
        self.assertEqual(sent["lines"], _LINES)

    def test_nesting_is_bounded(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        deeper = dict(_SUB, defines={"define22": {"x": 1}})
        bad_rows = [
            [{"op": "update", "line_id": 11, "allocations": [{"sort_seq": 10, "substitutes": [deeper]}]}],
            [{"op": "update", "line_id": 11, "substitutes": [_SUB]}],
            [{"op": "update", "line_id": 11, "allocations": {"sort_seq": 10}}],
        ]
        for lines in bad_rows:
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                client.update_voucher(_call(9), VoucherEdit(_KIND, {}, lines))
        with self.assertRaises(ValueError):
            client.update_voucher(_call(9), VoucherEdit("bom", {}, [{"op": "update", "sort_seq": 10, "allocations": []}]))


if __name__ == "__main__":
    unittest.main()
