"""契约 B5：请购单（purchase_requisition）的类型集合、正文和命令行 --type。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_arap_last, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _draft, _edit_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherDraft, VoucherEdit
from co.client.u8co_kinds import (
    CLOSABLE_KINDS,
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    GENERATABLE_KINDS,
    KIND_NAMES,
    UPDATABLE_KINDS,
    VERIFIABLE_KINDS,
)

_KIND = "purchase_requisition"
_HEAD = {"dDate": "2026-09-28", "cDepCode": "D01", "cMemo": "请购"}
_LINES = [{"cInvCode": "A001", "fQuantity": 2, "dRequirDate": "2026-10-08", "iOriCost": 10.5}]
_EDIT = [{"op": "update", "line_id": 7, "fQuantity": 3}]


def _call(doc_id: int, action: str = "") -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", doc_id, action)


class RequisitionTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        self.assertIn(_KIND, KIND_NAMES)
        _assert_arap_last(self)
        for group in (CREATABLE_KINDS, DELETABLE_KINDS, UPDATABLE_KINDS, CLOSABLE_KINDS, VERIFIABLE_KINDS):
            self.assertIn(_KIND, group)
        self.assertNotIn(_KIND, GENERATABLE_KINDS)

    def test_create_body(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).create_voucher(_call(0), VoucherDraft(_KIND, _HEAD, _LINES))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/create")
        self.assertEqual(list(sent), _AUTH + ["type", "head", "lines"])
        self.assertEqual((sent["type"], sent["head"], sent["lines"]), (_KIND, _HEAD, _LINES))

    def test_update_body(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).update_voucher(_call(9), VoucherEdit(_KIND, None, _EDIT))
            captured = bridge.capture
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/update")
        self.assertEqual((sent["type"], sent["id"], sent["lines"]), (_KIND, 9, _EDIT))

    def test_verify_and_close_bodies(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).verify_voucher(_call(9, "verify"), _KIND)
            verified = json.loads(bridge.capture["body"].decode("utf-8"))
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).close_voucher(_call(9), _KIND, "close")
            closed = json.loads(bridge.capture["body"].decode("utf-8"))
        self.assertEqual((verified["type"], verified["id"], verified["action"]), (_KIND, 9, "verify"))
        self.assertEqual((closed["type"], closed["id"], closed["action"]), (_KIND, 9, "close"))
        self.assertNotIn("line_ids", closed)

    def test_cli_accepts_requisition(self) -> None:
        with _JsonFile({"head": _HEAD, "lines": _LINES}) as path:
            made = _draft(_parser().parse_args(_argv("create", "--type", _KIND, "--file", path)))
        with _JsonFile({"lines": _EDIT}) as path:
            edit = _edit_draft(_parser().parse_args(_argv("update", "--type", _KIND, "--id", "9", "--file", path)))
        self.assertEqual((made.kind, made.head, made.lines), (_KIND, _HEAD, _LINES))
        self.assertEqual((edit.kind, edit.lines), (_KIND, _EDIT))


if __name__ == "__main__":
    unittest.main()
