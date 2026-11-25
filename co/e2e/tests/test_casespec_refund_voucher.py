"""退款单制单（refund_voucher）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import refund_voucher as rv
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.refund_voucher_sql import gl_sql, left_sql, marks_sql, refund_voucher_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("refund-voucher-cycle", "ar-refund-voucher-cycle")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_PZ = {"ap_refund": "AP0000000000042", "ar_refund": "AR0000000000043"}
# 往来红字、结算正数：供应商退款在借方，客户退款在贷方。
_LINES = {
    "ap_refund": [{"entry": 1, "account": "220201", "debit": -100.0, "credit": 0},
                  {"entry": 2, "account": "100201", "debit": 100.0, "credit": 0}],
    "ar_refund": [{"entry": 1, "account": "112201", "debit": 0, "credit": -100.0},
                  {"entry": 2, "account": "100201", "debit": 0, "credit": 100.0}],
}
_GL = {"ap_refund": "2|-100.00|0.00|100.00|0.00|0|0|1", "ar_refund": "2|0.00|-100.00|0.00|100.00|0|0|1"}
_SIGN = {"ap_refund": "收", "ar_refund": "付"}


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


def _client(kind: str, lines: list | None = None) -> _Replay:
    made = {"ok": True, "pz_id": _PZ[kind], "voucher": {"sign": _SIGN[kind]}, "lines": lines or _LINES[kind]}
    return _Replay({
        "/v1/vouchers/create": [{"ok": True, "id": 7}],
        "/v1/vouchers/verify": [{"ok": True, "state": {"verified": True}}, {"ok": True, "state": {"verified": False}}],
        "/v1/arap/voucher": [made, U8CoError(409, "state_mismatch", "凭证已生成，不能重复制单。")],
        "/v1/arap/voucher/delete": [{"ok": True, "deleted": True}],
        "/v1/vouchers/delete": [{"ok": True, "deleted": True}],
    })


def _sql(kind: str, gl: str = "") -> _Sql:
    return _Sql([
        ("b.cVouchID", _rows("0000000001")),
        ("GL_accvouch AS g", _rows(gl or _GL[kind])),
        ("h.cPzID", _rows("1|2|2")),
        ("GL_accvouch WHERE coutno_id", _rows("0")),
        ("Ap_CloseBills WHERE iID", _rows("0")),
    ])


def _run(kind: str, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return rv.REFUND_VOUCHER_OPS[f"{kind}_voucher"](_env(client), _SPEC, _STEP)


def _order(client: _Replay) -> list[str]:
    return [route.split("/v1/", 1)[1] + (":" + fields["action"] if "action" in fields else "") for route, fields in client.calls]


class RegistryTests(unittest.TestCase):
    def test_registered_with_ops_and_tags(self) -> None:
        cases = rv.refund_voucher_cases()
        self.assertEqual(tuple(spec.name for spec in cases), _NAMES)
        for spec in cases:
            self.assertEqual(known_names().count(spec.name), 1)
            self.assertTrue(spec.requires_login and spec.routes)
            self.assertTrue(ops_run.has_op(spec.steps[-1].sql_name))
        self.assertIn("need:ap_master", auto_tags(cases[0]))
        self.assertIn("new:gl:收", auto_tags(cases[0]))
        self.assertIn("new:gl:付", auto_tags(cases[1]))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in refund_voucher_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
        gl = gl_sql("ap_refund", "AP0000000000001", "0000000001", ("220201", "100201"), "S0001")
        self.assertIn("coutbillsign, N'') = N'48'", gl)
        self.assertIn("g.csup_id", gl)
        self.assertIn("g.ccus_id", gl_sql("ar_refund", "AR0000000000001", "0000000001", ("112201", "100201"), "C0001"))
        self.assertIn("dbo.Ar_Detail", marks_sql("ar_refund", "7", "0000000001", "AR0000000000001"))
        bad = (
            lambda: left_sql("XX1"),
            lambda: gl_sql("ap_payment", "AP1", "1", ("1", "2"), "S"),
            lambda: gl_sql("ap_refund", "AP1", "1'--", ("1", "2"), "S"),
            lambda: marks_sql("ap_refund", "0", "1", "AP1"),
        )
        for call in bad:
            with self.assertRaises(ValueError):
                call()


class CycleTests(unittest.TestCase):
    def test_ap_cycle_passes_and_cleans_up_in_order(self) -> None:
        client = _client("ap_refund")
        status, detail = _run("ap_refund", client, _sql("ap_refund"))
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_order(client), [
            "vouchers/create", "vouchers/verify:verify", "arap/voucher", "arap/voucher", "arap/voucher/delete",
            "vouchers/verify:unverify", "vouchers/delete",
        ])
        self.assertEqual(client.calls[2][1]["type"], "ap_refund")
        self.assertEqual(client.calls[2][1]["flag"], "AP")
        self.assertEqual(client.calls[4][1]["pz_id"], _PZ["ap_refund"])

    def test_ar_cycle_passes(self) -> None:
        client = _client("ar_refund")
        status, detail = _run("ar_refund", client, _sql("ar_refund"))
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[2][1]["flag"], "AR")

    def test_reversed_sides_fail_after_cleanup(self) -> None:
        # 往来、结算分在借贷两方（不是 U8 的红字同方向）：FAIL，凭证照样取消、单据照样弃审删除。
        lines = [{"entry": 1, "account": "100201", "debit": 100.0, "credit": 0},
                 {"entry": 2, "account": "220201", "debit": 0, "credit": 100.0}]
        client = _client("ap_refund", lines)
        status, detail = _run("ap_refund", client, _sql("ap_refund"))
        self.assertEqual(status, "FAIL")
        self.assertIn("分录", detail)
        self.assertEqual(_order(client)[-3:], ["arap/voucher/delete", "vouchers/verify:unverify", "vouchers/delete"])

    def test_gl_mismatch_fails(self) -> None:
        client = _client("ap_refund")
        status, detail = _run("ap_refund", client, _sql("ap_refund", "2|100.00|0.00|0.00|100.00|0|0|1"))
        self.assertEqual(status, "FAIL")
        self.assertIn("往来借", detail)
        self.assertEqual(_order(client)[-1], "vouchers/delete")


if __name__ == "__main__":
    unittest.main()
