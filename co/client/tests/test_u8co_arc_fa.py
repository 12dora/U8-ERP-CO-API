"""档案 fa_card（固定资产卡片）：编码长度、type_code / dept_code / include_disposed 过滤、CLI 选项。
可新增、撤销本期新增（见 test_u8co_fa_write），不能修改。"""

from __future__ import annotations

import contextlib
import io
import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import FA_ARCHIVE, READ_ARCHIVES, RO6_ARCHIVES, ArcQuery, ArcRecord


class FaWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_get_by_card_number(self) -> None:
        path, sent = self._send(lambda client: client.arc_get(gl_arc._call(), FA_ARCHIVE, "FA-9001"))
        self.assertEqual(path, "/u8co/v1/archives/get")
        self.assertEqual(list(sent), _AUTH + ["archive", "code"])
        self.assertEqual((sent["archive"], sent["code"]), (FA_ARCHIVE, "FA-9001"))

    def test_list_sends_fa_filters(self) -> None:
        query = ArcQuery(FA_ARCHIVE, after="FA-9000", limit=5, type_code="20", dept_code="D901", include_disposed=True)
        path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), query))
        self.assertEqual(path, "/u8co/v1/archives/list")
        self.assertEqual(list(sent), _AUTH + ["archive", "after", "limit", "type_code", "dept_code", "include_disposed"])
        self.assertEqual((sent["type_code"], sent["dept_code"], sent["include_disposed"]), ("20", "D901", True))

    def test_list_keeps_explicit_false_and_omits_unset(self) -> None:
        _path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), ArcQuery(FA_ARCHIVE, include_disposed=False)))
        self.assertEqual(list(sent), _AUTH + ["archive", "include_disposed"])
        self.assertIs(sent["include_disposed"], False)
        _path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), ArcQuery(FA_ARCHIVE)))
        self.assertEqual(list(sent), _AUTH + ["archive"])


class FaValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_update_is_refused(self) -> None:
        self._refused(lambda c: c.arc_update(gl_arc._call(), ArcRecord(FA_ARCHIVE, "FA-9001", {"name": "x"})), "变动单")

    def test_code_and_filter_lengths(self) -> None:
        self._refused(lambda c: c.arc_get(gl_arc._call(), FA_ARCHIVE, "C" * 21), "code")
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery(FA_ARCHIVE, type_code="T" * 21)), "type_code")
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery(FA_ARCHIVE, dept_code="D" * 13)), "dept_code")

    def test_filters_only_for_fa_card(self) -> None:
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery("account", type_code="20")), "fa_card")
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery("department", dept_code="D901")), "fa_card")
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery("unit", include_disposed=False)), "fa_card")


class FaCliTests(unittest.TestCase):
    def test_read_choice_and_list_options(self) -> None:
        self.assertIn(FA_ARCHIVE, READ_ARCHIVES)
        self.assertTrue(set(RO6_ARCHIVES) <= set(READ_ARCHIVES))
        parsed = _parser().parse_args(_argv("arc-get", "--archive", FA_ARCHIVE, "--code", "FA-9001"))
        self.assertEqual((parsed.archive, parsed.code), (FA_ARCHIVE, "FA-9001"))
        argv = _argv("arc-list", "--archive", FA_ARCHIVE, "--type-code", "20", "--dept-code", "D901", "--include-disposed")
        listing = _parser().parse_args(argv)
        self.assertEqual((listing.type_code, listing.dept_code, listing.include_disposed), ("20", "D901", True))
        plain = _parser().parse_args(_argv("arc-list", "--archive", FA_ARCHIVE))
        self.assertFalse(plain.include_disposed)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("arc-update", "--archive", FA_ARCHIVE, "--code", "FA-9001", "--file", "x.json"))


if __name__ == "__main__":
    unittest.main()
