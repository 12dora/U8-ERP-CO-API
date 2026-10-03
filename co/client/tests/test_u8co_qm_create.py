"""报检单、检验单参照生单（检验单表头可带 items）、删除和报检单审核的客户端正文与命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _gen_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen
from co.client.u8co_kinds import (
    DELETABLE_KINDS,
    GENERATABLE_KINDS,
    GENERATE_SOURCES,
    VERIFIABLE_KINDS,
    WORKFLOW_KINDS,
    check_source,
)

_INSPECT = ("qm_incoming_inspect", "qm_product_inspect")
_CHECK = ("qm_incoming_check", "qm_product_check")
_ITEMS = [{"cChkItemCode": "I01", "cChkGuideCode": "G01", "cTargetQJug": "合格"}]
_HEAD = {"cCheckPersonCode": "P01", "project_code": "0000000001", "items": _ITEMS}
_LINE = [{"source_line_id": 21, "quantity": 1}]
# 产品报检单可单独弃审（桥只收 unverify），其余报检单、检验单仍不开放审核。
_NO_VERIFY = ("qm_incoming_inspect",) + _CHECK


def _call(action: str = "") -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-29", 7, action)


def _offline() -> U8CoClient:
    return U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)


class QmCreateTests(unittest.TestCase):
    def _generate(self, draft: VoucherGen) -> dict:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(_call(), draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        return json.loads(captured["body"].decode("utf-8"))

    def test_kind_lists_and_sources(self) -> None:
        for kind in _INSPECT + _CHECK:
            self.assertIn(kind, DELETABLE_KINDS)
            self.assertIn(kind, GENERATABLE_KINDS)
        # 来料报检单不开放单独审核、弃审（U8 只在保存时自动审核）；产品报检单只开弃审；检验单走 workflow/*。
        for kind in _NO_VERIFY:
            self.assertNotIn(kind, VERIFIABLE_KINDS)
        self.assertIn("qm_product_inspect", VERIFIABLE_KINDS)
        for kind in _CHECK:
            self.assertIn(kind, WORKFLOW_KINDS)
        self.assertEqual(GENERATE_SOURCES["qm_incoming_inspect"], ("arrival",))
        self.assertEqual(GENERATE_SOURCES["qm_product_inspect"], ("production_order",))
        self.assertEqual(check_source("qm_incoming_check", "qm_incoming_inspect"), "qm_incoming_inspect")
        self.assertEqual(check_source("qm_product_check", "qm_product_inspect"), "qm_product_inspect")
        with self.assertRaises(ValueError):
            check_source("qm_product_check", "qm_incoming_inspect")

    def test_inspect_generate_body(self) -> None:
        lines = [{"source_line_id": 11, "quantity": 2, "cWhCode": "01"}]
        sent = self._generate(VoucherGen("qm_incoming_inspect", {"cDepCode": "D01"}, lines, "arrival"))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("qm_incoming_inspect", 7, "arrival"))
        self.assertEqual(sent["lines"], lines)

    def test_check_generate_keeps_the_items_list(self) -> None:
        sent = self._generate(VoucherGen("qm_incoming_check", _HEAD, _LINE))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "head", "lines"])
        self.assertEqual(sent["head"], _HEAD)
        self.assertEqual(sent["lines"], _LINE)

    def test_items_are_refused_where_they_do_not_belong(self) -> None:
        bad = (
            VoucherGen("qm_incoming_inspect", {"items": _ITEMS}, _LINE),
            VoucherGen("arrival", {"items": _ITEMS}, _LINE),
            VoucherGen("qm_incoming_check", {"cCheckPersonCode": "P01", "items": []}, _LINE),
            VoucherGen("qm_incoming_check", {"cCheckPersonCode": "P01", "items": _ITEMS * 51}, _LINE),
            VoucherGen("qm_incoming_check", {"cCheckPersonCode": "P01", "items": [{"cChkItemCode": ["x"]}]}, _LINE),
            VoucherGen("qm_incoming_check", {"cCheckPersonCode": "P01", "items": ["I01"]}, _LINE),
            VoucherGen("qm_incoming_check", {"items": _ITEMS, "Items": _ITEMS}, _LINE),
            VoucherGen("qm_incoming_check", {"cMemo": ["x"], "items": _ITEMS}, _LINE),
        )
        for draft in bad:
            with self.assertRaises(ValueError, msg=repr(draft.head)):
                _offline().generate_voucher(_call(), draft)

    def test_delete_body_and_verify_refused(self) -> None:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).delete_voucher(_call(), "qm_incoming_inspect")
            deleted = json.loads(bridge.capture["body"].decode("utf-8"))
        self.assertEqual((deleted["type"], deleted["id"]), ("qm_incoming_inspect", 7))
        for kind in _NO_VERIFY:
            for action in ("verify", "unverify"):
                with self.assertRaises(ValueError):
                    _offline().verify_voucher(_call(action), kind)
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).verify_voucher(_call("unverify"), "qm_product_inspect")
            captured = bridge.capture
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/verify")
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual((sent["type"], sent["id"], sent["action"]), ("qm_product_inspect", 7, "unverify"))

    def test_cli_accepts_the_kinds(self) -> None:
        for kind in _INSPECT + _CHECK:
            parsed = _parser().parse_args(_argv("delete", "--type", kind, "--id", "7"))
            self.assertEqual(parsed.type, kind)
        with _JsonFile({"head": _HEAD, "lines": _LINE}) as path:
            argv = _argv("generate", "--type", "qm_product_check", "--id", "7", "--file", path)
            parsed = _parser().parse_args(argv + ["--source-type", "qm_product_inspect"])
            draft = _gen_draft(parsed)
        self.assertEqual((draft.kind, draft.source_type), ("qm_product_check", "qm_product_inspect"))
        self.assertEqual(draft.head["items"], _ITEMS)
        for kind in _NO_VERIFY:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    _parser().parse_args(_argv("verify", "--type", kind, "--id", "7", "--action", "verify"))
        parsed = _parser().parse_args(_argv("verify", "--type", "qm_product_inspect", "--id", "7", "--action", "unverify"))
        self.assertEqual((parsed.type, parsed.action), ("qm_product_inspect", "unverify"))
