"""生产订单参照销售订单生单的类型集合、正文和命令行。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _gen_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen
from co.client.u8co_kinds import GENERATABLE_KINDS, GENERATE_SOURCES, SOURCE_TYPES, check_source

_HEAD = {"mo_type": "1", "mo_code": "MO-T1", "remark": "参照销售订单"}
_LINES = [{"source_line_id": 41, "quantity": 2, "dept_code": "D01"}]


class MoFromSaleOrderTests(unittest.TestCase):
    def test_sets(self) -> None:
        self.assertIn("production_order", GENERATABLE_KINDS)
        self.assertEqual(GENERATE_SOURCES["production_order"], ("sale_order",))
        self.assertIn("sale_order", SOURCE_TYPES)
        self.assertEqual(check_source("production_order", "sale_order"), "sale_order")
        with self.assertRaises(ValueError):
            check_source("production_order", "purchase_order")

    def test_generate_body(self) -> None:
        draft = VoucherGen("production_order", _HEAD, _LINES, "sale_order")
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 9)
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("production_order", 9, "sale_order"))
        self.assertEqual((sent["head"], sent["lines"]), (_HEAD, _LINES))

    def test_cli(self) -> None:
        with _JsonFile({"head": _HEAD, "lines": _LINES}) as path:
            argv = _argv(
                "generate",
                "--type",
                "production_order",
                "--id",
                "9",
                "--file",
                path,
                "--source-type",
                "sale_order",
            )
            draft = _gen_draft(_parser().parse_args(argv))
        self.assertEqual((draft.kind, draft.source_type, draft.lines), ("production_order", "sale_order", _LINES))
        self.assertEqual(draft.head, _HEAD)


if __name__ == "__main__":
    unittest.main()
