"""检验单、其他检验单、其他报检单修改（vouchers/update，只收 head，检验单 head 可带 items）的客户端正文。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _assert_signed, _Running
from co.client.u8co_client import U8Call, U8CoClient, VoucherEdit
from co.client.u8co_kinds import QM_UPDATE_KINDS, UPDATABLE_KINDS, VERIFIABLE_KINDS

_ITEMS = [{"cChkItemCode": "I01", "cChkGuideCode": "G01", "cCheckValue": "1.2", "cTargetQJug": "不合格"}]
_HEAD = {"fDisQuantity": 1, "cReasonCode": "03", "cChkConclusion": "不合格", "items": _ITEMS}


def _call() -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-30", 7, "")


def _offline() -> U8CoClient:
    return U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)


class QmUpdateTests(unittest.TestCase):
    def _update(self, draft: VoucherEdit) -> dict:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).update_voucher(_call(), draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/update")
        return json.loads(captured["body"].decode("utf-8"))

    def test_kinds(self) -> None:
        self.assertEqual(QM_UPDATE_KINDS, ("qm_incoming_check", "qm_product_check", "qm_other_inspect", "qm_other_check"))
        self.assertLessEqual(set(QM_UPDATE_KINDS), set(UPDATABLE_KINDS))
        for kind in ("qm_incoming_inspect", "qm_product_inspect", "qm_incoming_reject"):
            self.assertNotIn(kind, UPDATABLE_KINDS)
        # 产品报检单只开放弃审（不能修改）。
        self.assertIn("qm_product_inspect", VERIFIABLE_KINDS)
        self.assertNotIn("qm_incoming_inspect", VERIFIABLE_KINDS)

    def test_check_update_keeps_the_items_list(self) -> None:
        for kind in ("qm_product_check", "qm_other_check"):
            sent = self._update(VoucherEdit(kind, _HEAD, None))
            self.assertEqual(list(sent), _AUTH + ["type", "id", "head"])
            self.assertEqual(sent["head"], _HEAD)

    def test_inspect_update_head_only(self) -> None:
        head = {"dDate": "2026-09-30", "cDefine10": "B-1"}
        sent = self._update(VoucherEdit("qm_other_inspect", head, None))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "head"])
        self.assertEqual(sent["head"], head)

    def test_items_are_refused_where_they_do_not_belong(self) -> None:
        bad = (
            VoucherEdit("qm_other_inspect", {"items": _ITEMS}, None),
            VoucherEdit("other_in", {"items": _ITEMS}, None),
            VoucherEdit("qm_incoming_check", {"items": []}, None),
            VoucherEdit("qm_incoming_check", {"cMemo": ["x"], "items": _ITEMS}, None),
        )
        for draft in bad:
            with self.assertRaises(ValueError, msg=repr(draft.head)):
                _offline().update_voucher(_call(), draft)
