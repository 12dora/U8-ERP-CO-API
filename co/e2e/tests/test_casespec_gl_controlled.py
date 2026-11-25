"""总账受控科目选项（gl_controlled）的登记、分片、SQL 形状和回放核对。不连接服务器。

重点核对恢复：无论步骤成败，新建过的凭证都作废删除，选项按原值改回并回读核对。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import flip_util as util
from co.e2e.casespec import gl_controlled as m
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.gl_controlled_sql import (
    account_pick_sql,
    made_by_digest_sql,
    option_restore_sql,
    option_set_sql,
    option_sql,
    gl_controlled_samples,
    voucher_rows_sql,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.gl_voucher_sql import gl_key
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("gl-controlled-ar-option", "gl-controlled-ap-option")
_SPEC = CaseSpec("s", "s")
_CREATE = "/v1/gl/vouchers/create"
_AR_REFUSED = "贷方科目 112201 是应收系统受控科目，总账凭证不能使用"


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
    """按 SQL 里的特征串依次回答（每个键一个队列，按登记顺序找第一个还有回答的键），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]
        self.seen: list[str] = []

    def __call__(self, _env: object, query: str, _label: str) -> str:
        self.seen.append(query)
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag={})


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _step(side: str) -> Step:
    return Step("custom", fields=(("side", side),), sql_name="gl_controlled", id_from="none")


def _run(client: _Replay, sql: _Sql, side: str = "AR") -> tuple[str, str]:
    with mock.patch.object(util, "run_guarded", sql):
        return m.gl_controlled(_env(client), _SPEC, _step(side))


F = "ROW:1|0|460061006C0073006500|False\n"
ONE = "ROW:1|0|3100|1\n"


def _sql(reads: list[str], updates: list[str], rows: list[str], pick: str = "ROW:112201|C001\n",
         digest: list[str] | None = None) -> _Sql:
    return _Sql([
        ("UPDATE dbo.AccInformation", updates),
        ("cdigest = N'CO桥回归'", digest or []),
        ("FROM dbo.AccInformation", reads),
        ("FROM dbo.code AS k", [pick]),
        ("FROM dbo.GL_accvouch", rows),
    ])


def _client(*creates: object) -> _Replay:
    return _Replay({_CREATE: list(creates), "/v1/gl/vouchers/void": [_ok()], "/v1/gl/vouchers/delete": [_ok()]})


def _routes(client: _Replay) -> list[str]:
    return [route.rsplit("/", 1)[1] for route, _fields in client.calls]


_MADE = _ok(period=10, sign="转", no=7)


class RegistryTests(unittest.TestCase):
    def test_registered_with_ops_and_tags(self) -> None:
        names = known_names()
        cases = m.gl_controlled_cases()
        self.assertEqual(tuple(spec.name for spec in cases), _NAMES)
        for spec, side in zip(cases, ("AR", "AP")):
            with self.subTest(case=spec.name):
                self.assertEqual(names.count(spec.name), 1)
                self.assertTrue(spec.requires_login)
                self.assertEqual(dict(spec.steps[0].fields), {"side": side})
                self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
                self.assertIn("gl", auto_tags(spec))
                self.assertIn("gl.sign_transfer", spec.data)

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in gl_controlled_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("cSysID = N'GL' AND cName = N'bUseYFKM'", option_sql("bUseYFKM"))
        self.assertIn("CASE WHEN cValue IS NULL THEN 1 ELSE 0 END", option_sql("bUseYSKM"))
        self.assertIn("SET cValue = NULL", option_restore_sql("bUseYSKM", None, "True"))
        self.assertIn("SET cValue = N''", option_restore_sql("bUseYSKM", "", "True"))
        self.assertIn("dbill_date = '2026-10-02' AND cdigest = N'CO桥回归'", made_by_digest_sql("2026", "2026-10-02", "112201"))
        update = option_set_sql("bUseYSKM", "True", "False")
        self.assertIn("SET cValue = N'True'", update)
        self.assertIn("= N'False'", update)
        ar, ap = account_pick_sql("AR", "2026"), account_pick_sql("AP", "2026")
        self.assertIn("LIKE N'%AR%'", ar)
        self.assertIn("NOT LIKE N'%AP%'", ar)
        self.assertIn("ISNULL(k.bcus, 0) = 1", ar)
        self.assertIn("ISNULL(k.bsup, 0) = 1", ap)
        self.assertIn("dbo.Vendor AS u", ap)
        bad = (
            lambda: option_sql("bMasterSign"), lambda: option_set_sql("bUseYSKM", "1'", "0"),
            lambda: account_pick_sql("GL", "2026"), lambda: account_pick_sql("AR", "26"),
            lambda: voucher_rows_sql("2026", gl_key(1, "转", 1), "11'22"),
            lambda: option_restore_sql("bUseYSKM", "0x41", "True"), lambda: option_restore_sql("bUseYSKM", "410", "True"),
            lambda: made_by_digest_sql("2026", "2026/10/02", "112201"),
        )
        for call in bad:
            with self.assertRaises(ValueError):
                call()

    def test_switch_values(self) -> None:
        self.assertEqual(m.switch_values("1"), ("0", "1"))
        self.assertEqual(m.switch_values("False"), ("False", "True"))
        self.assertEqual(m.switch_values(""), ("False", "True"))


class FlipTests(unittest.TestCase):
    def test_off_refused_on_accepted_then_restored(self) -> None:
        client = _client(U8CoError(400, "bad_request", _AR_REFUSED), _MADE)
        sql = _sql([F, F], ["ROW:1\n", "ROW:1\n"], ["ROW:2|1|0|GL\n", "ROW:0|0|-|\n"])
        status, detail = _run(client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("bvalueedit=0", detail)
        self.assertIn("coutsysname=GL", detail)
        self.assertEqual(_routes(client), ["create", "create", "void", "delete"])
        lines = client.calls[0][1]["lines"]
        self.assertEqual((lines[0]["account"], lines[1]["account"], lines[1]["customer"]), ("6602", "112201", "C001"))
        self.assertEqual(client.calls[2][1]["no"], 7)
        updates = [query for query in sql.seen if query.startswith("SET NOCOUNT ON;\nUPDATE")]
        self.assertIn("SET cValue = N'True'", updates[0])
        self.assertIn("SET cValue = CONVERT(nvarchar(100), 0x460061006C0073006500)", updates[1])

    def test_numeric_option_keeps_numeric_form(self) -> None:
        client = _client(U8CoError(400, "bad_request", _AR_REFUSED), _MADE)
        sql = _sql([ONE, ONE], ["ROW:1\n", "ROW:1\n"], ["ROW:2|1|0|GL\n", "ROW:0|0|-|\n"])
        status, detail = _run(client, sql)
        self.assertEqual(status, "PASS", detail)
        updates = [query for query in sql.seen if "UPDATE dbo.AccInformation" in query]
        self.assertEqual(len(updates), 2)
        self.assertIn("SET cValue = N'0'", updates[0])
        self.assertIn("SET cValue = N'1'", updates[1])

    def test_off_not_refused_drops_voucher(self) -> None:
        client = _client(_MADE)
        sql = _sql([F, F], [], ["ROW:0|0|-|\n"])
        status, detail = _run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("应 400", detail)
        self.assertEqual(_routes(client), ["create", "void", "delete"])

    def test_u8_refusal_when_on_fails_and_restores(self) -> None:
        client = _client(U8CoError(400, "bad_request", _AR_REFUSED), U8CoError(409, "u8_rejected", "导入失败"))
        sql = _sql([F, F], ["ROW:1\n", "ROW:1\n"], [], digest=[""])
        status, detail = _run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("桥与 U8 规则不一致", detail)
        self.assertEqual(_routes(client), ["create", "create"])
        self.assertEqual(sum("UPDATE dbo.AccInformation" in query for query in sql.seen), 2)

    def test_null_original_restored_as_null(self) -> None:
        client = _client(U8CoError(400, "bad_request", _AR_REFUSED), _MADE)
        null = "ROW:1|1||\n"
        sql = _sql([null, null], ["ROW:1\n", "ROW:1\n", "ROW:1\n"], ["ROW:2|1|0|GL\n", "ROW:0|0|-|\n"])
        status, detail = _run(client, sql)
        self.assertEqual(status, "PASS", detail)
        updates = [query for query in sql.seen if "UPDATE dbo.AccInformation" in query]
        self.assertEqual(len(updates), 3)
        self.assertIn("SET cValue = N'False'", updates[0])
        self.assertIn("SET cValue = NULL", updates[2])
        self.assertIn("= N'True'", updates[2])

    def test_unknown_create_finds_voucher_by_digest(self) -> None:
        client = _client(U8CoError(400, "bad_request", _AR_REFUSED), U8CoError(504, "outcome_unknown", "超时"))
        sql = _sql([F, F], ["ROW:1\n", "ROW:1\n"], ["ROW:0|0|-|\n"], digest=["ROW:10|转|8\n"])
        status, detail = _run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("504", detail)
        self.assertEqual(_routes(client), ["create", "create", "void", "delete"])
        self.assertEqual(client.calls[3][1]["no"], 8)
        lookup = next(query for query in sql.seen if "cdigest" in query)
        self.assertIn("dbill_date = '2026-10-02'", lookup)
        self.assertIn("ccode = N'112201'", lookup)

    def test_restore_mismatch_fails(self) -> None:
        client = _client(U8CoError(400, "bad_request", _AR_REFUSED), _MADE)
        sql = _sql([F, "ROW:1|0|5400720075006500|True\n"], ["ROW:1\n", "ROW:0\n"], ["ROW:2|1|0|GL\n", "ROW:0|0|-|\n"])
        status, detail = _run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("应原样改回 'False'", detail)

    def test_ap_side_uses_supplier_and_ap_text(self) -> None:
        refused = "贷方科目 220201 是应付系统受控科目，总账凭证不能使用"
        client = _client(U8CoError(400, "bad_request", refused), _MADE)
        sql = _sql([F, F], ["ROW:1\n", "ROW:1\n"], ["ROW:2|1|0|GL\n", "ROW:0|0|-|\n"],
                   pick="ROW:220201|V001\n")
        status, detail = _run(client, sql, "AP")
        self.assertEqual(status, "PASS", detail)
        self.assertIn("bUseYFKM", detail)
        self.assertEqual(client.calls[0][1]["lines"][1]["supplier"], "V001")
        self.assertIn("cName = N'bUseYFKM'", sql.seen[0])

    def test_missing_option_or_account_is_na(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(["ROW:0|0||\n"], [], []))
        self.assertEqual(status, "N/A")
        self.assertIn("恰好一行", detail)
        status, detail = _run(client, _sql([F], [], [], pick=""))
        self.assertEqual(status, "N/A")
        self.assertIn("挑不到", detail)
        self.assertEqual(client.calls, [])

    def test_bad_side_fails(self) -> None:
        status, _detail = _run(_client(), _sql([], [], []), "GL")
        self.assertEqual(status, "FAIL")


if __name__ == "__main__":
    unittest.main()
