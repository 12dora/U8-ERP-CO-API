"""客户开票单位用例 `customer-invoice-company-cycle` 的登记、分线、SQL 守卫和一轮回放。不连接服务器。"""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e.casespec import arc_cus_invoice as mod
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.arc_write import CORE_HANDLERS
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.arc_cus_invoice_sql import (
    cusinvoice_samples,
    missing_sql,
    pick_sql,
    rows_sql,
)
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "customer-invoice-company-cycle"
_CUSTOMERS = ("C900001", "C900002", "C900003")


class _Bridge:
    """一个客户的开票单位表 + 客户表头；路由按桥的规则改它，SQL 按关键字应答。"""

    def __init__(self, move_header: bool = True) -> None:
        self.rows: list[list] = [["C900001", 1], ["C900003", 0]]
        self.header = "C900001"
        self.move_header = move_header
        self.calls: list[str] = []

    def call(self, route: str, fields: dict) -> dict:
        op = route.rsplit("/", 1)[1]
        self.calls.append(op)
        code = fields.get("code", "")
        customer, sep, company = code.partition(":")
        if not sep or not company:
            raise U8CoError(400, "bad_request", "code 格式不对")
        if op != "customer_invoice_company_add" and not isinstance(
            fields.get("expect"), dict
        ):
            raise U8CoError(400, "bad_request", "缺 expect")
        if customer != "C900001":
            raise U8CoError(404, "not_found", "档案不存在")
        if op != "customer_invoice_company_delete" and company not in _CUSTOMERS:
            raise U8CoError(409, "invoice_company_invalid", "开票单位不存在")
        before = self._view()
        dry = bool(fields.get("dry_run"))
        snapshot = [list(row) for row in self.rows], self.header
        changed = getattr(self, "_" + op)(company, fields)
        out = {
            "ok": True,
            "code": code,
            "changed": changed,
            "before": before,
            "after": self._view(),
        }
        if dry:
            self.rows, self.header = snapshot[0], snapshot[1]
            out.update(dry_run=True, mode="validate")
        return out

    def _view_of(self, rows: list) -> list[dict]:
        return [{"code": code, "bdefault": bool(flag)} for code, flag in rows]

    def _view(self) -> list[dict]:
        return self._view_of(self.rows)

    def _find(self, company: str) -> list | None:
        return next((row for row in self.rows if row[0] == company), None)

    def _customer_invoice_company_add(self, company: str, fields: dict) -> bool:
        row = self._find(company)
        if row is not None:
            if fields.get("default") and not row[1]:
                raise U8CoError(409, "invoice_company_taken", "已存在但不是默认")
            return False
        if fields.get("default"):
            for item in self.rows:
                item[1] = 0
        self.rows.append([company, 1 if fields.get("default") else 0])
        return True

    def _need(self, company: str, fields: dict) -> list:
        row = self._find(company)
        if row is None:
            raise U8CoError(404, "invoice_company_not_found", "没有这个开票单位")
        if bool(row[1]) != fields["expect"]["bdefault"]:
            raise U8CoError(409, "state_mismatch", "默认标志不符")
        return row

    def _customer_invoice_company_delete(self, company: str, fields: dict) -> bool:
        row = self._need(company, fields)
        if row[1]:
            raise U8CoError(
                409,
                "invoice_company_is_default",
                "默认开票单位不能删除，请先设置其他默认",
            )
        self.rows.remove(row)
        return True

    def _customer_invoice_company(self, company: str, fields: dict) -> bool:
        row = self._need(company, fields)
        if row[1]:
            return False
        for item in self.rows:
            item[1] = 0
        row[1] = 1
        if self.move_header:
            self.header = company
        return True

    def guarded(self, _env: object, query: str, _label: str) -> str:
        for key, handler in (
            ("INNER JOIN dbo.Customer AS o", lambda _q: "ROW:C900001\tC900002\n"),
            ("VALUES (N'ZZNOINV01'", lambda _q: "ROW:ZZNOINV01\n"),
            ("DELETE FROM dbo.sa_invoicecustomers", self._sql_delete),
            ("UPDATE dbo.sa_invoicecustomers", self._sql_reset),
            ("FROM dbo.sa_invoicecustomers AS s WHERE s.ccuscode = N'", self._sql_rows),
            ("dbo.Customer AS t", self._sql_header),
        ):
            if key in query:
                return handler(query)
        raise AssertionError("没有料到的 SQL：" + query[:80])

    def _sql_rows(self, _query: str) -> str:
        return "".join(f"ROW:{code}\t{flag}\n" for code, flag in self.rows)

    def _sql_header(self, _query: str) -> str:
        return f"ROW:cInvoiceCompany\t{self.header}\nROW:cModifyPerson\t演示\nROW:cCusName\th:abc\n"

    def _sql_delete(self, query: str) -> str:
        company = re.search(r"cinvoicecompany\)\) = N'(\w+)'", query).group(1)  # type: ignore[union-attr]
        gone = [row for row in self.rows if row[0] == company]
        for row in gone:
            self.rows.remove(row)
        return f"ROW:{len(gone)}\n"

    def _sql_reset(self, query: str) -> str:
        company = re.search(r"cinvoicecompany\)\) = N'(\w+)' THEN 1", query).group(1)  # type: ignore[union-attr]
        for row in self.rows:
            row[1] = 1 if row[0] == company else 0
        self.header = company
        return f"ROW:{len(self.rows)}\n"


def _run(bridge: _Bridge) -> tuple[str, str]:
    spec = mod.cusinv_cases()[0]
    env = SimpleNamespace(
        client=bridge,
        operator=SimpleNamespace(acc="801", year="2026", code="op001", password="pw"),
        year="2026",
        today="2026-09-28",
        operator_name="",
    )
    with mock.patch.object(mod, "run_guarded", bridge.guarded):
        return mod.step_cusinv(env, spec, spec.steps[0])


class CatalogTests(unittest.TestCase):
    def test_case_registered(self) -> None:
        (spec,) = mod.cusinv_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(spec.requires_login)
        self.assertEqual(
            spec.routes,
            (
                "/v1/archives/customer_invoice_company_add",
                "/v1/archives/customer_invoice_company_delete",
                "/v1/archives/customer_invoice_company",
            ),
        )
        self.assertIs(CORE_HANDLERS["core_cusinv"], mod.step_cusinv)
        self.assertIs(_HANDLERS["core_cusinv"], mod.step_cusinv)

    def test_tags_serialise_with_the_other_customer_writes(self) -> None:
        (spec,) = mod.cusinv_cases()
        tags = auto_tags(spec)
        for tag in ("arc:customer", "arc:write"):
            self.assertIn(tag, tags)
        self.assertTrue(conflicts(tags, frozenset({"arc:customer"})))


class SqlTests(unittest.TestCase):
    # 挑单 SQL 读配置里的账套库名：装示例配置，不依赖本机的 e2e.json。
    def setUp(self) -> None:
        support.install()
        self.addCleanup(support.reset)

    def test_samples_pass_the_guard(self) -> None:
        for name, sql in cusinvoice_samples():
            self.assertEqual(sql_block_reason(sql), "", name)
            self.assertIn("ROW:", sql, name)

    def test_pick_wants_a_consistent_customer(self) -> None:
        sql = pick_sql()
        self.assertIn("s.bdefault = 1) = 1", sql)
        self.assertIn("c.cInvoiceCompany", sql)
        self.assertIn("NOT EXISTS", sql)

    def test_rows_and_missing(self) -> None:
        self.assertIn("ORDER BY s.autoid", rows_sql("C900001"))
        self.assertIn("NOT EXISTS", missing_sql())
        with self.assertRaises(ValueError):
            rows_sql("C9' OR 1=1")


class ReplayTests(unittest.TestCase):
    def test_passes_and_restores_every_row(self) -> None:
        bridge = _Bridge()
        status, note = _run(bridge)
        self.assertEqual(status, "PASS", note)
        self.assertEqual(bridge.rows, [["C900001", 1], ["C900003", 0]])
        self.assertEqual(bridge.header, "C900001")
        calls = bridge.calls
        for op in (
            "customer_invoice_company_add",
            "customer_invoice_company_delete",
            "customer_invoice_company",
        ):
            self.assertIn(op, calls)

    def test_header_not_moving_fails_and_is_cleaned_up(self) -> None:
        bridge = _Bridge(move_header=False)
        status, note = _run(bridge)
        self.assertEqual(status, "FAIL")
        self.assertIn("客户表头", note)
        self.assertEqual(bridge.rows, [["C900001", 1], ["C900003", 0]])
        self.assertEqual(bridge.header, "C900001")

    def test_no_data_is_not_applicable(self) -> None:
        bridge = _Bridge()
        bridge.guarded = lambda _env, query, _label: (
            "" if "INNER JOIN" in query else "ROW:ZZNOINV01\n"
        )  # type: ignore[method-assign]
        status, _note = _run(bridge)
        self.assertEqual(status, "N/A")


if __name__ == "__main__":
    unittest.main()
