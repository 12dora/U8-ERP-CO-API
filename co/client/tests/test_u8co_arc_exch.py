"""档案 exchange_rate（汇率）：编码写法、currency / fiscal_year 过滤、CLI 选项；可写。"""

from __future__ import annotations

import contextlib
import io
import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import DELETE_ARCHIVES, EXCH_ARCHIVE, READ_ARCHIVES, UPDATE_ARCHIVES, WRITE_ARCHIVES, ArcQuery, ArcRecord


class ExchWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_get_by_period_and_by_date(self) -> None:
        for code in ("美元:2026:9", "美元:2026-09-14", "美元:2026:9:2026-09-14"):
            path, sent = self._send(lambda client, c=code: client.arc_get(gl_arc._call(), EXCH_ARCHIVE, c))
            self.assertEqual(path, "/u8co/v1/archives/get")
            self.assertEqual(list(sent), _AUTH + ["archive", "code"])
            self.assertEqual((sent["archive"], sent["code"]), (EXCH_ARCHIVE, code))

    def test_list_sends_currency_and_fiscal_year(self) -> None:
        query = ArcQuery(EXCH_ARCHIVE, after="美元:2026:5", limit=5, currency="美元", fiscal_year=2026)
        path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), query))
        self.assertEqual(path, "/u8co/v1/archives/list")
        self.assertEqual(list(sent), _AUTH + ["archive", "after", "limit", "currency", "fiscal_year"])
        self.assertEqual((sent["currency"], sent["fiscal_year"]), ("美元", 2026))

    def test_writes_forward(self) -> None:
        record = ArcRecord(EXCH_ARCHIVE, "美元:2026:10", {"rate": 7.1})
        path, sent = self._send(lambda client: client.arc_create(gl_arc._call(), record))
        self.assertEqual((path, sent["code"], sent["fields"]), ("/u8co/v1/archives/create", "美元:2026:10", {"rate": 7.1}))
        update = ArcRecord(EXCH_ARCHIVE, "美元:2026:10", {"adjust_rate": 7.2})
        path, sent = self._send(lambda client: client.arc_update(gl_arc._call(), update))
        self.assertEqual((path, sent["fields"]), ("/u8co/v1/archives/update", {"adjust_rate": 7.2}))
        path, sent = self._send(lambda client: client.arc_delete(gl_arc._call(), EXCH_ARCHIVE, "美元:2026:10"))
        self.assertEqual((path, sent["code"]), ("/u8co/v1/archives/delete", "美元:2026:10"))

    def test_list_without_filters(self) -> None:
        _path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), ArcQuery(EXCH_ARCHIVE, changed_since="9")))
        self.assertEqual(list(sent), _AUTH + ["archive", "changed_since"])


class ExchValidationTests(unittest.TestCase):
    _refused = gl_arc.ValidationTests._refused

    def test_write_lists_and_template(self) -> None:
        for kinds in (WRITE_ARCHIVES, UPDATE_ARCHIVES, DELETE_ARCHIVES):
            self.assertIn(EXCH_ARCHIVE, kinds)
        record = ArcRecord(EXCH_ARCHIVE, "美元:2026:9", {"rate": 7}, template="美元:2026:8")
        self._refused(lambda c: c.arc_create(gl_arc._call(), record), "template")

    def test_code_needs_separator_and_length(self) -> None:
        self._refused(lambda c: c.arc_get(gl_arc._call(), EXCH_ARCHIVE, "美元"), "<币种>")
        self._refused(lambda c: c.arc_get(gl_arc._call(), EXCH_ARCHIVE, "美" * 28), "code")
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery(EXCH_ARCHIVE, after="美元")), "after")

    def test_filters_only_for_exchange_rate(self) -> None:
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery("currency", currency="美元")), "exchange_rate")
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery("account", fiscal_year=2026)), "exchange_rate")
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery(EXCH_ARCHIVE, fiscal_year=26)), "fiscal_year")
        self._refused(lambda c: c.arc_list(gl_arc._call(), ArcQuery(EXCH_ARCHIVE, currency="X" * 9)), "currency")


class ExchCliTests(unittest.TestCase):
    def test_read_choice_and_list_options(self) -> None:
        self.assertIn(EXCH_ARCHIVE, READ_ARCHIVES)
        parsed = _parser().parse_args(_argv("arc-get", "--archive", EXCH_ARCHIVE, "--code", "美元:2026-09-14"))
        self.assertEqual((parsed.archive, parsed.code), (EXCH_ARCHIVE, "美元:2026-09-14"))
        argv = _argv("arc-list", "--archive", EXCH_ARCHIVE, "--currency", "美元", "--fiscal-year", "2026")
        listing = _parser().parse_args(argv)
        self.assertEqual((listing.currency, listing.fiscal_year), ("美元", 2026))
        # 汇率可写：删除命令能解析。
        deleting = _parser().parse_args(_argv("arc-delete", "--archive", EXCH_ARCHIVE, "--code", "美元:2026:9"))
        self.assertEqual((deleting.archive, deleting.code), (EXCH_ARCHIVE, "美元:2026:9"))


if __name__ == "__main__":
    unittest.main()
