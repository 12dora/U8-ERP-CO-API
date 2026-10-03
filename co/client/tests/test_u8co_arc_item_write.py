"""货位、计量单位、自定义项档案、客户存货对照可新增、删除（EAI），前两类还能修改；两列主键的不收 template。"""

from __future__ import annotations

import contextlib
import io
import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import DELETE_ARCHIVES, EAI_ITEM_ARCHIVES, UPDATE_ARCHIVES, WRITE_ARCHIVES, ArcRecord

_CODES = {
    "position": "ZZ01",
    "unit": "ZZ260928",
    "user_define": "1002:快递",
    "customer_inventory": "C001:INV0010",
}
_NEW = {
    "position": {"name": "测试货位", "warehouse_code": "01"},
    "unit": {"name": "测试单位", "group_code": "02", "changerate": 25},
    "user_define": {"alias": "KD"},
    "customer_inventory": {"ccusinvname": "客户叫法"},
}


class ItemArchiveWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_create_update_delete_each_kind(self) -> None:
        for archive in EAI_ITEM_ARCHIVES:
            code = _CODES[archive]
            record = ArcRecord(archive, code, _NEW[archive])
            path, sent = self._send(lambda client, r=record: client.arc_create(gl_arc._call(), r))
            self.assertEqual(path, "/u8co/v1/archives/create")
            self.assertEqual(list(sent), _AUTH + ["archive", "code", "fields"])
            self.assertEqual((sent["archive"], sent["code"], sent["fields"]), (archive, code, _NEW[archive]))
            if archive in UPDATE_ARCHIVES:
                update = ArcRecord(archive, code, {"barcode": "B1"})
                path, sent = self._send(lambda client, r=update: client.arc_update(gl_arc._call(), r))
                self.assertEqual((path, sent["fields"]), ("/u8co/v1/archives/update", {"barcode": "B1"}))
            path, sent = self._send(lambda client, a=archive, c=code: client.arc_delete(gl_arc._call(), a, c))
            self.assertEqual((path, sent["archive"], sent["code"]), ("/u8co/v1/archives/delete", archive, code))

    def test_unit_template_is_sent(self) -> None:
        record = ArcRecord("unit", "ZZ02", _NEW["unit"], "0202")
        _path, sent = self._send(lambda client: client.arc_create(gl_arc._call(), record))
        self.assertEqual(sent["template"], "0202")


class ItemArchiveValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_pair_kinds_refuse_template_and_bad_codes(self) -> None:
        record = ArcRecord("user_define", "1002:快递", {"alias": "x"}, "1002:顺丰")
        self._refused(lambda c: c.arc_create(gl_arc._call(), record), "template")
        self._refused(lambda c: c.arc_delete(gl_arc._call(), "customer_inventory", "C001"), "<第一段>:<第二段>")

    def test_no_update_for_user_define_and_customer_inventory(self) -> None:
        for archive in ("user_define", "customer_inventory"):
            record = ArcRecord(archive, _CODES[archive], _NEW[archive])
            self._refused(lambda c, r=record: c.arc_update(gl_arc._call(), r), "不支持修改")

    def test_kind_lists(self) -> None:
        self.assertTrue(set(EAI_ITEM_ARCHIVES) <= set(WRITE_ARCHIVES))
        self.assertTrue(set(EAI_ITEM_ARCHIVES) <= set(DELETE_ARCHIVES))
        self.assertTrue({"position", "unit"} <= set(UPDATE_ARCHIVES) < set(WRITE_ARCHIVES))
        self.assertFalse({"user_define", "customer_inventory"} & set(UPDATE_ARCHIVES))
        self.assertNotIn("trade_class", WRITE_ARCHIVES)


class ItemArchiveCliTests(unittest.TestCase):
    def test_write_choices(self) -> None:
        made = _parser().parse_args(_argv("arc-create", "--archive", "position", "--code", "ZZ01", "--file", "x.json"))
        self.assertEqual((made.archive, made.code), ("position", "ZZ01"))
        gone = _parser().parse_args(_argv("arc-delete", "--archive", "user_define", "--code", "1002:快递"))
        self.assertEqual(gone.archive, "user_define")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("arc-delete", "--archive", "trade_class", "--code", "01"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("arc-update", "--archive", "user_define", "--code", "1002:快递", "--file", "x.json"))


if __name__ == "__main__":
    unittest.main()
