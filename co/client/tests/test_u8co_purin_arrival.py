"""蓝字采购入库参照到货单（source_type=arrival，lines 可省）的客户端正文与命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _gen_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen
from co.client.u8co_kinds import GENERATE_SOURCES, SOURCE_TYPES, check_source, whole_generate


def _sent(draft: VoucherGen) -> dict:
    with _Running() as bridge:
        call = U8Call("801", "2026", "op001", PASSWORD, "2026-10-03", 7)
        U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
        captured = bridge.capture
    return captured


class PurchaseInArrivalTests(unittest.TestCase):
    def test_sources(self) -> None:
        self.assertEqual(GENERATE_SOURCES["purchase_in"][0], "purchase_order")
        self.assertIn("arrival", GENERATE_SOURCES["purchase_in"])
        self.assertIn("arrival", SOURCE_TYPES)
        self.assertEqual(check_source("purchase_in", "arrival"), "arrival")
        self.assertTrue(whole_generate("purchase_in", "arrival"))
        self.assertFalse(whole_generate("purchase_in", "purchase_order") or whole_generate("purchase_in"))

    def test_whole_arrival_body_has_no_lines(self) -> None:
        captured = _sent(VoucherGen("purchase_in", {"cWhCode": "01"}, None, "arrival"))
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("purchase_in", 7, "arrival"))

    def test_partial_arrival_body_keeps_lines(self) -> None:
        lines = [{"source_line_id": 31, "quantity": 1, "cposition": "A01"}]
        sent = json.loads(_sent(VoucherGen("purchase_in", {"cWhCode": "01"}, lines, "arrival"))["body"].decode("utf-8"))
        self.assertEqual(sent["lines"], lines)

    def test_cli_without_lines(self) -> None:
        with _JsonFile({"head": {"cWhCode": "01"}}) as path:
            parsed = _parser().parse_args(
                _argv("generate", "--type", "purchase_in", "--id", "7", "--file", path, "--source-type", "arrival"),
            )
            draft = _gen_draft(parsed)
            self.assertEqual((draft.source_type, draft.lines), ("arrival", None))
            plain = _parser().parse_args(_argv("generate", "--type", "purchase_in", "--id", "7", "--file", path))
            with self.assertRaises(SystemExit):
                _gen_draft(plain)


if __name__ == "__main__":
    unittest.main()
