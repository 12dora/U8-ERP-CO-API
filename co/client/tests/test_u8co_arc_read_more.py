"""十类只读档案（货位、收发类别、采购类型、销售类型、地区分类、行业、银行档案、客户收货地址、自定义项、客户存货对照）。"""

from __future__ import annotations

import contextlib
import io
import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import PAIR_ARCHIVES, READ_ARCHIVES, RO6_ARCHIVES, ArcQuery, ArcRecord

_PAIR_CODE = {
    "customer_address": "C001:01",
    "user_define": "1002:快递",
    "customer_inventory": "C001:INV0010",
}


def _code(archive: str) -> str:
    return _PAIR_CODE.get(archive, "01")


class ReadArchiveWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_get_each_kind(self) -> None:
        for archive in RO6_ARCHIVES:
            code = _code(archive)
            path, sent = self._send(lambda client, a=archive, c=code: client.arc_get(gl_arc._call(), a, c))
            self.assertEqual(path, "/u8co/v1/archives/get")
            self.assertEqual(list(sent), _AUTH + ["archive", "code"])
            self.assertEqual((sent["archive"], sent["code"]), (archive, code))

    def test_pair_list_sends_whole_prefix_and_after(self) -> None:
        query = ArcQuery("user_define", code_prefix="1002:", after="1002:" + "x" * 400, limit=5)
        path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), query))
        self.assertEqual(path, "/u8co/v1/archives/list")
        self.assertEqual(list(sent), _AUTH + ["archive", "code_prefix", "after", "limit"])
        self.assertEqual(len(sent["after"]), 405)

    def test_position_list_with_since(self) -> None:
        _path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), ArcQuery("position", changed_since="9")))
        self.assertEqual(list(sent), _AUTH + ["archive", "changed_since"])


class ReadArchiveValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_writes_are_refused(self) -> None:
        # 收发类别、采购类型、销售类型、地区分类、银行档案之后可写（test_u8co_arc_class_write），行业仍只读。
        self._refused(lambda c: c.arc_delete(gl_arc._call(), "trade_class", "101"), "只读")
        self._refused(lambda c: c.arc_create(gl_arc._call(), ArcRecord("trade_class", "01", {"name": "x"})), "只读")
        self._refused(lambda c: c.arc_update(gl_arc._call(), ArcRecord("customer_address", "C1:01", {"x": 1})), "只读")

    def test_pair_codes_need_separator_and_length(self) -> None:
        self._refused(lambda c: c.arc_get(gl_arc._call(), "customer_address", "C001"), "<第一段>:<第二段>")
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery("customer_inventory", after="C001")), "after")
        self._refused(lambda c: c.arc_get(gl_arc._call(), "customer_inventory", "C:" + "9" * 80), "code")
        self._refused(lambda c: c.arc_get(gl_arc._call(), "user_define", "1:" + "x" * 410), "code")
        self._refused(lambda c: c.arc_get(gl_arc._call(), "position", "9" * 61), "code")


class ReadArchiveCliTests(unittest.TestCase):
    def test_kinds_are_read_choices_only(self) -> None:
        self.assertTrue(set(RO6_ARCHIVES) <= set(READ_ARCHIVES))
        self.assertTrue(set(PAIR_ARCHIVES) <= set(READ_ARCHIVES))
        parsed = _parser().parse_args(_argv("arc-get", "--archive", "customer_address", "--code", "C001:01"))
        self.assertEqual((parsed.archive, parsed.code), ("customer_address", "C001:01"))
        listing = _parser().parse_args(_argv("arc-list", "--archive", "trade_class"))
        self.assertEqual(listing.archive, "trade_class")
        for command in ("arc-delete", "arc-create", "arc-update"):
            extra = ("--file", "x.json") if command != "arc-delete" else ()
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    _parser().parse_args(_argv(command, "--archive", "trade_class", "--code", "01", *extra))


if __name__ == "__main__":
    unittest.main()
