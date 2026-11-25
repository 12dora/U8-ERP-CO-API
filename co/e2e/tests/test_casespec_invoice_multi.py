"""一张销售发票参照多张发货单（invoice_multi）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import invoice_multi as m
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.invoice_multi_sql import (
    family_col,
    family_col_sql,
    inv_count_sql,
    inv_lines_sql,
    invoice_multi_samples,
    settle_sql,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_GEN = "/v1/vouchers/generate"
_PICK = "ROW:C1|ST|D1|P1|I1|W1\n"
# 发票两行（U8 界面口径）：A 2 × 1 = 2.00 = 1.77 + 0.23；B 1 × 1 = 1.00 = 0.88 + 0.12。
_AMT = "ROW:51|2.000000|1.000000|2.00|1.77|0.23\nROW:52|1.000000|1.000000|1.00|0.88|0.12\n"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


class _Sql:
    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-03", bag={})


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _client(body_define: bool, made: dict | None = None, family: str = "") -> _Replay:
    gens = [
        _ok(id=2),
        _ok(id=3),
        U8CoError(400, "bad_request", "来源发货单重复"),
        U8CoError(409, "state_mismatch", "超过可生单数量"),
        U8CoError(400, "bad_request", "明细行不存在"),
        _ok(dry_run=True),
        made or _ok(id=9, source_id=2, source_ids=[2, 3], lines=2),
    ]
    answers = {
        "/v1/vouchers/create": [_ok(id=1)],
        "/v1/vouchers/verify": [_ok() for _ in range(6)],
        _GEN: gens,
        "/v1/vouchers/delete": [_ok(deleted=True) for _ in range(4)],
    }
    if body_define or family:
        first: dict = {"iDLsID": 41, "cbdefine1": "2.150000"} if body_define else {"iDLsID": 41}
        if family:
            first["cbDefine7"] = family
        answers["/v1/vouchers/load"] = [_ok(lines=[first, {"iDLsID": 42}])]
    return _Replay(answers)


def _sql(body_define: bool, after: tuple[str, str] = ("2.0000", "1.0000"), family: str | None = None, amt: str = _AMT,
         flags: str = "ROW:51|1\nROW:52|1\n") -> _Sql:
    """family 为 None：账套没有族成员列；否则账套有 cbdefine7，family 是库里 A 行读回的值。"""
    sign = ("2.15", "0.950000") if body_define else ("", "")
    tail = ("", "") if family is None else (f"|{family}", "|")
    return _Sql([
        ("a.cName = N'bSalePrice'", ["ROW:True\n"]),
        ("N'bsaleprice') IS NULL", ["ROW:1\n"]),
        ("CONVERT(int, b.bsaleprice)", [flags]),
        ("LIKE N'cbdefine[1-9]'", ["ROW:cbdefine7\n" if family is not None else ""]),
        ("AS v\nFROM dbo.Warehouse AS w", [_PICK]),
        ("MIN(d.iSOsID)", ["ROW:31\n", "ROW:31\n"]),
        ("MIN(x.iDLsID)", ["ROW:41\n", "ROW:42\n"]),
        ("d.iDLsID = 41", ["ROW:0.0000\n"] * 4 + [f"ROW:{after[0]}\n", "ROW:0.0000\n"]),
        ("d.iDLsID = 42", ["ROW:0.0000\n"] * 4 + [f"ROW:{after[1]}\n", "ROW:0.0000\n"]),
        ("COL_LENGTH", ["ROW:1\n" if body_define else "ROW:0\n"]),
        ("WHERE b.iDLsID IN", ["ROW:0\n", "ROW:0\n"]),
        ("FROM dbo.SaleBillVouchs AS b LEFT JOIN", [f"ROW:41|2.0000|1|{sign[0]}{tail[0]}\nROW:42|1.0000|1|{sign[1]}{tail[1]}\n"]),
        ("FROM dbo.SaleBillVouch AS h WHERE h.SBVID", ["ROW:0\n"]),
        ("b.iTaxUnitPrice", [amt]),
    ])


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return m.sale_invoice_multi(_env(client), _SPEC, _STEP)


class RegistryTests(unittest.TestCase):
    def test_registered_once_with_op_and_routes(self) -> None:
        (spec,) = m.invoice_multi_cases()
        self.assertEqual(spec.name, "sale-invoice-multi-dispatch")
        self.assertEqual(known_names().count(spec.name), 1)
        self.assertTrue(spec.requires_login)
        self.assertIn(_GEN, spec.routes)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertIn("w:sale_invoice", spec.tags)

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in invoice_multi_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("x.cbdefine1", inv_lines_sql("7", True))
        self.assertNotIn("extradefine", inv_lines_sql("7", False))
        self.assertIn("x.cbdefine12", inv_lines_sql("7", False, "cbdefine12"))
        self.assertIn("extradefine", inv_lines_sql("7", False, "cbdefine12"))
        calls = (
            lambda: settle_sql("0"),
            lambda: inv_lines_sql("x", True),
            lambda: inv_count_sql("1", "2 OR 1=1"),
            lambda: inv_lines_sql("7", True, "cbdefine1"),
            lambda: inv_lines_sql("7", True, "cbdefine7; --"),
        )
        for call in calls:
            with self.assertRaises(ValueError):
                call()

    def test_family_column_comes_from_the_catalog(self) -> None:
        text = family_col_sql()
        for needle in ("sys.columns", "SaleBillVouchs_extradefine", "N'cbdefine1', N'cbdefine2', N'cbdefine3'", "N'nvarchar'"):
            self.assertIn(needle, text)
        self.assertEqual(family_col(" cbDefine12 "), "cbdefine12")
        for bad in ("cbdefine1", "cbdefine3", "cbdefine04", "cbdefine100", "chdefine5", ""):
            self.assertEqual(family_col(bad), "", bad)


class ReplayTests(unittest.TestCase):
    def test_pass_with_body_define(self) -> None:
        client = _client(True)
        status, note = _run(client, _sql(True))
        self.assertEqual(status, "PASS", note)
        self.assertIn("cbdefine1 写入并读回", note)
        gens = [fields for route, fields in client.calls if route == _GEN]
        many = gens[6]
        self.assertNotIn("id", many)
        self.assertEqual([one["id"] for one in many["sources"]], [2, 3])
        self.assertEqual(many["sources"][0]["lines"], [{"source_line_id": 41, "quantity": 2, "cbdefine1": "2.15"}])
        self.assertEqual(many["sources"][1]["lines"], [{"source_line_id": 42, "quantity": 1, "cbdefine1": "0.95"}])
        self.assertIs(gens[5]["dry_run"], True)
        self.assertEqual([one["id"] for one in gens[2]["sources"]], [2, 2])

    def test_pass_without_body_define_column(self) -> None:
        status, note = _run(_client(False), _sql(False))
        self.assertEqual(status, "PASS", note)
        self.assertIn("未带 cbdefine1", note)
        self.assertIn("未带族成员", note)

    def _family_run(self, body_define: bool, stored: str | None = None) -> tuple[str, str, _Replay]:
        # 值是用例现编的（E2EF + 随机），先固定随机数，库和 vouchers/load 的回答用同一个值。
        with mock.patch.object(m.os, "urandom", return_value=bytes((0xAB, 0xCD))):
            client = _client(body_define, family="E2EFABCD")
            status, note = _run(client, _sql(body_define, family="E2EFABCD" if stored is None else stored))
        return status, note, client

    def test_pass_with_a_family_member_column(self) -> None:
        for body_define in (True, False):
            status, note, client = self._family_run(body_define)
            self.assertEqual(status, "PASS", note)
            self.assertIn("族成员 cbdefine7 写入并读回", note)
            many = [fields for route, fields in client.calls if route == _GEN][6]
            self.assertEqual(many["sources"][0]["lines"][0]["cbdefine7"], "E2EFABCD")
            self.assertNotIn("cbdefine7", many["sources"][1]["lines"][0])
            self.assertEqual("cbdefine1" in many["sources"][0]["lines"][0], body_define)

    def test_fail_when_family_member_not_stored(self) -> None:
        status, note, _client = self._family_run(True, stored="")
        self.assertEqual(status, "FAIL")
        self.assertIn("cbdefine7", note)

    def test_fail_when_settle_not_added(self) -> None:
        status, note = _run(_client(True), _sql(True, ("2.0000", "0.0000")))
        self.assertEqual(status, "FAIL")
        self.assertIn("累计开票", note)

    def test_fail_when_partial_line_follows_untaxed_price(self) -> None:
        # B 行按无税单价 0.884956 算：0.88 + 0.11 = 0.99，与 round(1 × 1, 2) 差一分。
        amt = "ROW:51|2.000000|1.000000|2.00|1.77|0.23\nROW:52|1.000000|1.000000|0.99|0.88|0.11\n"
        status, note = _run(_client(True), _sql(True, amt=amt))
        self.assertEqual(status, "FAIL")
        self.assertIn("行 52 价税合计 0.99 应为", note)

    def test_pass_when_account_quotes_untaxed(self) -> None:
        # 账套报价不含税：B 行按无税单价算 0.99 = 0.88 + 0.11，bsaleprice=0；只核价税合计 = 无税金额 + 税额。
        amt = "ROW:51|2.000000|1.000000|2.00|1.77|0.23\nROW:52|1.000000|1.000000|0.99|0.88|0.11\n"
        with mock.patch.object(m, "sale_taxed", return_value=False):
            status, note = _run(_client(True), _sql(True, amt=amt, flags="ROW:51|0\nROW:52|0\n"))
        self.assertEqual(status, "PASS", note)
        self.assertIn("账套报价不含税", note)
        self.assertIn("sale_invoice bsaleprice=0", note)

    def test_fail_when_invoice_line_not_taxed(self) -> None:
        status, note = _run(_client(True), _sql(True, flags="ROW:51|1\nROW:52|\n"))
        self.assertEqual(status, "FAIL")
        self.assertIn("行 52 的 bsaleprice 应为 1", note)

    def test_fail_when_response_lacks_source_ids(self) -> None:
        status, note = _run(_client(True, _ok(id=9, source_id=2, lines=2)), _sql(True))
        self.assertEqual(status, "FAIL")
        self.assertIn("source_ids", note)


if __name__ == "__main__":
    unittest.main()
