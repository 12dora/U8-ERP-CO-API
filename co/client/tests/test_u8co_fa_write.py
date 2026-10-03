"""固定资产写入（客户端）：卡片新增 / 撤销本期新增、设备台账新增（archives）。变动单在 U8 客户端录入，不在 API 里。"""

from __future__ import annotations

import contextlib
import io
import unittest

from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.tests.test_u8co_update_close_gen import _argv
from co.client.u8co_cli import _parser
from co.client.u8co_client import VoucherDraft
from co.client.u8co_fa_write import EQ_ARCHIVE, NO_UPDATE_TEXT
from co.client.u8co_kinds import CREATABLE_KINDS, KIND_NAMES
from co.client.u8co_gl_arc import DELETE_ARCHIVES, FA_ARCHIVE, READ_ARCHIVES, UPDATE_ARCHIVES, WRITE_ARCHIVES, ArcQuery, ArcRecord


class FaKindTests(unittest.TestCase):
    def test_fa_change_is_not_a_kind(self) -> None:
        self.assertNotIn("fa_change", KIND_NAMES)
        self.assertNotIn("fa_change", CREATABLE_KINDS)

    def test_archive_groups(self) -> None:
        self.assertIn(EQ_ARCHIVE, READ_ARCHIVES)
        for archive in (FA_ARCHIVE, EQ_ARCHIVE):
            self.assertIn(archive, WRITE_ARCHIVES)
            self.assertNotIn(archive, UPDATE_ARCHIVES)
        self.assertIn(FA_ARCHIVE, DELETE_ARCHIVES)
        self.assertNotIn(EQ_ARCHIVE, DELETE_ARCHIVES)
        for group in (READ_ARCHIVES, WRITE_ARCHIVES, DELETE_ARCHIVES):
            self.assertEqual(len(group), len(set(group)))


class FaWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_card_create_delete_and_equipment(self) -> None:
        record = ArcRecord(FA_ARCHIVE, "ZC-001", {"name": "空压机", "original_value": 12000})
        path, sent = self._send(lambda client: client.arc_create(gl_arc._call(), record))
        self.assertEqual((path, sent["archive"], sent["code"]), ("/u8co/v1/archives/create", FA_ARCHIVE, "ZC-001"))
        path, sent = self._send(lambda client: client.arc_delete(gl_arc._call(), FA_ARCHIVE, "FA-9002"))
        self.assertEqual((path, sent["code"]), ("/u8co/v1/archives/delete", "FA-9002"))
        equipment = ArcRecord(EQ_ARCHIVE, "EQ-0001", {"name": "数控车床", "cdepcode": "D901"})
        _path, sent = self._send(lambda client: client.arc_create(gl_arc._call(), equipment))
        self.assertEqual(sent["fields"], {"name": "数控车床", "cdepcode": "D901"})
        _path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), ArcQuery(EQ_ARCHIVE, changed_since="24877043")))
        self.assertEqual(sent["changed_since"], "24877043")


class FaValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_fa_change_is_refused(self) -> None:
        draft = VoucherDraft("fa_change", {"card_code": "FA-9002"}, [{"x": 1}])
        self._refused(lambda c: c.create_voucher(gl_arc._call(), draft), "单据类型")

    def test_archive_refusals(self) -> None:
        for archive in (FA_ARCHIVE, EQ_ARCHIVE):
            record = ArcRecord(archive, "X1", {"name": "x"})
            self._refused(lambda c, record=record: c.arc_update(gl_arc._call(), record), NO_UPDATE_TEXT[archive])
            with_template = ArcRecord(archive, "X1", {"name": "x"}, template="X0")
            self._refused(lambda c, record=with_template: c.arc_create(gl_arc._call(), record), "template")
        self._refused(lambda c: c.arc_delete(gl_arc._call(), EQ_ARCHIVE, "EQ-0001"), "删除")
        self._refused(lambda c: c.arc_get(gl_arc._call(), EQ_ARCHIVE, "E" * 31), "code")


class FaCliTests(unittest.TestCase):
    def test_archive_choices(self) -> None:
        parsed = _parser().parse_args(_argv("arc-delete", "--archive", FA_ARCHIVE, "--code", "FA-9002"))
        self.assertEqual(parsed.archive, FA_ARCHIVE)
        listing = _parser().parse_args(_argv("arc-list", "--archive", EQ_ARCHIVE))
        self.assertEqual(listing.archive, EQ_ARCHIVE)
        refused = (("arc-update", "--file", "x.json"), ("arc-delete",))
        with contextlib.redirect_stderr(io.StringIO()):
            for name, *extra in refused:
                with self.assertRaises(SystemExit):
                    _parser().parse_args(_argv(name, "--archive", EQ_ARCHIVE, "--code", "EQ-0001", *extra))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("create", "--type", "fa_change", "--head-json", "{}", "--lines-json", "[]"))


if __name__ == "__main__":
    unittest.main()
