"""客户、供应商的银行账户、客户联系人可写（customer_bank、vendor_bank、customer_contact）；供应商联系人也可写。"""

from __future__ import annotations

import unittest

from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import (
    DELETE_ARCHIVES,
    PAIR_ARCHIVES,
    READ_ARCHIVES,
    UPDATE_ARCHIVES,
    WRITE_ARCHIVES,
    ArcRecord,
)
from co.client.u8co_partner import PARTNER_ARCHIVES, PARTNER_WRITE_ARCHIVES

_CODES = {
    "customer_bank": "ZZC01:6222000000000001",
    "vendor_bank": "ZZV01:6222000000000002",
    "customer_contact": "ZZC01:ZZL01",
    "vendor_contact": "ZZV01:ZZL02",
}
_NEW = {
    "customer_bank": {"branch": "测试银行某支行", "default": True},
    "vendor_bank": {"branch": "测试银行某支行"},
    "customer_contact": {"name": "张三", "sex": "男"},
    "vendor_contact": {"name": "李四", "mobile": "13800000000"},
}
_CREATE_CODES = {**_CODES, "customer_contact": "ZZC01:", "vendor_contact": "ZZV01:"}


class PartnerWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_create_update_delete_each_kind(self) -> None:
        for archive in PARTNER_WRITE_ARCHIVES:
            code = _CODES[archive]
            record = ArcRecord(archive, _CREATE_CODES[archive], _NEW[archive])
            path, sent = self._send(lambda client, r=record: client.arc_create(gl_arc._call(), r))
            self.assertEqual(path, "/u8co/v1/archives/create")
            self.assertEqual(list(sent), _AUTH + ["archive", "code", "fields"])
            self.assertEqual((sent["archive"], sent["code"], sent["fields"]), (archive, _CREATE_CODES[archive], _NEW[archive]))
            change = {"memo": "改"} if "contact" in archive else {"account_name": "改"}
            update = ArcRecord(archive, code, change)
            path, sent = self._send(lambda client, r=update: client.arc_update(gl_arc._call(), r))
            self.assertEqual((path, sent["fields"]), ("/u8co/v1/archives/update", change))
            path, sent = self._send(lambda client, a=archive, c=code: client.arc_delete(gl_arc._call(), a, c))
            self.assertEqual((path, sent["archive"], sent["code"]), ("/u8co/v1/archives/delete", archive, code))
            path, sent = self._send(lambda client, a=archive, c=code: client.arc_get(gl_arc._call(), a, c))
            self.assertEqual((path, sent["code"]), ("/u8co/v1/archives/get", code))


class PartnerValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_kind_lists(self) -> None:
        for kinds in (READ_ARCHIVES, PAIR_ARCHIVES):
            self.assertTrue(set(PARTNER_ARCHIVES) <= set(kinds))
        for kinds in (WRITE_ARCHIVES, UPDATE_ARCHIVES, DELETE_ARCHIVES):
            self.assertTrue(set(PARTNER_WRITE_ARCHIVES) <= set(kinds))
            self.assertIn("vendor_contact", kinds)

    def test_code_needs_two_parts(self) -> None:
        self._refused(lambda c: c.arc_delete(gl_arc._call(), "customer_bank", "ZZC01"), ":")

    def test_no_template(self) -> None:
        record = ArcRecord("vendor_bank", "ZZV01:1", {"branch": "x"}, template="ZZV02:2")
        self._refused(lambda c: c.arc_create(gl_arc._call(), record), "template")


class PartnerCliTests(unittest.TestCase):
    def test_choices(self) -> None:
        for command in ("arc-create", "arc-update", "arc-delete", "arc-get"):
            extra = ("--file", "x.json") if command in ("arc-create", "arc-update") else ()
            parsed = _parser().parse_args(_argv(command, "--archive", "customer_contact", "--code", "ZZC01:L1", *extra))
            self.assertEqual((parsed.archive, parsed.code), ("customer_contact", "ZZC01:L1"))
        parsed = _parser().parse_args(_argv("arc-get", "--archive", "vendor_contact", "--code", "ZZV01:L1"))
        self.assertEqual(parsed.archive, "vendor_contact")


if __name__ == "__main__":
    unittest.main()
