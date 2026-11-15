"""生产订单（production_order）按行关闭、打开的类型集合、正文和命令行 --type。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_kinds import CLOSABLE_KINDS, CREATABLE_KINDS, DELETABLE_KINDS, KIND_NAMES, UPDATABLE_KINDS

_KIND = "production_order"


def _call(doc_id: int) -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", doc_id, "")


class ProductionOrderCloseTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        self.assertIn(_KIND, KIND_NAMES)
        self.assertIn(_KIND, CLOSABLE_KINDS)
        # 可新增、删除（test_u8co_mo_create），可修改（test_co_mo_update）。
        self.assertIn(_KIND, CREATABLE_KINDS)
        self.assertIn(_KIND, DELETABLE_KINDS)
        self.assertIn(_KIND, UPDATABLE_KINDS)

    def test_close_lines_body(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).close_voucher(_call(9), _KIND, "close", [11, 12])
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/close")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "action", "line_ids"])
        self.assertEqual((sent["type"], sent["id"], sent["action"], sent["line_ids"]), (_KIND, 9, "close", [11, 12]))

    def test_open_whole_body(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).close_voucher(_call(9), _KIND, "open")
            sent = json.loads(bridge.capture["body"].decode("utf-8"))
        self.assertEqual((sent["type"], sent["id"], sent["action"]), (_KIND, 9, "open"))
        self.assertNotIn("line_ids", sent)

    def test_cli_accepts_production_order(self) -> None:
        parsed = _parser().parse_args(
            _argv("close", "--type", _KIND, "--id", "9", "--action", "close", "--line-ids", "11,12")
        )
        self.assertEqual((parsed.type, parsed.id, parsed.action, parsed.line_ids), (_KIND, 9, "close", "11,12"))


if __name__ == "__main__":
    unittest.main()
