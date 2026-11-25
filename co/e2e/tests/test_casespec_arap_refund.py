"""供应商退款、客户退款（arap_refund）的登记、分片、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import arap_refund as rf
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arap_refund_sql import arap_refund_samples, detail_sql, gone_sql, head_sql, pick_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("ap-refund-cycle", "ar-refund-cycle", "list-ap-refund", "list-ar-refund")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_GONE = U8CoError(404, "not_found", "单据不存在")
_MADE = "0000000001|8051|//ap48/0000000001|100.00||CO桥回归|1|100.00|0|8051"


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
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])


def _rows(*values: str) -> list[str]:
    return [f"ROW:{value}\n" for value in values]


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    bag = {"ap_master": "S0001|220201|100201|1|5101", "ar_master": "C0001|01|P01|112201|100201|1|6001"}
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-03", bag=bag)


def _client(**over: list) -> _Replay:
    answers: dict[str, list] = {
        "/v1/vouchers/create": [{"ok": True, "id": 7, "code": "0000000001"}],
        "/v1/vouchers/load": [{"ok": True, "code": "0000000001"}, _GONE, _GONE],
        "/v1/vouchers/update": [{"ok": True}],
        "/v1/vouchers/verify": [{"ok": True, "state": {"verified": True}}, {"ok": True, "state": {"verified": False}}],
        "/v1/vouchers/delete": [U8CoError(409, "state_mismatch", "单据已审核"), {"ok": True, "deleted": True}],
        "/v1/vouchers/list": [{"ok": True, "items": [{"id": 7, "vouch_type": "48", "ledger": "AP"}], "next": None},
                              {"ok": True, "items": [{"id": 7}], "next": None}],
    }
    answers.update(over)
    return _Replay(answers)


def _sql(details: tuple[str, ...] = ("2|0|-200.00", "0|0|0.00")) -> _Sql:
    edited = _MADE.replace("CO桥回归", "CO桥回归改")
    return _Sql([
        ("csysbarcode", _rows(_MADE, edited, edited)),
        ("iDAmount", _rows(*details)),
        ("FROM dbo.Ap_CloseBills WHERE iID", _rows("0")),
        ("ORDER BY h.iID DESC", _rows("7|0000000001|1")),
    ])


def _run(op: str, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return rf.ARAP_REFUND_OPS[op](_env(client), _SPEC, _STEP)


def _order(client: _Replay) -> list[tuple[str, str]]:
    return [(route.rsplit("/", 1)[1], fields.get("action", "")) for route, fields in client.calls]


class RegistryTests(unittest.TestCase):
    def test_registered_once_with_ops(self) -> None:
        cases = rf.arap_refund_cases()
        self.assertEqual(tuple(spec.name for spec in cases), _NAMES)
        for spec in cases:
            self.assertEqual(known_names().count(spec.name), 1)
            self.assertTrue(spec.requires_login and spec.routes)
            self.assertTrue(ops_run.has_op(spec.steps[-1].sql_name))
        self.assertIn("need:ap_master", auto_tags(cases[0]))
        self.assertIn("new:ar_refund", auto_tags(cases[1]))
        self.assertIn("list:ap_refund", auto_tags(cases[2]))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in arap_refund_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
        self.assertIn("h.cFlag = N'AP' AND h.cVouchType = N'48'", head_sql("ap_refund", "7"))
        self.assertIn("CardNumber = N'AR49'", head_sql("ar_refund", "7"))
        self.assertIn("dbo.Ar_Detail", detail_sql("ar_refund", "7"))
        self.assertIn("h.cFlag = N'AR' AND h.cVouchType = N'49'", pick_sql("ar_refund"))
        bad = (lambda: head_sql("ap_payment", "7"), lambda: detail_sql("ap_refund", "0"), lambda: gone_sql("x"))
        for call in bad:
            with self.assertRaises(ValueError):
                call()


class CycleTests(unittest.TestCase):
    def test_cycle_passes_and_cleans_up_in_order(self) -> None:
        client = _client()
        status, detail = _run("ap_refund_cycle", client, _sql())
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_order(client), [
            ("create", ""), ("load", ""), ("load", ""), ("update", ""), ("verify", "verify"), ("delete", ""),
            ("verify", "unverify"), ("delete", ""), ("load", ""),
        ])
        made = client.calls[0][1]
        self.assertEqual(made["type"], "ap_refund")
        self.assertEqual(made["head"]["cDwCode"], "S0001")
        self.assertEqual(made["lines"], [{"iType": 0, "cKm": "220201", "iAmt": 100}])
        self.assertEqual(client.calls[2][1]["type"], "ap_payment")

    def test_positive_detail_fails_after_cleanup(self) -> None:
        client = _client(**{"/v1/vouchers/delete": [{"ok": True, "deleted": True}]})
        status, detail = _run("ap_refund_cycle", client, _sql(("2|2|200.00",)))
        self.assertEqual(status, "FAIL")
        self.assertIn("负数", detail)
        self.assertNotIn("清理失败", detail)
        self.assertEqual(_order(client)[-2:], [("verify", "unverify"), ("delete", "")])

    def test_twin_type_loaded_fails(self) -> None:
        loads = [{"ok": True, "code": "0000000001"}, {"ok": True, "code": "0000000001"}]
        client = _client(**{"/v1/vouchers/load": loads, "/v1/vouchers/delete": [{"ok": True, "deleted": True}]})
        status, detail = _run("ap_refund_cycle", client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("404", detail)
        self.assertEqual(_order(client)[-1], ("delete", ""))

    def test_ar_body_uses_customer_master(self) -> None:
        client = _client()
        sql = _sql()
        sql.answers[0] = ("csysbarcode", _rows(*(row.replace("ap48", "ar49").replace("8051", "8055") for row in (
            _MADE, _MADE.replace("CO桥回归", "CO桥回归改"), _MADE.replace("CO桥回归", "CO桥回归改")))))
        status, detail = _run("ar_refund_cycle", client, sql)
        self.assertEqual(status, "PASS", detail)
        head = client.calls[0][1]["head"]
        self.assertEqual((head["cDwCode"], head["cDeptCode"], head["cPerson"]), ("C0001", "01", "P01"))
        self.assertEqual(client.calls[2][1]["type"], "ar_receipt")


class ListTests(unittest.TestCase):
    def test_list_passes(self) -> None:
        client = _client(**{"/v1/vouchers/load": [{"ok": True, "code": "0000000001"}, _GONE]})
        status, detail = _run("ap_refund_list", client, _sql())
        self.assertEqual(status, "PASS", detail)
        self.assertEqual([route.rsplit("/", 1)[1] for route, _f in client.calls], ["list", "list", "load", "load"])
        self.assertEqual(client.calls[1][1]["filter"], {"code": "0000000001"})

    def test_wrong_rows_fail(self) -> None:
        rows = {"ok": True, "items": [{"id": 7, "vouch_type": "49", "ledger": "AP"}], "next": None}
        client = _client(**{"/v1/vouchers/list": [rows]})
        status, detail = _run("ap_refund_list", client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("AP48", detail)

    def test_no_data_is_na(self) -> None:
        client = _client()
        status, detail = _run("ar_refund_list", client, _Sql([("ORDER BY h.iID DESC", [""])]))
        self.assertEqual(status, "N/A", detail)
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
