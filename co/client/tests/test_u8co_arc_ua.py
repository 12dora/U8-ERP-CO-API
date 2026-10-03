"""只读档案 operator（U8 操作员）、role（角色）：编码长度、只读、CLI 选项。"""

from __future__ import annotations

import contextlib
import io
import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import READ_ARCHIVES, RO6_ARCHIVES, UA_ARCHIVES, ArcQuery, ArcRecord


class UaWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_get_and_list(self) -> None:
        for kind in UA_ARCHIVES:
            path, sent = self._send(lambda client, kind=kind: client.arc_get(gl_arc._call(), kind, "A01"))
            self.assertEqual((path, list(sent)), ("/u8co/v1/archives/get", _AUTH + ["archive", "code"]))
            self.assertEqual((sent["archive"], sent["code"]), (kind, "A01"))
            query = ArcQuery(kind, code_prefix="A", after="A01", limit=5)
            path, sent = self._send(lambda client, query=query: client.arc_list(gl_arc._call(), query))
            self.assertEqual(path, "/u8co/v1/archives/list")
            self.assertEqual(list(sent), _AUTH + ["archive", "code_prefix", "after", "limit"])


class UaValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_writes_are_refused(self) -> None:
        for kind in UA_ARCHIVES:
            self._refused(lambda c, kind=kind: c.arc_delete(gl_arc._call(), kind, "A01"), "只读")
            self._refused(lambda c, kind=kind: c.arc_create(gl_arc._call(), ArcRecord(kind, "A01", {"name": "x"})), "只读")

    def test_code_length(self) -> None:
        for kind in UA_ARCHIVES:
            self._refused(lambda c, kind=kind: c.arc_get(gl_arc._call(), kind, "U" * 21), "code")
            self._refused(lambda c, kind=kind: c.arc_list(gl_arc._call(), ArcQuery(kind, after="U" * 21)), "after")


class UaCliTests(unittest.TestCase):
    def test_read_choices(self) -> None:
        self.assertTrue(set(UA_ARCHIVES) <= set(READ_ARCHIVES))
        self.assertTrue(set(RO6_ARCHIVES) <= set(READ_ARCHIVES))
        parsed = _parser().parse_args(_argv("arc-get", "--archive", "operator", "--code", "A01"))
        self.assertEqual((parsed.archive, parsed.code), ("operator", "A01"))
        listing = _parser().parse_args(_argv("arc-list", "--archive", "role", "--name-like", "会计"))
        self.assertEqual((listing.archive, listing.name_like), ("role", "会计"))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("arc-delete", "--archive", "operator", "--code", "A01"))


if __name__ == "__main__":
    unittest.main()
