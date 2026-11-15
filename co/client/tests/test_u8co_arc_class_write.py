"""计量单位组、结算方式、收发类别、采购类型、销售类型、地区分类、银行档案可新增、修改、删除（EAI）；
币种、凭证类别可写，见 test_u8co_arc_gl_write；科目仍只读。"""

from __future__ import annotations

import contextlib
import io
import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import DELETE_ARCHIVES, EAI_CLASS_ARCHIVES, UPDATE_ARCHIVES, WRITE_ARCHIVES, ArcRecord

_CODES = {
    "unit_group": "G01",
    "settle_style": "ZZ9",
    "rd_style": "ZZ901",
    "purchase_type": "ZZ",
    "sale_type": "ZZ",
    "district_class": "ZZ",
    "aa_bank": "ZZ001",
}
_NEW = {
    "unit_group": {"name": "测试组", "type": 1},
    "settle_style": {"name": "测试结算"},
    "rd_style": {"name": "测试收发", "rsflag": 1},
    "purchase_type": {"name": "测试采购"},
    "sale_type": {"name": "测试销售", "rstype_code": "101"},
    "district_class": {"name": "测试地区"},
    "aa_bank": {"name": "测试银行"},
}


class Rw8WireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_create_update_delete_each_kind(self) -> None:
        for archive in EAI_CLASS_ARCHIVES:
            code = _CODES[archive]
            record = ArcRecord(archive, code, _NEW[archive])
            path, sent = self._send(lambda client, r=record: client.arc_create(gl_arc._call(), r))
            self.assertEqual(path, "/u8co/v1/archives/create")
            self.assertEqual(list(sent), _AUTH + ["archive", "code", "fields"])
            self.assertEqual((sent["archive"], sent["code"], sent["fields"]), (archive, code, _NEW[archive]))
            change = {"name": "改名"}
            update = ArcRecord(archive, code, change)
            path, sent = self._send(lambda client, r=update: client.arc_update(gl_arc._call(), r))
            self.assertEqual((path, sent["fields"]), ("/u8co/v1/archives/update", change))
            path, sent = self._send(lambda client, a=archive, c=code: client.arc_delete(gl_arc._call(), a, c))
            self.assertEqual((path, sent["archive"], sent["code"]), ("/u8co/v1/archives/delete", archive, code))


class Rw8ValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_kind_lists(self) -> None:
        for kinds in (WRITE_ARCHIVES, UPDATE_ARCHIVES, DELETE_ARCHIVES):
            self.assertTrue(set(EAI_CLASS_ARCHIVES) <= set(kinds))
            self.assertFalse({"trade_class", "customer_address"} & set(kinds))

    def test_trade_class_read_only(self) -> None:
        record = ArcRecord("trade_class", "ZZ", {"name": "x"})
        self.assertNotIn("trade_class", EAI_CLASS_ARCHIVES)
        self._refused(lambda c: c.arc_create(gl_arc._call(), record), "只读")
        self._refused(lambda c: c.arc_update(gl_arc._call(), record), "只读")
        self._refused(lambda c: c.arc_delete(gl_arc._call(), "trade_class", "ZZ"), "只读")

    def test_code_cannot_change(self) -> None:
        record = ArcRecord("sale_type", "ZZ", {"code": "ZY"})
        self._refused(lambda c: c.arc_update(gl_arc._call(), record), "编码")


class Rw8CliTests(unittest.TestCase):
    def test_write_choices(self) -> None:
        for command in ("arc-create", "arc-update", "arc-delete"):
            extra = ("--file", "x.json") if command != "arc-delete" else ()
            parsed = _parser().parse_args(_argv(command, "--archive", "rd_style", "--code", "ZZ901", *extra))
            self.assertEqual((parsed.archive, parsed.code), ("rd_style", "ZZ901"))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("arc-delete", "--archive", "trade_class", "--code", "01"))


if __name__ == "__main__":
    unittest.main()
