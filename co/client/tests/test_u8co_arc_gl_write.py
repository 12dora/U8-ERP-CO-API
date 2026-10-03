"""币种、凭证类别可新增（EAI）、修改、删除（受控 SQL）；会计科目仍只读。"""

from __future__ import annotations

import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import DELETE_ARCHIVES, GL_ARCHIVES, UPDATE_ARCHIVES, WRITE_ARCHIVES, ArcRecord

_CODES = {"currency": "测试币", "voucher_sign": "CO"}
_NEW = {
    "currency": {"code": "TST", "caltype": 1, "precision": 4},
    "voucher_sign": {"type_name": "CO测试类别"},
}
_CHANGE = {"currency": {"precision": 2}, "voucher_sign": {"type_name": "改名"}}


class GlArcWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_create_update_delete_each_kind(self) -> None:
        for archive in GL_ARCHIVES:
            code = _CODES[archive]
            record = ArcRecord(archive, code, _NEW[archive])
            path, sent = self._send(lambda client, r=record: client.arc_create(gl_arc._call(), r))
            self.assertEqual(path, "/u8co/v1/archives/create")
            self.assertEqual(list(sent), _AUTH + ["archive", "code", "fields"])
            self.assertEqual((sent["archive"], sent["code"], sent["fields"]), (archive, code, _NEW[archive]))
            update = ArcRecord(archive, code, _CHANGE[archive])
            path, sent = self._send(lambda client, r=update: client.arc_update(gl_arc._call(), r))
            self.assertEqual((path, sent["fields"]), ("/u8co/v1/archives/update", _CHANGE[archive]))
            path, sent = self._send(lambda client, a=archive, c=code: client.arc_delete(gl_arc._call(), a, c))
            self.assertEqual((path, sent["archive"], sent["code"]), ("/u8co/v1/archives/delete", archive, code))


class GlArcValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_kind_lists(self) -> None:
        for kinds in (WRITE_ARCHIVES, UPDATE_ARCHIVES, DELETE_ARCHIVES):
            self.assertTrue(set(GL_ARCHIVES) <= set(kinds))
            self.assertNotIn("account", kinds)

    def test_no_template(self) -> None:
        record = ArcRecord("voucher_sign", "CO", {"type_name": "x"}, template="转")
        self._refused(lambda c: c.arc_create(gl_arc._call(), record), "template")

    def test_symbol_cannot_change(self) -> None:
        record = ArcRecord("currency", "测试币", {"code": "T2"})
        self._refused(lambda c: c.arc_update(gl_arc._call(), record), "编码")


class GlArcCliTests(unittest.TestCase):
    def test_write_choices(self) -> None:
        for archive in GL_ARCHIVES:
            for command in ("arc-create", "arc-update", "arc-delete"):
                extra = ("--file", "x.json") if command != "arc-delete" else ()
                parsed = _parser().parse_args(_argv(command, "--archive", archive, "--code", _CODES[archive], *extra))
                self.assertEqual((parsed.archive, parsed.code), (archive, _CODES[archive]))


if __name__ == "__main__":
    unittest.main()
