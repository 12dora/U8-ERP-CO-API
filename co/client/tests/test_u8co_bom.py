"""物料清单（bom）的类型集合、新增 / 修改 / 审核 / 删除正文、幂等键和命令行 --type。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_arap_last, _assert_signed, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherDraft, VoucherEdit
from co.client.u8co_kinds import (
    CLOSABLE_KINDS,
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    GENERATABLE_KINDS,
    KIND_NAMES,
    UPDATABLE_KINDS,
    VERIFIABLE_KINDS,
)

_KIND = "bom"
_HEAD = {"inv_code": "P001", "version_desc": "测试"}
_LINES = [{"inv_code": "C001", "base_qty_n": 2}, {"inv_code": "C002", "base_qty_n": 1, "base_qty_d": 1000}]
_EDIT = [
    {"op": "update", "sort_seq": 10, "base_qty_n": 3},
    {"op": "delete", "sort_seq": 20},
    {"op": "add", "inv_code": "C003", "base_qty_n": 1},
]


def _call(doc_id: int, action: str = "") -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", doc_id, action)


def _sent(invoke) -> tuple[dict, dict]:
    with _Running() as bridge:
        invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
        captured = bridge.capture
    return json.loads(captured["body"].decode("utf-8")), captured


class BomKindTests(unittest.TestCase):
    def test_kind_sets(self) -> None:
        for group in (KIND_NAMES, CREATABLE_KINDS, DELETABLE_KINDS, UPDATABLE_KINDS, VERIFIABLE_KINDS):
            self.assertIn(_KIND, group)
        self.assertNotIn(_KIND, CLOSABLE_KINDS)
        self.assertNotIn(_KIND, GENERATABLE_KINDS)
        _assert_arap_last(self)


class BomWireTests(unittest.TestCase):
    def test_create_body_and_idempotency_key(self) -> None:
        sent, captured = _sent(
            lambda client: client.create_voucher(_call(0), VoucherDraft(_KIND, _HEAD, _LINES), idempotency_key="bom-0001")
        )
        _assert_signed(self, captured)
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/create")
        self.assertEqual(list(sent), _AUTH + ["type", "head", "lines", "idempotency_key"])
        self.assertEqual((sent["type"], sent["head"], sent["lines"]), (_KIND, _HEAD, _LINES))
        self.assertEqual(sent["idempotency_key"], "bom-0001")

    def test_update_keeps_sort_seq_rows(self) -> None:
        sent, captured = _sent(lambda client: client.update_voucher(_call(9), VoucherEdit(_KIND, {"eff_date": "2026-10-01"}, _EDIT)))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/update")
        self.assertEqual((sent["type"], sent["id"]), (_KIND, 9))
        self.assertEqual(sent["lines"], _EDIT)
        self.assertEqual(sent["head"], {"eff_date": "2026-10-01"})

    def test_verify_and_delete_bodies(self) -> None:
        sent, captured = _sent(lambda client: client.verify_voucher(_call(9, "unverify"), _KIND))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/verify")
        self.assertEqual((sent["type"], sent["id"], sent["action"]), (_KIND, 9, "unverify"))
        sent, captured = _sent(lambda client: client.delete_voucher(_call(9), _KIND))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/delete")
        self.assertEqual((sent["type"], sent["id"]), (_KIND, 9))

    def test_cli_accepts_bom(self) -> None:
        parser = _parser()
        created = parser.parse_args(
            _argv("create", "--type", _KIND, "--head-json", json.dumps(_HEAD), "--lines-json", json.dumps(_LINES))
        )
        self.assertEqual(created.type, _KIND)
        for command in ("load", "delete"):
            parsed = parser.parse_args(_argv(command, "--type", _KIND, "--id", "9"))
            self.assertEqual((parsed.type, parsed.id), (_KIND, 9))
        with self.assertRaises(SystemExit):
            parser.parse_args(_argv("close", "--type", _KIND, "--id", "9", "--action", "close"))


if __name__ == "__main__":
    unittest.main()
