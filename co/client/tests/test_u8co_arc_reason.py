"""原因码档案（reason）：新增、修改、删除的正文和命令行可选值。"""

from __future__ import annotations

import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import DELETE_ARCHIVES, READ_ARCHIVES, REASON_ARCHIVE, UPDATE_ARCHIVES, WRITE_ARCHIVES, ArcRecord

_NEW = {"name": "检测指标不合格", "Reasontype": 1}


class ReasonWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_create_update_delete(self) -> None:
        record = ArcRecord(REASON_ARCHIVE, "T9", _NEW)
        path, sent = self._send(lambda client: client.arc_create(gl_arc._call(), record))
        self.assertEqual(path, "/u8co/v1/archives/create")
        self.assertEqual(list(sent), _AUTH + ["archive", "code", "fields"])
        self.assertEqual((sent["archive"], sent["code"], sent["fields"]), ("reason", "T9", _NEW))
        change = ArcRecord(REASON_ARCHIVE, "T9", {"ReasonMemo": "说明"})
        path, sent = self._send(lambda client: client.arc_update(gl_arc._call(), change))
        self.assertEqual((path, sent["fields"]), ("/u8co/v1/archives/update", {"ReasonMemo": "说明"}))
        path, sent = self._send(lambda client: client.arc_delete(gl_arc._call(), REASON_ARCHIVE, "T9"))
        self.assertEqual((path, sent["archive"], sent["code"]), ("/u8co/v1/archives/delete", "reason", "T9"))


class ReasonCliTests(unittest.TestCase):
    def test_kind_lists_and_choices(self) -> None:
        for kinds in (READ_ARCHIVES, WRITE_ARCHIVES, UPDATE_ARCHIVES, DELETE_ARCHIVES):
            self.assertIn(REASON_ARCHIVE, kinds)
        for command in ("arc-get", "arc-create", "arc-update", "arc-delete"):
            extra = ("--file", "x.json") if command in ("arc-create", "arc-update") else ()
            parsed = _parser().parse_args(_argv(command, "--archive", "reason", "--code", "T9", *extra))
            self.assertEqual((parsed.archive, parsed.code), ("reason", "T9"))


if __name__ == "__main__":
    unittest.main()
