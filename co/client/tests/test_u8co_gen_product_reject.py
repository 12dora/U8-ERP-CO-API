"""产成品入库参照产品不良品处理单（qm_product_reject）：生单正文和命令行 --source-type。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _gen_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen

_LINES = [{"source_line_id": 42, "quantity": 15551}]


class RejectSourceTests(unittest.TestCase):
    def test_generate_body_carries_reject_source(self) -> None:
        draft = VoucherGen("product_in", {"cWhCode": "10"}, _LINES, "qm_product_reject")
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", 42)
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("product_in", 42, "qm_product_reject"))
        self.assertEqual(sent["lines"], _LINES)

    def test_reject_source_refused_for_other_targets(self) -> None:
        call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", 1)
        offline = U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)
        for kind in ("material_out", "purchase_in", "sale_out"):
            with self.assertRaises(ValueError) as caught:
                offline.generate_voucher(call, VoucherGen(kind, None, _LINES, "qm_product_reject"))
            self.assertIn("来源", str(caught.exception))

    def test_cli_source_type(self) -> None:
        with _JsonFile({"lines": _LINES}) as path:
            argv = _argv("generate", "--type", "product_in", "--id", "42", "--file", path, "--source-type", "qm_product_reject")
            draft = _gen_draft(_parser().parse_args(argv))
            check = _argv("generate", "--type", "product_in", "--id", "9", "--file", path, "--source-type", "qm_product_check")
            plain = _gen_draft(_parser().parse_args(check))
        self.assertEqual((draft.kind, draft.source_type), ("product_in", "qm_product_reject"))
        self.assertEqual(plain.source_type, "qm_product_check")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(
                    _argv("generate", "--type", "product_in", "--id", "9", "--file", "x", "--source-type", "qm_incoming_reject"),
                )
