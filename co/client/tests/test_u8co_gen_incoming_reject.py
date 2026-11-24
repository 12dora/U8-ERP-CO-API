"""采购入库参照来料不良品处理单（qm_incoming_reject）：生单正文、其他目标拒绝该来源和命令行 --source-type。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _gen_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen

_LINES = [{"source_line_id": 11, "quantity": 3}, {"source_line_id": 12, "quantity": 5, "cbatch": "B1"}]
_HEAD = {"cWhCode": "01"}


class IncomingRejectSourceTests(unittest.TestCase):
    def test_generate_body_carries_reject_source(self) -> None:
        draft = VoucherGen("purchase_in", _HEAD, _LINES, "qm_incoming_reject")
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", 7)
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("purchase_in", 7, "qm_incoming_reject"))
        self.assertEqual((sent["head"], sent["lines"]), (_HEAD, _LINES))

    def test_reject_source_refused_for_other_targets(self) -> None:
        call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", 1)
        offline = U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)
        for kind in ("product_in", "material_out", "sale_out"):
            with self.assertRaises(ValueError) as caught:
                offline.generate_voucher(call, VoucherGen(kind, None, _LINES, "qm_incoming_reject"))
            self.assertIn("来源", str(caught.exception))

    def test_cli_source_type(self) -> None:
        with _JsonFile({"head": _HEAD, "lines": _LINES}) as path:
            argv = _argv(
                "generate", "--type", "purchase_in", "--id", "7", "--file", path, "--source-type", "qm_incoming_reject",
            )
            draft = _gen_draft(_parser().parse_args(argv))
        self.assertEqual((draft.kind, draft.source_type), ("purchase_in", "qm_incoming_reject"))
        self.assertEqual(draft.lines, _LINES)


if __name__ == "__main__":
    unittest.main()
