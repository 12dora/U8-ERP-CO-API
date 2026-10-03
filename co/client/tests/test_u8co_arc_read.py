"""只读档案（科目、计量单位、结算方式、凭证类别、币种、开户银行、项目）的 get / list 正文和命令行。"""

from __future__ import annotations

import contextlib
import io
import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import READ_ARCHIVES, ArcQuery, ArcRecord

_READ_ONLY = ("account", "unit", "unit_group", "settle_style", "voucher_sign", "currency", "bank", "project")


class ReadOnlyWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_get_read_only_kinds(self) -> None:
        for archive in _READ_ONLY:
            code = "98:01" if archive == "project" else "1001"
            path, sent = self._send(lambda client, a=archive, c=code: client.arc_get(gl_arc._call(), a, c))
            self.assertEqual(path, "/u8co/v1/archives/get")
            self.assertEqual(list(sent), _AUTH + ["archive", "code"])
            self.assertEqual((sent["archive"], sent["code"]), (archive, code))

    def test_project_list_sends_project_class(self) -> None:
        query = ArcQuery("project", code_prefix="98:", after="98:01", limit=50, project_class="98")
        path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), query))
        self.assertEqual(path, "/u8co/v1/archives/list")
        self.assertEqual(list(sent), _AUTH + ["archive", "code_prefix", "after", "limit", "project_class"])
        self.assertEqual(sent["project_class"], "98")

    def test_account_list_without_class(self) -> None:
        _path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), ArcQuery("account", changed_since="7")))
        self.assertEqual(list(sent), _AUTH + ["archive", "changed_since"])


class ReadOnlyValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_writes_on_read_only_kinds_are_refused(self) -> None:
        # 计量单位组、结算方式之后可写（test_u8co_arc_class_write），币种、凭证类别可写（test_u8co_arc_gl_write）；
        # 科目、行业仍只读。
        self._refused(lambda c: c.arc_create(gl_arc._call(), ArcRecord("account", "1001", {"name": "x"})), "只读")
        self._refused(lambda c: c.arc_delete(gl_arc._call(), "trade_class", "01"), "只读")
        self._refused(lambda c: c.arc_create(gl_arc._call(), ArcRecord("trade_class", "01", {"name": "x"})), "只读")
        self._refused(lambda c: c.arc_update(gl_arc._call(), ArcRecord("trade_class", "01", {"name": "x"})), "只读")

    def test_project_class_rules(self) -> None:
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery("customer", project_class="98")), "只有 project")
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery("project", project_class="987")), "project_class")
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery("project", project_class="9-")), "project_class")
        self._refused(lambda c: c.arc_get(gl_arc._call(), "account", "1" * 61), "code")
        self._refused(lambda c: c.arc_get(gl_arc._call(), "project", "9" * 64), "code")


class ReadOnlyCliTests(unittest.TestCase):
    def test_read_only_kinds_are_choices_for_get_and_list(self) -> None:
        start = READ_ARCHIVES.index(_READ_ONLY[0])
        self.assertEqual(READ_ARCHIVES[start : start + len(_READ_ONLY)], _READ_ONLY)
        parsed = _parser().parse_args(_argv("arc-get", "--archive", "project", "--code", "98:01"))
        self.assertEqual((parsed.archive, parsed.code), ("project", "98:01"))
        listing = _parser().parse_args(_argv("arc-list", "--archive", "project", "--project-class", "ZF"))
        self.assertEqual((listing.archive, listing.project_class), ("project", "ZF"))
        plain = _parser().parse_args(_argv("arc-list", "--archive", "currency"))
        self.assertEqual(plain.project_class, "")

    def test_read_only_kinds_are_not_choices_for_writes(self) -> None:
        for command in ("arc-delete", "arc-create", "arc-update"):
            extra = ("--file", "x.json") if command != "arc-delete" else ()
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    _parser().parse_args(_argv(command, "--archive", "trade_class", "--code", "01", *extra))


if __name__ == "__main__":
    unittest.main()
