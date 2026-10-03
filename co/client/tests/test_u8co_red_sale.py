"""红字销售发票参照退货单（sale_invoice ← sale_return）的客户端正文与命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _gen_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen
from co.client.u8co_kinds import GENERATE_SOURCES, SOURCE_TYPES, check_source

_LINES = [{"source_line_id": 41, "quantity": 2, "cMemo": "红冲"}]


def _sent(draft: VoucherGen) -> tuple[dict, dict]:
    with _Running() as bridge:
        call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6)
        U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
        captured = bridge.capture
    return captured, json.loads(captured["body"].decode("utf-8"))


class RedSaleInvoiceTests(unittest.TestCase):
    def test_generate_with_lines(self) -> None:
        captured, sent = _sent(VoucherGen("sale_invoice", {"cVouchType": "26"}, _LINES, "sale_return"))
        _assert_signed(self, captured)
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("sale_invoice", 6, "sale_return"))
        self.assertEqual(sent["lines"], _LINES)

    def test_generate_whole_return_omits_lines(self) -> None:
        _, sent = _sent(VoucherGen("sale_invoice", None, None, "sale_return"))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type"])

    def test_blue_red_whole_invoice_omits_lines(self) -> None:
        # 红冲蓝字发票（source_type=sale_invoice），lines 可省略。
        _, sent = _sent(VoucherGen("sale_invoice", None, None, "sale_invoice"))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type"])
        self.assertEqual(sent["source_type"], "sale_invoice")
        self.assertEqual(check_source("sale_invoice", "sale_invoice"), "sale_invoice")

    def test_sources(self) -> None:
        self.assertEqual(GENERATE_SOURCES["sale_invoice"], ("dispatch", "sale_return", "sale_invoice"))
        self.assertEqual(check_source("sale_invoice", "sale_return"), "sale_return")
        self.assertIn("sale_return", SOURCE_TYPES)
        call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6)
        with self.assertRaises(ValueError):
            U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5).generate_voucher(
                call, VoucherGen("sale_invoice", None, None, "dispatch")
            )

    def test_cli_lines_optional_only_for_red(self) -> None:
        with _JsonFile({"head": {"cMemo": "x"}}) as path:
            red = _parser().parse_args(
                _argv("generate", "--type", "sale_invoice", "--id", "6", "--file", path, "--source-type", "sale_return"),
            )
            draft = _gen_draft(red)
            self.assertEqual((draft.source_type, draft.lines), ("sale_return", None))
            blue = _parser().parse_args(_argv("generate", "--type", "sale_invoice", "--id", "6", "--file", path))
            with self.assertRaises(SystemExit):
                _gen_draft(blue)


if __name__ == "__main__":
    unittest.main()
