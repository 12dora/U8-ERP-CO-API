"""明细账报表（arap_detail、gl_detail）：正文、本地校验和命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _argv, _assert_signed, _run_main, _Running
from co.client.tests.test_u8co_reports import _call, _offline
from co.client.u8co_cli import _parser
from co.client.u8co_cli_reports_detail import arap_detail_query, gl_detail_query
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_reports_detail import ArapDetailQuery, GlDetailQuery


class WireTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_arap_detail(self) -> None:
        query = ArapDetailQuery("ar", "C001", "2026-08-01")
        path, sent = self._send(lambda client: client.report_arap_detail(_call(), query))
        self.assertEqual(path, "/u8co/v1/reports/arap_detail")
        self.assertEqual(list(sent), _AUTH + ["side", "partner", "date_from"])
        self.assertEqual(sent["partner"], "C001")
        full = ArapDetailQuery(
            "ap", ("V001", "V002"), "2026-01-01", "2026-08-31", "register", ("2202",), (), "D01", "P001",
            False, "opaque", 1000,
        )
        _path, sent = self._send(lambda client: client.report_arap_detail(_call(), full))
        self.assertEqual(
            list(sent),
            _AUTH + [
                "side", "partner", "date_from", "date_to", "basis", "accounts", "exclude_accounts", "dept", "person",
                "include_writeoff", "after", "limit",
            ],
        )
        self.assertEqual(sent["partner"], ["V001", "V002"])
        self.assertIs(sent["include_writeoff"], False)

    def test_gl_detail(self) -> None:
        query = GlDetailQuery("1122", 8, 8)
        path, sent = self._send(lambda client: client.report_gl_detail(_call(), query))
        self.assertEqual(path, "/u8co/v1/reports/gl_detail")
        self.assertEqual(list(sent), _AUTH + ["code", "period_from", "period_to"])
        dated = GlDetailQuery(
            "1002", date_from="2026-09-05", date_to="2026-09-20", fiscal_year=2026, include_sub=False,
            include_unposted=True, customer="C001", project="01", project_class="00", after="x", limit=10,
        )
        _path, sent = self._send(lambda client: client.report_gl_detail(_call(), dated))
        self.assertEqual(
            list(sent),
            _AUTH + [
                "code", "fiscal_year", "date_from", "date_to", "include_sub", "include_unposted", "customer",
                "project", "project_class", "after", "limit",
            ],
        )
        self.assertIs(sent["include_sub"], False)


class ValidationTests(unittest.TestCase):
    def _refused(self, invoke: Callable[[U8CoClient], object], text: str) -> None:
        with self.assertRaises(ValueError) as caught:
            invoke(_offline())
        self.assertIn(text, str(caught.exception))

    def test_arap_rules(self) -> None:
        cases = (
            (ArapDetailQuery("both", "C1", "2026-08-01"), "side"),
            (ArapDetailQuery("ar", "", "2026-08-01"), "partner"),
            (ArapDetailQuery("ar", (), "2026-08-01"), "partner"),
            (ArapDetailQuery("ar", ("C",) * 21, "2026-08-01"), "partner"),
            (ArapDetailQuery("ar", "C1", "2026-8-01"), "date_from"),
            (ArapDetailQuery("ar", "C1", "2026-08-02", "2026-08-01"), "date_from"),
            (ArapDetailQuery("ar", "C1", "2026-08-01", basis="due"), "basis"),
            (ArapDetailQuery("ar", "C1", "2026-08-01", accounts=()), "accounts"),
            (ArapDetailQuery("ar", "C1", "2026-08-01", dept="D" * 21), "dept"),
            (ArapDetailQuery("ar", "C1", "2026-08-01", limit=1001), "limit"),
        )
        for query, text in cases:
            self._refused(lambda c, q=query: c.report_arap_detail(_call(), q), text)

    def test_gl_rules(self) -> None:
        cases = (
            (GlDetailQuery("11%", 8, 8), "code"),
            (GlDetailQuery("1122"), "二选一"),
            (GlDetailQuery("1122", 8, 8, date_from="2026-08-01", date_to="2026-08-31"), "二选一"),
            (GlDetailQuery("1122", 8), "period_to"),
            (GlDetailQuery("1122", 9, 8), "period_from"),
            (GlDetailQuery("1122", date_from="2026-08-01"), "同时"),
            (GlDetailQuery("1122", date_from="2025-12-31", date_to="2026-01-01"), "同一年度"),
            (GlDetailQuery("1122", 8, 8, project_class="00"), "project"),
            (GlDetailQuery("1122", 8, 8, fiscal_year=1999), "fiscal_year"),
        )
        for query, text in cases:
            self._refused(lambda c, q=query: c.report_gl_detail(_call(), q), text)


class CliTests(unittest.TestCase):
    def test_parse_arap_detail(self) -> None:
        argv = _argv(
            "report-arap-detail", "--side", "ap", "--partner", "V001", "--partner", "V002", "--date-from", "2026-01-01",
            "--basis", "register", "--account", "2202", "--include-writeoff",
        )
        query = arap_detail_query(_parser().parse_args(argv))
        self.assertEqual(query.partner, ("V001", "V002"))
        self.assertEqual((query.basis, query.accounts, query.include_writeoff), ("register", ("2202",), True))
        argv = _argv("report-arap-detail", "--side", "ar", "--partner", "C1", "--date-from", "2026-08-01")
        single = arap_detail_query(_parser().parse_args(argv))
        self.assertEqual(single, ArapDetailQuery("ar", "C1", "2026-08-01"))

    def test_parse_gl_detail(self) -> None:
        argv = _argv(
            "report-gl-detail", "--code", "1122", "--date-from", "2026-08-01", "--date-to", "2026-08-31", "--exact",
            "--include-unposted", "--customer", "C1", "--project-class", "00", "--project", "01",
        )
        query = gl_detail_query(_parser().parse_args(argv))
        self.assertEqual((query.include_sub, query.include_unposted), (False, True))
        self.assertEqual((query.customer, query.project, query.project_class), ("C1", "01", "00"))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-gl-detail"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-arap-detail", "--side", "ar", "--date-from", "2026-08-01"))

    def test_main_wires_detail_reports(self) -> None:
        seen: dict[str, Any] = {}

        def _fake(name: str) -> Callable[..., dict[str, Any]]:
            def invoke(self: U8CoClient, call: U8Call, query: object = None) -> dict[str, Any]:
                seen[name] = query
                return {"ok": True}

            return invoke

        cases = (
            (
                _argv("report-arap-detail", "--side", "ar", "--partner", "C1", "--date-from", "2026-08-01"),
                "report_arap_detail",
                ArapDetailQuery("ar", "C1", "2026-08-01"),
            ),
            (
                _argv("report-gl-detail", "--code", "1122", "--period-from", "8", "--period-to", "8"),
                "report_gl_detail",
                GlDetailQuery("1122", 8, 8),
            ),
        )
        for argv, method, want in cases:
            self.assertEqual(_run_main(argv, method, _fake(method)), 0)
            self.assertEqual(seen[method], want)


if __name__ == "__main__":
    unittest.main()
