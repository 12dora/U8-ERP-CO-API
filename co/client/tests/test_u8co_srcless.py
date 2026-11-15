"""无来源新增（发货单、先开票销售发票、到货单、材料出库单）和产成品入库参照生产订单：正文和命令行。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _draft, _gen_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherDraft, VoucherGen
from co.client.u8co_kinds import CREATABLE_KINDS, GENERATE_SOURCES, SRCLESS_CREATE_KINDS, check_source

_HEAD = {"cCusCode": "C001", "cSTCode": "01"}
_LINES = [{"cWhCode": "01", "cInvCode": "A001", "iQuantity": 2}]
_MO_LINES = [{"source_line_id": 1000000051, "quantity": 1}]


class SourcelessCreateTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        for kind in ("dispatch", "sale_invoice", "arrival", "material_out"):
            self.assertIn(kind, SRCLESS_CREATE_KINDS)
            self.assertIn(kind, CREATABLE_KINDS)
        self.assertEqual(len(CREATABLE_KINDS), len(set(CREATABLE_KINDS)))
        self.assertEqual(GENERATE_SOURCES["product_in"][-1], "production_order")
        self.assertEqual(GENERATE_SOURCES["product_in"][0], "qm_product_check")
        self.assertEqual(check_source("product_in", "production_order"), "production_order")

    def test_create_body(self) -> None:
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 0)
            U8CoClient(bridge.base_url, SECRET, timeout=5).create_voucher(call, VoucherDraft("dispatch", _HEAD, _LINES))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/create")
        self.assertEqual(list(sent), _AUTH + ["type", "head", "lines"])
        self.assertEqual((sent["type"], sent["head"], sent["lines"]), ("dispatch", _HEAD, _LINES))

    def test_cli_create_type(self) -> None:
        with _JsonFile({"head": _HEAD, "lines": _LINES}) as path:
            for kind in SRCLESS_CREATE_KINDS:
                draft = _draft(_parser().parse_args(_argv("create", "--type", kind, "--file", path)))
                self.assertEqual(draft.kind, kind)

    def test_generate_body_from_production_order(self) -> None:
        draft = VoucherGen("product_in", {"cWhCode": "10"}, _MO_LINES, "production_order")
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 1000000052)
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
            captured = bridge.capture
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("product_in", 1000000052, "production_order"))
        self.assertEqual(sent["lines"], _MO_LINES)

    def test_cli_generate_from_production_order(self) -> None:
        with _JsonFile({"lines": _MO_LINES}) as path:
            argv = _argv("generate", "--type", "product_in", "--id", "7", "--file", path, "--source-type", "production_order")
            draft = _gen_draft(_parser().parse_args(argv))
        self.assertEqual((draft.kind, draft.source_type), ("product_in", "production_order"))


if __name__ == "__main__":
    unittest.main()
