"""B4 有货位记录的销售出库删除（binned_sale_out）的登记、分片、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import binned_sale_out as b4
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.binned_sale_out_sql import binned_sale_out_samples, out_head_sql, pos_sum_sql, stock_sum_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "binned-sale-out-delete"
_SPEC = CaseSpec(_NAME, "s")
_STEP = Step("custom")
_PICK = "ROW:C1|XS|D1|P1|I1|04|A01|10.0000|\n"


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
        raise AssertionError("没有准备回答：" + query[:80])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag={})


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _client() -> _Replay:
    return _Replay({
        "/v1/vouchers/create": [_ok(id=1)],
        "/v1/vouchers/verify": [_ok(), _ok(), _ok(), _ok()],
        "/v1/vouchers/generate": [_ok(id=2), _ok(id=3)],
        "/v1/vouchers/delete": [_ok(deleted=True), _ok(deleted=True), _ok(deleted=True)],
    })


def _sql(rows: str = "1", psum: tuple = ("10.0000", "9.0000", "10.0000"), head: str = "0") -> _Sql:
    return _Sql([
        ("InvPositionSum AS s", [_PICK]),
        ("InvPositionSum AS p", [f"ROW:{value}\n" for value in psum]),
        ("CurrentStock AS c WHERE c.cWhCode", ["ROW:20.0000\n", "ROW:20.0000\n"]),
        ("SO_SODetails", ["ROW:11\n"]),
        ("MIN(x.iDLsID)", ["ROW:21\n"]),
        ("InvPosition AS p WHERE p.RdID", [f"ROW:{rows}\n", "ROW:0\n"]),
        ("RdRecord32 AS h", [f"ROW:{head}\n"]),
    ])


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return b4.binned_sale_out(_env(client), _SPEC, _STEP)


class BinnedSaleOutTests(unittest.TestCase):
    def test_registered_and_conflicts_with_bin_cases(self) -> None:
        self.assertEqual(known_names().count(_NAME), 1)
        spec = b4.binned_sale_out_cases()[0]
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertIn("stock", auto_tags(spec))
        split = next(case for case in sp.stockplus_cases() if case.name == "sale-out-bin-split")
        self.assertTrue(conflicts(auto_tags(spec), auto_tags(split)))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in binned_sale_out_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
        self.assertIn("p.cPosCode = N'A01'", pos_sum_sql("01", "A01", "0001"))
        self.assertIn("dbo.CurrentStock", stock_sum_sql("01", "0001"))
        self.assertIn("h.ID = 100", out_head_sql("100"))
        for bad in (lambda: pos_sum_sql("01'", "A01", "0001"), lambda: out_head_sql("0")):
            with self.assertRaises(ValueError):
                bad()

    def test_chain_with_position_and_rollback(self) -> None:
        client = _client()
        status, detail = _run(client, _sql())
        self.assertEqual(status, "PASS", detail)
        out_lines = client.calls[4][1]["lines"]
        self.assertEqual(out_lines, [{"source_line_id": 21, "quantity": 1, "cposition": "A01"}])
        order = [(route.rsplit("/", 1)[1], fields["type"], fields.get("action", "")) for route, fields in client.calls[5:]]
        self.assertEqual(order, [
            ("delete", "sale_out", ""), ("verify", "dispatch", "unverify"), ("delete", "dispatch", ""),
            ("verify", "sale_order", "unverify"), ("delete", "sale_order", ""),
        ])

    def test_missing_position_record_fails_after_cleanup(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(rows="0"))
        self.assertEqual(status, "FAIL")
        self.assertIn("InvPosition", detail)
        self.assertEqual(len(client.answers["/v1/vouchers/delete"]), 0)

    def test_position_stock_not_back_fails(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(psum=("10.0000", "9.0000", "9.0000")))
        self.assertEqual(status, "FAIL")
        self.assertIn("回到", detail)

    def test_position_stock_unchanged_after_generate_fails(self) -> None:
        status, detail = _run(_client(), _sql(psum=("10.0000", "10.0000", "10.0000")))
        self.assertEqual(status, "FAIL")
        self.assertIn("生成后", detail)

    def test_nothing_to_pick_is_na(self) -> None:
        client = _client()
        status, _detail = _run(client, _Sql([("InvPositionSum AS s", [""])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
