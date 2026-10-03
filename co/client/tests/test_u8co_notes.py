"""票据只读：note_get 的字段（id 或票据号）与本地校验，list_vouchers 收 ar_note / ap_note，票据不进 KIND_NAMES。"""

from __future__ import annotations

import dataclasses
import unittest

from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.u8co_cli import _parser
from co.client.u8co_kinds import KIND_NAMES, NOTE_KINDS
from co.client.u8co_notes_read import note_get_fields
from co.client.u8co_gl_arc import VoucherQuery


class NoteWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_note_get_by_id(self) -> None:
        path, sent = self._send(lambda client: client.note_get(gl_arc._call(), "ar_note", 3416))
        self.assertEqual(path, "/u8co/v1/notes/get")
        self.assertEqual(list(sent), _AUTH + ["type", "id"])
        self.assertEqual((sent["type"], sent["id"]), ("ar_note", 3416))

    def test_note_get_by_code(self) -> None:
        path, sent = self._send(lambda client: client.note_get(gl_arc._call(), "ap_note", "PJ0001"))
        self.assertEqual(path, "/u8co/v1/notes/get")
        self.assertEqual(list(sent), _AUTH + ["type", "code"])
        self.assertEqual(sent["code"], "PJ0001")

    def test_list_accepts_note_kinds(self) -> None:
        query = VoucherQuery("ap_note", keys_only=True, limit=500)
        path, sent = self._send(lambda client: client.list_vouchers(gl_arc._call(), query))
        self.assertEqual(path, "/u8co/v1/vouchers/list")
        self.assertEqual((sent["type"], sent["keys_only"]), ("ap_note", True))


class NoteLocalTests(unittest.TestCase):
    def test_bad_keys_are_refused_locally(self) -> None:
        for kind, key in (
            ("ar_bill", 1), ("ar_note", 0), ("ar_note", 2147483648), ("ar_note", True), ("ar_note", ""),
            ("ar_note", "x" * 61), ("ar_note", "a\nb"), ("ar_note", 1.0),
        ):
            with self.subTest(kind=kind, key=key), self.assertRaises(ValueError):
                note_get_fields(gl_arc._call(), kind, key)

    def test_note_kinds_stay_out_of_voucher_kinds(self) -> None:
        self.assertEqual(NOTE_KINDS, ("ar_note", "ap_note"))
        self.assertFalse(set(NOTE_KINDS) & set(KIND_NAMES))
        with self.assertRaisesRegex(ValueError, "未知的单据类型"):
            gl_arc._offline().load_voucher(dataclasses.replace(gl_arc._call(), doc_id=1), "ar_note")

    def test_cli_list_accepts_note_kinds(self) -> None:
        listing = _parser().parse_args(_argv("list", "--type", "ar_note", "--keys-only"))
        self.assertEqual(listing.type, "ar_note")


if __name__ == "__main__":
    unittest.main()
