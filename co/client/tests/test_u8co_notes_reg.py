"""应收票据登记、删除（应付票据用 flag AP）：note_create / note_delete 的字段与本地校验，两个路由都在写路由表里
（可带幂等键、可预演）。"""

from __future__ import annotations

import dataclasses
import unittest

from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.tests.test_u8co_update_close_gen import _AUTH
from co.client.u8co_idem import WRITE_ROUTES
from co.client.u8co_notes_reg import NoteRegistration, note_create_fields, note_delete_fields

_NOTE = NoteRegistration(
    note_no="N2026001",
    settle_code="301",
    amount=1000.0,
    sign_date="2026-08-01",
    receipt_date="2026-08-10",
    expire_date="2027-02-01",
    customer="C001",
    dept="D01",
    receiver="示例科技有限公司",
)
_REQUIRED = [
    "flag", "note_no", "settle_code", "amount", "sign_date", "receipt_date", "expire_date", "customer", "dept", "receiver",
]


class NoteRegWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_note_create(self) -> None:
        path, sent = self._send(lambda client: client.note_create(gl_arc._call(), _NOTE))
        self.assertEqual(path, "/u8co/v1/notes/create")
        self.assertEqual(list(sent), _AUTH + _REQUIRED)
        self.assertEqual((sent["flag"], sent["amount"]), ("AR", 1000.0))

    def test_note_create_optional_fields(self) -> None:
        ask = dataclasses.replace(_NOTE, person="P01", digest="收到承兑")
        sent = note_create_fields(gl_arc._call(), ask)
        self.assertEqual(list(sent)[-2:], ["person", "digest"])

    def test_note_create_split(self) -> None:
        ask = dataclasses.replace(_NOTE, amount=100, km="112201", sub_start=900000001, sub_end=900010000)
        sent = note_create_fields(gl_arc._call(), ask)
        self.assertEqual(list(sent)[-3:], ["km", "sub_start", "sub_end"])
        self.assertEqual((sent["sub_start"], sent["sub_end"]), (900000001, 900010000))
        self.assertNotIn("sub_start", note_create_fields(gl_arc._call(), _NOTE))

    def test_note_delete_by_id_and_code(self) -> None:
        path, sent = self._send(lambda client: client.note_delete(gl_arc._call(), 3416))
        self.assertEqual(path, "/u8co/v1/notes/delete")
        self.assertEqual(list(sent), _AUTH + ["flag", "id"])
        sent = note_delete_fields(gl_arc._call(), "N1")
        self.assertEqual((sent["flag"], sent["note_no"]), ("AR", "N1"))

    def test_ap_note_create_and_delete(self) -> None:
        ask = dataclasses.replace(_NOTE, flag="AP", customer=None, vendor="V001", note_km="2201")
        sent = note_create_fields(gl_arc._call(), ask)
        want = [k if k != "customer" else "vendor" for k in _REQUIRED] + ["note_km"]
        self.assertEqual(list(sent)[-len(want) :], want)
        self.assertEqual((sent["flag"], sent["vendor"]), ("AP", "V001"))
        sent = note_delete_fields(gl_arc._call(), 3416, "AP")
        self.assertEqual((sent["flag"], sent["id"]), ("AP", 3416))

    def test_routes_are_writes(self) -> None:
        self.assertLessEqual({"/v1/notes/create", "/v1/notes/delete"}, WRITE_ROUTES)


class NoteRegLocalTests(unittest.TestCase):
    def test_bad_create_is_refused_locally(self) -> None:
        for change in (
            {"amount": 0}, {"amount": 1.005}, {"amount": True}, {"sign_date": "2026/08/01"},
            {"expire_date": "2026-07-01"}, {"receipt_date": "2026-07-30"}, {"receiver": " "}, {"digest": "a\nb"},
            {"note_no": "x" * 61}, {"settle_code": "3011"},
            {"sub_start": 1}, {"sub_end": 100000}, {"sub_start": 1, "sub_end": 200000},
            {"sub_start": 2, "sub_end": 1}, {"sub_start": 0, "sub_end": 99999}, {"sub_start": True, "sub_end": 100000},
            {"sub_start": "1", "sub_end": 100000}, {"sub_start": 10**15, "sub_end": 10**15 + 99999},
            {"flag": "XX"}, {"vendor": "V001"}, {"customer": None}, {"flag": "AP"}, {"flag": "AP", "customer": None},
            {"note_km": " "},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                note_create_fields(gl_arc._call(), dataclasses.replace(_NOTE, **change))

    def test_bad_delete_key_is_refused_locally(self) -> None:
        for key in (0, 2147483648, True, "", "a\nb", 1.0):
            with self.subTest(key=key), self.assertRaises(ValueError):
                note_delete_fields(gl_arc._call(), key)
        with self.assertRaises(ValueError):
            note_delete_fields(gl_arc._call(), 1, "XX")


if __name__ == "__main__":
    unittest.main()
