"""契约 B7：开户银行、项目的新增、修改、删除的正文和命令行（项目被引用时由桥拒绝删除）。"""

from __future__ import annotations

import contextlib
import io
import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import DELETE_ARCHIVES, WRITE_ARCHIVES, ArcRecord

_BANK = {"name": "测试开户行", "account": "999999999999", "cbankcode": "01", "ccurrencyname": "人民币"}


class BankProjectWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_bank_create_update_delete(self) -> None:
        path, sent = self._send(lambda client: client.arc_create(gl_arc._call(), ArcRecord("bank", "99", _BANK)))
        self.assertEqual(path, "/u8co/v1/archives/create")
        self.assertEqual(list(sent), _AUTH + ["archive", "code", "fields"])
        self.assertEqual(sent["fields"], _BANK)
        path, sent = self._send(lambda client: client.arc_update(gl_arc._call(), ArcRecord("bank", "99", {"flag": True})))
        self.assertEqual((path, sent["fields"]), ("/u8co/v1/archives/update", {"flag": True}))
        path, sent = self._send(lambda client: client.arc_delete(gl_arc._call(), "bank", "99"))
        self.assertEqual((path, sent["archive"], sent["code"]), ("/u8co/v1/archives/delete", "bank", "99"))

    def test_project_create_update_delete(self) -> None:
        record = ArcRecord("project", "00:9901", {"name": "测试项目", "citemccode": "01"})
        path, sent = self._send(lambda client: client.arc_create(gl_arc._call(), record))
        self.assertEqual((path, sent["code"]), ("/u8co/v1/archives/create", "00:9901"))
        update = ArcRecord("project", "00:9901", {"bclose": True})
        path, sent = self._send(lambda client: client.arc_update(gl_arc._call(), update))
        self.assertEqual((path, sent["fields"]), ("/u8co/v1/archives/update", {"bclose": True}))
        path, sent = self._send(lambda client: client.arc_delete(gl_arc._call(), "project", "00:9901"))
        self.assertEqual((path, list(sent)), ("/u8co/v1/archives/delete", _AUTH + ["archive", "code"]))
        self.assertEqual((sent["archive"], sent["code"]), ("project", "00:9901"))


class BankProjectValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_kind_lists(self) -> None:
        self.assertTrue({"bank", "project"} <= set(WRITE_ARCHIVES))
        self.assertIn("bank", DELETE_ARCHIVES)
        self.assertIn("project", DELETE_ARCHIVES)


class BankProjectCliTests(unittest.TestCase):
    def test_write_choices(self) -> None:
        made = _parser().parse_args(_argv("arc-create", "--archive", "project", "--code", "00:01", "--file", "x.json"))
        self.assertEqual((made.archive, made.code), ("project", "00:01"))
        gone = _parser().parse_args(_argv("arc-delete", "--archive", "bank", "--code", "99"))
        self.assertEqual(gone.archive, "bank")
        dropped = _parser().parse_args(_argv("arc-delete", "--archive", "project", "--code", "00:01"))
        self.assertEqual((dropped.archive, dropped.code), ("project", "00:01"))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("arc-delete", "--archive", "trade_class", "--code", "1"))


if __name__ == "__main__":
    unittest.main()
