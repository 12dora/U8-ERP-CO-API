"""收付款协议 pay_agreement 可新增、修改、删除（受控 SQL）：请求形状、编码长度和命令行可选值。"""

from __future__ import annotations

import contextlib
import io
import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import (
    DELETE_ARCHIVES,
    READ_ARCHIVES,
    SA_WRITE_ARCHIVES,
    UPDATE_ARCHIVES,
    WRITE_ARCHIVES,
    ArcRecord,
)

_NEW = {"cName": "协议A", "iLZYJ": 0, "dblZQNum": 30}


class AgreementWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_create_update_delete(self) -> None:
        record = ArcRecord("pay_agreement", "Z01", _NEW)
        path, sent = self._send(lambda client: client.arc_create(gl_arc._call(), record))
        self.assertEqual(path, "/u8co/v1/archives/create")
        self.assertEqual(list(sent), _AUTH + ["archive", "code", "fields"])
        self.assertEqual((sent["archive"], sent["code"], sent["fields"]), ("pay_agreement", "Z01", _NEW))
        update = ArcRecord("pay_agreement", "Z01", {"dEDate": "2026-12-31"})
        path, sent = self._send(lambda client: client.arc_update(gl_arc._call(), update))
        self.assertEqual((path, sent["fields"]), ("/u8co/v1/archives/update", {"dEDate": "2026-12-31"}))
        path, sent = self._send(lambda client: client.arc_delete(gl_arc._call(), "pay_agreement", "Z01"))
        self.assertEqual((path, sent["archive"], sent["code"]), ("/u8co/v1/archives/delete", "pay_agreement", "Z01"))


class AgreementValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_kind_lists(self) -> None:
        self.assertEqual(SA_WRITE_ARCHIVES, ("pay_agreement",))
        for kinds in (READ_ARCHIVES, WRITE_ARCHIVES, UPDATE_ARCHIVES, DELETE_ARCHIVES):
            self.assertIn("pay_agreement", kinds)

    def test_code_is_at_most_10(self) -> None:
        record = ArcRecord("pay_agreement", "1" * 11, _NEW)
        self._refused(lambda c: c.arc_create(gl_arc._call(), record), "code")
        self._refused(lambda c: c.arc_delete(gl_arc._call(), "pay_agreement", "1" * 61), "code")

    def test_template_refused(self) -> None:
        record = ArcRecord("pay_agreement", "Z01", _NEW, "001")
        self._refused(lambda c: c.arc_create(gl_arc._call(), record), "template")


class AgreementCliTests(unittest.TestCase):
    def test_write_choices(self) -> None:
        for command in ("arc-create", "arc-update"):
            made = _parser().parse_args(_argv(command, "--archive", "pay_agreement", "--code", "Z01", "--file", "x.json"))
            self.assertEqual(made.archive, "pay_agreement")
        gone = _parser().parse_args(_argv("arc-delete", "--archive", "pay_agreement", "--code", "Z01"))
        self.assertEqual(gone.archive, "pay_agreement")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("arc-delete", "--archive", "customer_invoice_company", "--code", "C001:C002"))


if __name__ == "__main__":
    unittest.main()
