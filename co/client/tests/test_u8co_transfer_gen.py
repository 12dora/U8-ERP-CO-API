"""调拨单参照调拨申请单的类型集合、正文和命令行。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _gen_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen
from co.client.u8co_kinds import GENERATABLE_KINDS, GENERATE_SOURCES, SOURCE_TYPES, check_source

_HEAD = {"dTVDate": "2026-09-28", "cMemo": "参照申请"}
_LINES = [{"source_line_id": 41, "quantity": 1}]


class TransferFromRequestTests(unittest.TestCase):
    def test_sets(self) -> None:
        self.assertIn("transfer", GENERATABLE_KINDS)
        self.assertEqual(GENERATE_SOURCES["transfer"], ("transfer_request",))
        self.assertIn("transfer_request", SOURCE_TYPES)
        self.assertEqual(check_source("transfer", "transfer_request"), "transfer_request")
        with self.assertRaises(ValueError):
            check_source("transfer", "purchase_order")

    def test_generate_body(self) -> None:
        draft = VoucherGen("transfer", _HEAD, _LINES, "transfer_request")
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 9)
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("transfer", 9, "transfer_request"))
        self.assertEqual((sent["head"], sent["lines"]), (_HEAD, _LINES))

    def test_cli(self) -> None:
        with _JsonFile({"head": _HEAD, "lines": _LINES}) as path:
            argv = _argv("generate", "--type", "transfer", "--id", "9", "--file", path, "--source-type", "transfer_request")
            draft = _gen_draft(_parser().parse_args(argv))
        self.assertEqual((draft.kind, draft.source_type, draft.lines), ("transfer", "transfer_request", _LINES))


if __name__ == "__main__":
    unittest.main()
