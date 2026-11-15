"""红字采购入库参照采购退货单、红字采购发票参照红字入库单的客户端正文与命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _gen_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen
from co.client.u8co_kinds import GENERATE_SOURCES, SOURCE_TYPES, check_source

_LINES = [{"source_line_id": 41, "quantity": 1}]


def _offline() -> U8CoClient:
    return U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)


class RedPurchaseTests(unittest.TestCase):
    def test_sources(self) -> None:
        self.assertEqual(GENERATE_SOURCES["purchase_in"][0], "purchase_order")
        self.assertIn("purchase_return", GENERATE_SOURCES["purchase_in"])
        self.assertIn("purchase_return", SOURCE_TYPES)
        self.assertEqual(check_source("purchase_in", "purchase_return"), "purchase_return")
        call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 9)
        for kind in ("purchase_invoice", "arrival"):
            with self.assertRaises(ValueError) as caught:
                _offline().generate_voucher(call, VoucherGen(kind, None, _LINES, "purchase_return"))
            self.assertIn("来源", str(caught.exception))

    def test_generate_red_purchase_in_body(self) -> None:
        draft = VoucherGen("purchase_in", {"cWhCode": "01"}, _LINES, "purchase_return")
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 9)
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("purchase_in", 9, "purchase_return"))
        self.assertEqual(sent["lines"], _LINES)

    def test_cli_source_type(self) -> None:
        with _JsonFile({"head": {"cWhCode": "01"}, "lines": _LINES}) as path:
            parsed = _parser().parse_args(
                _argv("generate", "--type", "purchase_in", "--id", "9", "--file", path, "--source-type", "purchase_return"),
            )
            self.assertEqual(_gen_draft(parsed).source_type, "purchase_return")
            invoice = _parser().parse_args(_argv("generate", "--type", "purchase_invoice", "--id", "9", "--file", path))
            self.assertEqual(_gen_draft(invoice).source_type, "")


if __name__ == "__main__":
    unittest.main()
