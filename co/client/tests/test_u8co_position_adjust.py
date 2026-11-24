"""货位调整单（position_adjust）的类型集合、修改正文和命令行 --type。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _argv, _assert_arap_last, _JsonFile, _Running
from co.client.u8co_cli import _edit_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherEdit
from co.client.u8co_kinds import (
    CLOSABLE_KINDS,
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    GENERATABLE_KINDS,
    KIND_NAMES,
    UPDATABLE_KINDS,
    VERIFIABLE_KINDS,
)

_KIND = "position_adjust"
_HEAD = {"cMemo": "改"}
_EDIT = [
    {"op": "update", "line_id": 7, "iQuantity": 3},
    {"op": "delete", "line_id": 8},
    {"op": "add", "cInvCode": "A001", "cBPosCode": "5A010101", "cAPosCode": "5A010102", "iQuantity": 1},
]


def _call(doc_id: int) -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", doc_id, "")


class PositionAdjustTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        _assert_arap_last(self)
        for group in (KIND_NAMES, CREATABLE_KINDS, UPDATABLE_KINDS, DELETABLE_KINDS, VERIFIABLE_KINDS):
            self.assertIn(_KIND, group)
        self.assertNotIn(_KIND, CLOSABLE_KINDS)
        self.assertNotIn(_KIND, GENERATABLE_KINDS)

    def test_update_body(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).update_voucher(_call(9), VoucherEdit(_KIND, _HEAD, _EDIT))
            captured = bridge.capture
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/update")
        self.assertEqual((sent["type"], sent["id"], sent["head"], sent["lines"]), (_KIND, 9, _HEAD, _EDIT))

    def test_cli_accepts_update(self) -> None:
        with _JsonFile({"head": _HEAD, "lines": _EDIT}) as path:
            edit = _edit_draft(_parser().parse_args(_argv("update", "--type", _KIND, "--id", "9", "--file", path)))
        self.assertEqual((edit.kind, edit.head, edit.lines), (_KIND, _HEAD, _EDIT))


if __name__ == "__main__":
    unittest.main()
