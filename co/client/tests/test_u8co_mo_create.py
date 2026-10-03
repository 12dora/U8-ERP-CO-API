"""生产订单（production_order）新增、删除（另可修改）的类型集合、正文、幂等键和命令行 --type。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherDraft
from co.client.u8co_kinds import CREATABLE_KINDS, DELETABLE_KINDS, UPDATABLE_KINDS

_KIND = "production_order"
_HEAD = {"remark": "测试"}
_LINES = [
    {
        "inv_code": "P001",
        "qty": 2,
        "start_date": "2026-09-28",
        "due_date": "2026-09-30",
        "mo_type": "1",
        "dept_code": "D01",
    }
]


def _call(doc_id: int) -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", doc_id, "")


class ProductionOrderCreateTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        self.assertIn(_KIND, CREATABLE_KINDS)
        self.assertIn(_KIND, DELETABLE_KINDS)
        self.assertIn(_KIND, UPDATABLE_KINDS)  # 可修改

    def test_create_body(self) -> None:
        with _Running() as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            client.create_voucher(_call(0), VoucherDraft(_KIND, _HEAD, _LINES))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/create")
        self.assertEqual(list(sent), _AUTH + ["type", "head", "lines"])
        self.assertEqual((sent["type"], sent["head"], sent["lines"]), (_KIND, _HEAD, _LINES))

    def test_create_with_idempotency_key(self) -> None:
        with _Running() as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            client.create_voucher(_call(0), VoucherDraft(_KIND, {}, _LINES), idempotency_key="mo-2026-0001")
            sent = json.loads(bridge.capture["body"].decode("utf-8"))
        self.assertEqual(sent["idempotency_key"], "mo-2026-0001")

    def test_delete_body(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).delete_voucher(_call(9), _KIND)
            captured = bridge.capture
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/delete")
        self.assertEqual((sent["type"], sent["id"]), (_KIND, 9))

    def test_cli_accepts_production_order(self) -> None:
        parser = _parser()
        created = parser.parse_args(
            _argv("create", "--type", _KIND, "--head-json", "{}", "--lines-json", json.dumps(_LINES))
        )
        self.assertEqual(created.type, _KIND)
        deleted = parser.parse_args(_argv("delete", "--type", _KIND, "--id", "9"))
        self.assertEqual((deleted.type, deleted.id), (_KIND, 9))
        updated = parser.parse_args(_argv("update", "--type", _KIND, "--id", "9", "--file", "mo.json"))
        self.assertEqual((updated.type, updated.id), (_KIND, 9))


if __name__ == "__main__":
    unittest.main()
