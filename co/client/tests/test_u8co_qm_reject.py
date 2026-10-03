"""来料 / 产品不良品处理单（QM05 / QM06）的客户端：类型清单、生单正文、审核与删除、命令行解析。只监听 127.0.0.1。"""

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
    KIND_NAMES,
    VERIFIABLE_KINDS,
    WORKFLOW_KINDS,
    check_source,
)

_KINDS = ("qm_incoming_reject", "qm_product_reject")
_LINES = [
    {"source_line_id": 7, "quantity": 0.5, "cScrapDisCode": "Sys01", "cReasonCode": "01"},
    {"source_line_id": 7, "quantity": 0.5, "cScrapDisCode": "Sys03", "cReasonCode": "01", "cDimInvCode": "I02"},
]


def _call(action: str = "") -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-10-02", 7, action)


def _sent(invoke) -> tuple[str, dict]:
    with _Running() as bridge:
        invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
        captured = bridge.capture
    return captured["path"], json.loads(captured["body"].decode("utf-8"))


class QmRejectTests(unittest.TestCase):
    def test_kind_lists_and_sources(self) -> None:
        for kind in _KINDS:
            self.assertIn(kind, DELETABLE_KINDS)
            self.assertIn(kind, GENERATABLE_KINDS)
            self.assertIn(kind, VERIFIABLE_KINDS)
            self.assertIn(kind, WORKFLOW_KINDS)
        self.assertEqual(VERIFIABLE_KINDS, tuple(name for name in KIND_NAMES if name in VERIFIABLE_KINDS))
        self.assertEqual(GENERATE_SOURCES["qm_incoming_reject"], ("qm_incoming_check",))
        self.assertEqual(GENERATE_SOURCES["qm_product_reject"], ("qm_product_check",))
        self.assertEqual(check_source("qm_product_reject", "qm_product_check"), "qm_product_check")
        with self.assertRaises(ValueError):
            check_source("qm_product_reject", "qm_incoming_check")

    def test_generate_body(self) -> None:
        head = {"dDate": "2026-10-02", "chDefine15": "SEED"}
        draft = VoucherGen("qm_product_reject", head, _LINES, "qm_product_check")
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(_call(), draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("qm_product_reject", 7, "qm_product_check"))
        self.assertEqual(sent["lines"], _LINES)
        self.assertEqual(sent["head"], {"dDate": "2026-10-02", "chDefine15": "SEED"})

    def test_verify_and_delete_bodies(self) -> None:
        path, sent = _sent(lambda client: client.verify_voucher(_call("unverify"), "qm_incoming_reject"))
        self.assertEqual(path, "/u8co/v1/vouchers/verify")
        self.assertEqual((sent["type"], sent["id"], sent["action"]), ("qm_incoming_reject", 7, "unverify"))
        path, sent = _sent(lambda client: client.delete_voucher(_call(), "qm_product_reject"))
        self.assertEqual(path, "/u8co/v1/vouchers/delete")
        self.assertEqual((sent["type"], sent["id"]), ("qm_product_reject", 7))

    def test_cli_accepts_the_reject_kinds(self) -> None:
        with _JsonFile({"lines": _LINES}) as path:
            argv = _argv("generate", "--type", "qm_incoming_reject", "--id", "7", "--file", path)
            draft = _gen_draft(_parser().parse_args(argv))
        self.assertEqual((draft.kind, draft.lines), ("qm_incoming_reject", _LINES))
        verify = _parser().parse_args(_argv("verify", "--type", "qm_product_reject", "--id", "7", "--action", "verify"))
        self.assertEqual(verify.type, "qm_product_reject")
        gone = _parser().parse_args(_argv("delete", "--type", "qm_incoming_reject", "--id", "7"))
        self.assertEqual(gone.type, "qm_incoming_reject")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("update", "--type", "qm_incoming_reject", "--id", "7", "--file", "x"))


if __name__ == "__main__":
    unittest.main()
