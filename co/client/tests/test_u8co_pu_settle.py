"""采购结算单参照采购发票整张自动结算（不带 lines，表头只收 settle_date）和删除。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _assert_signed, _Running
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen
from co.client.u8co_kinds import (
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    GENERATABLE_KINDS,
    GENERATE_SOURCES,
    SOURCE_TYPES,
    UPDATABLE_KINDS,
    VERIFIABLE_KINDS,
    whole_generate,
)

_KIND = "purchase_settle"
_HEAD = {"settle_date": "2026-09-30"}


def _call(doc_id: int) -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-10-03", doc_id, "")


class PuSettleWriteTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        self.assertIn(_KIND, DELETABLE_KINDS)
        self.assertIn(_KIND, GENERATABLE_KINDS)
        self.assertEqual(GENERATE_SOURCES[_KIND], ("purchase_invoice",))
        self.assertIn("purchase_invoice", SOURCE_TYPES)
        self.assertIn(_KIND, CREATABLE_KINDS)  # 手工结算（test_u8co_pu_settle_man）
        for group in (UPDATABLE_KINDS, VERIFIABLE_KINDS):
            self.assertNotIn(_KIND, group)
        self.assertTrue(whole_generate(_KIND) and whole_generate(_KIND, "purchase_invoice"))
        self.assertTrue(whole_generate("sale_invoice", "sale_return") and whole_generate("sale_out"))
        self.assertFalse(whole_generate("sale_invoice", "dispatch") or whole_generate("purchase_invoice"))

    def test_generate_body_without_lines(self) -> None:
        with _Running() as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            client.generate_voucher(_call(1000000001), VoucherGen(_KIND, _HEAD, None, "purchase_invoice"))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head"])
        self.assertEqual((sent["type"], sent["id"], sent["head"]), (_KIND, 1000000001, _HEAD))

    def test_delete_body(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).delete_voucher(_call(2773), _KIND)
            captured = bridge.capture
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/delete")
        self.assertEqual((sent["type"], sent["id"]), (_KIND, 2773))


if __name__ == "__main__":
    unittest.main()
