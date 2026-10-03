"""票据处理：note_process 的字段与本地校验、路由在写路由表里（可带幂等键、可预演）；处理制单的 expense_code。"""

from __future__ import annotations

import dataclasses
import unittest

from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.tests.test_u8co_update_close_gen import _AUTH
from co.client.u8co_arap_proc import ProcVoucher, proc_voucher_fields
from co.client.u8co_idem import WRITE_ROUTES
from co.client.u8co_notes_proc import NoteProcess, note_process_fields

_SETTLE = NoteProcess(op="settle", note="N2026001", bank_code="100201")
_DISCOUNT = NoteProcess(
    op="discount", note=3416, amount=1000, bank_code="100201", expense=12.5, interest=0, rate=2.35, digest="贴现"
)
_ENDORSE = NoteProcess(
    op="endorse", note="N2026001", amount=800, vendor="V001", ap_lines=[{"type": "P0", "id": "YF1", "amount": 800}]
)
_RETURN = NoteProcess(op="return", note="N2026001")


class NoteProcWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_settle(self) -> None:
        path, sent = self._send(lambda client: client.note_process(gl_arc._call(), _SETTLE))
        self.assertEqual(path, "/u8co/v1/notes/process")
        self.assertEqual(list(sent), _AUTH + ["flag", "op", "note", "bank_code"])
        self.assertEqual((sent["flag"], sent["op"], sent["note"]), ("AR", "settle", "N2026001"))

    def test_discount_fields_in_order(self) -> None:
        _path, sent = self._send(lambda client: client.note_process(gl_arc._call(), _DISCOUNT))
        tail = ["flag", "op", "note", "amount", "bank_code", "expense", "interest", "rate", "digest"]
        self.assertEqual(list(sent), _AUTH + tail)
        self.assertEqual((sent["note"], sent["rate"]), (3416, 2.35))

    def test_endorse_and_sub_range(self) -> None:
        ask = dataclasses.replace(_ENDORSE, sub_start=1, sub_end=80000)
        _path, sent = self._send(lambda client: client.note_process(gl_arc._call(), ask))
        self.assertEqual(list(sent), _AUTH + ["flag", "op", "note", "amount", "sub_start", "sub_end", "vendor", "ap_lines"])
        self.assertEqual(sent["ap_lines"], [{"type": "P0", "id": "YF1", "amount": 800}])

    def test_return_sends_common_fields_only(self) -> None:
        ask = dataclasses.replace(_RETURN, sub_start=1, sub_end=10000000, amount=100000, digest="退回")
        _path, sent = self._send(lambda client: client.note_process(gl_arc._call(), ask))
        self.assertEqual(list(sent), _AUTH + ["flag", "op", "note", "amount", "sub_start", "sub_end", "digest"])
        self.assertEqual((sent["op"], sent["note"]), ("return", "N2026001"))

    def test_route_is_write(self) -> None:
        self.assertIn("/v1/notes/process", WRITE_ROUTES)

    def test_ap_settle_and_return(self) -> None:
        # 应付票据只收结算、退回。
        sent = note_process_fields(gl_arc._call(), dataclasses.replace(_SETTLE, flag="AP"))
        self.assertEqual((sent["flag"], sent["op"]), ("AP", "settle"))
        sent = note_process_fields(gl_arc._call(), dataclasses.replace(_RETURN, flag="AP"))
        self.assertEqual(list(sent)[-3:], ["flag", "op", "note"])
        self.assertEqual(sent["flag"], "AP")

    def test_voucher_expense_code(self) -> None:
        ask = ProcVoucher(flag="AR", cancel_nos=["PJTAR000000000001"], expense_code="660301")
        sent = proc_voucher_fields(gl_arc._call(), ask)
        self.assertEqual(list(sent)[-2:], ["cancel_nos", "expense_code"])


class NoteProcLocalTests(unittest.TestCase):
    def test_bad_asks_are_refused_locally(self) -> None:
        cases = (
            (_SETTLE, {"op": "transfer"}),
            (_SETTLE, {"op": "return"}),
            (_RETURN, {"vendor": "V001"}),
            (_RETURN, {"expense": 1}),
            (_SETTLE, {"note": 0}),
            (_SETTLE, {"note": True}),
            (_SETTLE, {"note": "a\nb"}),
            (_SETTLE, {"bank_code": None}),
            (_SETTLE, {"amount": 0}),
            (_SETTLE, {"amount": 1.005}),
            (_SETTLE, {"expense": 1}),
            (_SETTLE, {"vendor": "V001"}),
            (_SETTLE, {"sub_start": 1}),
            (_SETTLE, {"sub_start": 5, "sub_end": 4}),
            (_SETTLE, {"sub_start": 1, "sub_end": 100, "amount": 2}),
            (_DISCOUNT, {"rate": 101}),
            (_DISCOUNT, {"rate": 1.0000001}),
            (_DISCOUNT, {"expense": -1}),
            (_ENDORSE, {"bank_code": "100201"}),
            (_ENDORSE, {"vendor": None}),
            (_ENDORSE, {"ap_lines": []}),
            (_ENDORSE, {"ap_lines": [{"type": "49", "id": "FK1", "amount": 800}]}),
            (_ENDORSE, {"amount": 700}),
            (_DISCOUNT, {"flag": "AP"}),
            (_ENDORSE, {"flag": "AP"}),
            (_SETTLE, {"flag": "ap"}),
        )
        for base, change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                note_process_fields(gl_arc._call(), dataclasses.replace(base, **change))


if __name__ == "__main__":
    unittest.main()
