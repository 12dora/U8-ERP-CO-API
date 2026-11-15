"""只读报表的正文、本地校验和命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import (
    PASSWORD,
    SECRET,
    _AUTH,
    _argv,
    _assert_signed,
    _run_main,
    _Running,
)
from co.client.u8co_cli import _parser
from co.client.u8co_cli_reports import arap_query, parse_buckets
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_reports import ArapQuery, BomQuery, GlAuxQuery, GlBalanceQuery


def _call() -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-27")


def _offline() -> U8CoClient:
    return U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)


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

    def test_close_status(self) -> None:
        path, sent = self._send(lambda client: client.report_close_status(_call()))
        self.assertEqual(path, "/u8co/v1/reports/close_status")
        self.assertEqual(list(sent), _AUTH)
        _path, sent = self._send(lambda client: client.report_close_status(_call(), 2026))
        self.assertEqual(list(sent), _AUTH + ["fiscal_year"])
        self.assertEqual(sent["fiscal_year"], 2026)

    def test_gl_balance(self) -> None:
        path, sent = self._send(lambda client: client.report_gl_balance(_call(), GlBalanceQuery(1, 8)))
        self.assertEqual(path, "/u8co/v1/reports/gl_balance")
        self.assertEqual(list(sent), _AUTH + ["period_from", "period_to"])
        query = GlBalanceQuery(
            1, 8, fiscal_year=2026, grade_from=1, grade_to=2, code_prefix="1002", leaf_only=False,
            include_unposted=True, nonzero=True, after="1001", limit=1000,
        )
        _path, sent = self._send(lambda client: client.report_gl_balance(_call(), query))
        self.assertEqual(
            list(sent),
            _AUTH + [
                "fiscal_year", "period_from", "period_to", "grade_from", "grade_to", "code_prefix",
                "leaf_only", "include_unposted", "nonzero", "after", "limit",
            ],
        )
        self.assertIs(sent["leaf_only"], False)
        self.assertEqual(sent["after"], "1001")

    def test_gl_aux(self) -> None:
        query = GlAuxQuery("project", 8, 8, project_class="00", dim_code="P01", after="a|b")
        path, sent = self._send(lambda client: client.report_gl_aux_balance(_call(), query))
        self.assertEqual(path, "/u8co/v1/reports/gl_aux_balance")
        self.assertEqual(list(sent), _AUTH + ["dim", "period_from", "period_to", "dim_code", "project_class", "after"])
        self.assertEqual(sent["after"], "a|b")

    def test_arap(self) -> None:
        query = ArapQuery("ap", "2026-09-26", ("220201", "220202"), (), "V001", False, "V0", 50)
        path, sent = self._send(lambda client: client.report_arap_balance(_call(), query))
        self.assertEqual(path, "/u8co/v1/reports/arap_balance")
        self.assertEqual(
            list(sent),
            _AUTH + ["side", "as_of", "accounts", "exclude_accounts", "partner", "nonzero", "after", "limit"],
        )
        self.assertEqual(sent["accounts"], ["220201", "220202"])
        self.assertEqual(sent["exclude_accounts"], [])
        self.assertIs(sent["nonzero"], False)
        aging = ArapQuery("ar", basis="due", buckets=(30, 60, 90))
        path, sent = self._send(lambda client: client.report_arap_aging(_call(), aging))
        self.assertEqual(path, "/u8co/v1/reports/arap_aging")
        self.assertEqual(list(sent), _AUTH + ["side", "basis", "buckets"])
        self.assertEqual(sent["buckets"], [30, 60, 90])
        by_person = ArapQuery("ar", basis="due", group_by="person", person=("P001", "P002"), overdue_only=True)
        _path, sent = self._send(lambda client: client.report_arap_aging(_call(), by_person))
        self.assertEqual(list(sent), _AUTH + ["side", "basis", "group_by", "person", "overdue_only"])
        self.assertEqual(sent["person"], ["P001", "P002"])
        self.assertIs(sent["overdue_only"], True)

    def test_arap_aging_default_credit_days(self) -> None:
        aging = ArapQuery("ar", basis="due", default_credit_days=30)
        _path, sent = self._send(lambda client: client.report_arap_aging(_call(), aging))
        self.assertEqual(list(sent), _AUTH + ["side", "basis", "default_credit_days"])
        self.assertEqual(sent["default_credit_days"], 30)
        zero = ArapQuery("ap", basis="due", default_credit_days=0)
        _path, sent = self._send(lambda client: client.report_arap_aging(_call(), zero))
        self.assertEqual(sent["default_credit_days"], 0)

    def test_bom(self) -> None:
        path, sent = self._send(lambda client: client.report_bom(_call(), BomQuery("INV0012")))
        self.assertEqual(path, "/u8co/v1/reports/bom")
        self.assertEqual(list(sent), _AUTH + ["parent"])
        query = BomQuery("INV0012", "2026-09-26", 10, 5000)
        _path, sent = self._send(lambda client: client.report_bom(_call(), query))
        self.assertEqual(list(sent), _AUTH + ["parent", "as_of", "levels", "limit"])


class ValidationTests(unittest.TestCase):
    def _refused(self, invoke: Callable[[U8CoClient], object], text: str) -> None:
        with self.assertRaises(ValueError) as caught:
            invoke(_offline())
        self.assertIn(text, str(caught.exception))

    def test_gl_rules(self) -> None:
        self._refused(lambda c: c.report_close_status(_call(), 1999), "fiscal_year")
        self._refused(lambda c: c.report_gl_balance(_call(), GlBalanceQuery(9, 8)), "period_from")
        self._refused(lambda c: c.report_gl_balance(_call(), GlBalanceQuery(0, 8)), "period_from")
        self._refused(lambda c: c.report_gl_balance(_call(), GlBalanceQuery(1, 8, grade_from=3, grade_to=2)), "grade")
        self._refused(lambda c: c.report_gl_balance(_call(), GlBalanceQuery(1, 8, grade_to=10)), "grade_to")
        self._refused(lambda c: c.report_gl_balance(_call(), GlBalanceQuery(1, 8, code_prefix="10%")), "code_prefix")
        self._refused(lambda c: c.report_gl_balance(_call(), GlBalanceQuery(1, 8, limit=1001)), "limit")
        self._refused(lambda c: c.report_gl_balance(_call(), GlBalanceQuery(1, 8, nonzero="yes")), "布尔")
        self._refused(lambda c: c.report_gl_aux_balance(_call(), GlAuxQuery("account", 1, 8)), "dim")
        self._refused(
            lambda c: c.report_gl_aux_balance(_call(), GlAuxQuery("customer", 1, 8, project_class="00")),
            "project_class",
        )
        self._refused(
            lambda c: c.report_gl_aux_balance(_call(), GlAuxQuery("project", 1, 8, project_class="0-0")),
            "project_class",
        )

    def test_arap_and_bom_rules(self) -> None:
        self._refused(lambda c: c.report_arap_balance(_call(), ArapQuery("both")), "side")
        self._refused(lambda c: c.report_arap_balance(_call(), ArapQuery("ar", as_of="2026-02-30")), "as_of")
        self._refused(lambda c: c.report_arap_balance(_call(), ArapQuery("ar", accounts=())), "accounts")
        self._refused(lambda c: c.report_arap_balance(_call(), ArapQuery("ar", accounts=("1",) * 21)), "accounts")
        self._refused(lambda c: c.report_arap_balance(_call(), ArapQuery("ar", basis="due")), "basis")
        self._refused(lambda c: c.report_arap_aging(_call(), ArapQuery("ar", basis="invoice")), "basis")
        for bad in ((), (60, 30), (30, 30), (0,), (3651,), tuple(range(1, 12))):
            self._refused(lambda c, b=bad: c.report_arap_aging(_call(), ArapQuery("ar", buckets=b)), "buckets")
        self._refused(lambda c: c.report_arap_balance(_call(), ArapQuery("ar", group_by="person")), "group_by")
        self._refused(lambda c: c.report_arap_balance(_call(), ArapQuery("ar", person=("P1",))), "person")
        self._refused(lambda c: c.report_arap_aging(_call(), ArapQuery("ar", group_by="dept")), "group_by")
        for bad in ((), ("P",) * 21, ("",), ("P" * 21,), ("a\tb",)):
            self._refused(lambda c, b=bad: c.report_arap_aging(_call(), ArapQuery("ar", person=b)), "person")
        self._refused(lambda c: c.report_arap_aging(_call(), ArapQuery("ar", overdue_only=True)), "overdue_only")
        self._refused(lambda c: c.report_arap_aging(_call(), ArapQuery("ar", basis="due", overdue_only=1)), "布尔")
        self._refused(lambda c: c.report_bom(_call(), BomQuery("")), "parent")
        self._refused(lambda c: c.report_bom(_call(), BomQuery("P", levels=11)), "levels")
        self._refused(lambda c: c.report_bom(_call(), BomQuery("P", limit=5001)), "limit")


    def test_default_credit_days_rules(self) -> None:
        days = "default_credit_days"
        self._refused(lambda c: c.report_arap_aging(_call(), ArapQuery("ar", default_credit_days=30)), days)
        self._refused(
            lambda c: c.report_arap_aging(_call(), ArapQuery("ar", basis="document", default_credit_days=30)), days
        )
        for bad in (-1, 3651, "30", 1.5, True):
            query = ArapQuery("ar", basis="due", default_credit_days=bad)  # type: ignore[arg-type]
            self._refused(lambda c, q=query: c.report_arap_aging(_call(), q), days)
        self._refused(lambda c: c.report_arap_balance(_call(), ArapQuery("ar", default_credit_days=30)), days)


class CliTests(unittest.TestCase):
    def test_parse_default_credit_days(self) -> None:
        argv = _argv("report-arap-aging", "--side", "ar", "--basis", "due", "--default-credit-days", "45")
        query = arap_query(_parser().parse_args(argv))
        self.assertEqual((query.basis, query.default_credit_days), ("due", 45))
        plain = arap_query(_parser().parse_args(_argv("report-arap-aging", "--side", "ar")))
        self.assertIsNone(plain.default_credit_days)

    def test_parse_arap(self) -> None:
        parsed = _parser().parse_args(
            _argv(
                "report-arap-aging", "--side", "ar", "--account", "1122", "--exclude-account", "112204",
                "--exclude-account", "112205", "--include-zero", "--basis", "due", "--buckets", "30, 60,90",
            ),
        )
        query = arap_query(parsed)
        self.assertEqual(query.accounts, ("1122",))
        self.assertEqual(query.exclude_accounts, ("112204", "112205"))
        self.assertIs(query.nonzero, False)
        self.assertEqual((query.basis, query.buckets), ("due", (30, 60, 90)))
        plain = arap_query(_parser().parse_args(_argv("report-arap-balance", "--side", "ap")))
        self.assertEqual(plain, ArapQuery("ap"))
        argv = _argv(
            "report-arap-aging", "--side", "ar", "--basis", "due", "--group-by", "partner_person",
            "--person", "P001", "--person", "P002", "--overdue-only",
        )
        query = arap_query(_parser().parse_args(argv))
        self.assertEqual((query.group_by, query.person, query.overdue_only), ("partner_person", ("P001", "P002"), True))

    def test_buckets_text(self) -> None:
        self.assertIsNone(parse_buckets(None))
        for bad in ("", "30,,60", "a", "-1", "30;60"):
            with self.assertRaises(SystemExit):
                parse_buckets(bad)

    def test_choices_reject_wrong_values(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-gl-aux", "--dim", "account", "--period-from", "1", "--period-to", "2"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-arap-balance", "--side", "both"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-arap-balance", "--side", "ar", "--buckets", "30"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-bom"))

    def test_main_wires_reports(self) -> None:
        seen: dict[str, Any] = {}

        def _fake(name: str) -> Callable[..., dict[str, Any]]:
            def invoke(self: U8CoClient, call: U8Call, query: object = None) -> dict[str, Any]:
                seen[name] = query
                return {"ok": True}

            return invoke

        cases = (
            (_argv("report-close-status", "--fiscal-year", "2026"), "report_close_status", 2026),
            (
                _argv("report-gl-balance", "--period-from", "1", "--period-to", "8", "--include-unposted"),
                "report_gl_balance",
                GlBalanceQuery(1, 8, include_unposted=True),
            ),
            (
                _argv("report-gl-aux", "--dim", "customer", "--period-from", "8", "--period-to", "8", "--nonzero"),
                "report_gl_aux_balance",
                GlAuxQuery("customer", 8, 8, nonzero=True),
            ),
            (_argv("report-arap-balance", "--side", "ar"), "report_arap_balance", ArapQuery("ar")),
            (_argv("report-arap-aging", "--side", "ap"), "report_arap_aging", ArapQuery("ap")),
            (_argv("report-bom", "--parent", "P1", "--levels", "3"), "report_bom", BomQuery("P1", "", 3, None)),
        )
        for argv, method, want in cases:
            self.assertEqual(_run_main(argv, method, _fake(method)), 0)
            self.assertEqual(seen[method], want)


if __name__ == "__main__":
    unittest.main()
