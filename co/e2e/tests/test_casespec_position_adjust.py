"""货位调整单往返（position_adjust）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import position_adjust as pa
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.position_adjust_sql import (
    bin_sums_sql,
    edit_sql,
    gone_sql,
    head_sql,
    ledger_sql,
    pick_sql,
    position_adjust_samples,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "position-adjust-cycle"
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_SAME = U8CoError(400, "bad_request", "调出货位和调入货位不能相同", field="lines.0.caposcode")
_VERIFIED = U8CoError(409, "state_mismatch", "单据已审核")


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

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-03", bag={})


def _rows(*values: str) -> list[str]:
    return [f"ROW:{value}\n" for value in values]


def _client(**over: list) -> _Replay:
    answers: dict[str, list] = {
        "/v1/vouchers/create": [_SAME, {"ok": True, "id": 7}],
        # 改备注、删旧行加新行、数量改 2、数量改回 1、调出调入相同（400）、已审核（409）。
        "/v1/vouchers/update": [{"ok": True}] * 4 + [_SAME, _VERIFIED],
        "/v1/vouchers/verify": [
            {"ok": True, "ledger_rows": 2, "bin_moves": [{}, {}], "verified_by": "张三"},
            {"ok": True},
        ],
        "/v1/vouchers/load": [{"ok": True, "positions": [{}, {}]}],
        "/v1/vouchers/delete": [{"ok": True, "deleted": True}],
    }
    answers.update(over)
    return _Replay(answers)


def _edit_rows(*qty: str) -> list[str]:
    """修改前、改备注后、换行后、（数量改 2、改回 1）的（表头备注|行数|首行|行备注|行单号|行号|数量|件数|表头单号）。"""
    first = "CO桥回归|1|55||PA0001|1|1.0000||PA0001"
    memo = "CO桥回归改|1|55|CO桥回归改|PA0001|1|1.0000||PA0001"
    swap = "CO桥回归改|1|56||PA0001|2|1.0000||PA0001"
    return _rows(first, memo, swap, *(f"CO桥回归改|1|56||PA0001|2|{one}||PA0001" for one in qty))


def _sql(
    ledger: tuple[str, ...] | None = None,
    heads: tuple[str, ...] = ("PA0001||1",),
    stock: int = 5,
) -> _Sql:
    """stock 是调出货位原结存；>= 2 时多一次数量修改（改 2、改回 1 各一次回读、结存和台账核对）。
    核对顺序：保存后、（改数量后 ×2）、修改后、审核后、弃审后、删除后。"""
    many = stock >= 2
    base, moved = f"{stock}.0000|2.0000", f"{stock - 1}.0000|3.0000"
    bins = [base] * (4 if many else 2) + [moved, base, base]
    ledger = ledger or ("0|0|0",) * (4 if many else 2) + ("2|1|1", "0|0|0", "0|0|0")
    return _Sql([
        ("CROSS APPLY", _rows(f"W1|I1|P1|P2|{stock}.0000|2.0000")),
        ("chandler", _rows(*heads)),
        ("b.cbMemo", _edit_rows("2.0000", "1.0000") if stock >= 2 else _edit_rows()),
        ("s.cPosCode = N'P1'", _rows(*bins)),
        ("p.cvouchtype = N'19'", _rows(*ledger)),
        ("COUNT(*)) AS v", _rows("0")),
    ])


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return pa.position_adjust_cycle(_env(client), _SPEC, _STEP)


def _order(client: _Replay) -> list[tuple[str, str]]:
    return [(route.rsplit("/", 1)[1], fields.get("action", "")) for route, fields in client.calls]


class RegistryTests(unittest.TestCase):
    def test_registered_with_stock_tag(self) -> None:
        cases = pa.position_adjust_cases()
        self.assertEqual([spec.name for spec in cases], [_NAME])
        spec = cases[0]
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(spec.requires_login and spec.routes)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertIn("stock", auto_tags(spec))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in position_adjust_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("p.bPosEnd = 1", pick_sql())
        self.assertIn("w.bWhPos = 1", pick_sql())
        self.assertIn("p.RdID = 7 AND p.cvouchtype = N'19'", ledger_sql("7"))
        self.assertIn("h.Id = 7", head_sql("7"))
        self.assertIn("h.Id = 7", gone_sql("7"))
        self.assertIn("h.Id = 7", edit_sql("7"))
        bad = (lambda: bin_sums_sql("W'1", "I1", "P1", "P2"), lambda: ledger_sql("0"), lambda: gone_sql("x"),
               lambda: edit_sql("0"))
        for call in bad:
            with self.assertRaises(ValueError):
                call()


class CycleTests(unittest.TestCase):
    def test_cycle_passes_and_cleans_up_in_order(self) -> None:
        client = _client()
        status, detail = _run(client, _sql())
        self.assertEqual(status, "PASS", detail)
        self.assertIn("PA0001", detail)
        self.assertEqual(_order(client), [
            ("create", ""), ("create", ""), ("update", ""), ("update", ""), ("update", ""), ("update", ""), ("update", ""),
            ("verify", "verify"), ("load", ""), ("update", ""), ("verify", "unverify"), ("delete", ""),
        ])
        same = client.calls[0][1]["lines"][0]
        self.assertEqual((same["cBPosCode"], same["cAPosCode"]), ("P1", "P1"))
        line = client.calls[1][1]["lines"][0]
        self.assertEqual((line["cBPosCode"], line["cAPosCode"], line["iQuantity"]), ("P1", "P2", 1))
        self.assertEqual(client.calls[1][1]["head"]["cWhCode"], "W1")
        memo, swap, two, one, same = (client.calls[i][1] for i in (2, 3, 4, 5, 6))
        self.assertEqual((memo["id"], memo["head"], memo["lines"][0]["line_id"]), (7, {"cMemo": "CO桥回归改"}, 55))
        self.assertEqual([item["op"] for item in swap["lines"]], ["delete", "add"])
        self.assertEqual((swap["lines"][0]["line_id"], swap["lines"][1]["cAPosCode"]), (55, "P2"))
        self.assertEqual(two["lines"], [{"op": "update", "line_id": 56, "iQuantity": 2}])
        self.assertEqual(one["lines"], [{"op": "update", "line_id": 56, "iQuantity": 1}])
        self.assertEqual((same["lines"][0]["line_id"], same["lines"][0]["caposcode"]), (56, "P1"))
        self.assertEqual(client.calls[9][1]["id"], 7)

    def test_quantity_edit_skipped_when_stock_below_two(self) -> None:
        client = _client(**{"/v1/vouchers/update": [{"ok": True}, {"ok": True}, _SAME, _VERIFIED]})
        status, detail = _run(client, _sql(stock=1))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("数量修改未测", detail)
        self.assertEqual([order for order in _order(client) if order[0] == "update"], [("update", "")] * 4)
        # 没有对已有行的数量修改（换行请求里新增行的 iQuantity 不算）。
        edits = [item for _route, fields in client.calls[2:5] for item in fields.get("lines", []) if item["op"] == "update"]
        self.assertTrue(edits)
        self.assertTrue(all("iQuantity" not in item for item in edits), edits)

    def test_new_row_without_code_fails(self) -> None:
        client = _client()
        rows = _sql()
        rows.answers[2] = ("b.cbMemo", _rows("CO桥回归|1|55|", "CO桥回归改|1|55|CO桥回归改", "CO桥回归改|1|56||||"))
        status, detail = _run(client, rows)
        self.assertEqual(status, "FAIL")
        self.assertIn("行号", detail)

    def test_test_account_only_is_fail(self) -> None:
        # 货位调整单写入是第一级：旧桥的 403 test_account_only 不再记 N/A。
        refused = U8CoError(403, "test_account_only", "货位调整单的新增、审核、弃审、删除只对配置为测试账套的账套开放")
        client = _client(**{"/v1/vouchers/create": [refused]})
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL", detail)
        self.assertIn("test_account_only", detail)
        self.assertEqual(len(client.calls), 1)

    def test_same_bin_saved_fails_and_deletes(self) -> None:
        client = _client(**{"/v1/vouchers/create": [{"ok": True, "id": 9}]})
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("被保存", detail)
        route, fields = client.calls[-1]
        self.assertEqual((route, fields["type"], fields["id"]), ("/v1/vouchers/delete", "position_adjust", 9))

    def test_ledger_mismatch_fails_after_cleanup(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(("0|0|0",) * 5, ("PA0001||1", "PA0001|张三|1")))
        self.assertEqual(status, "FAIL")
        self.assertIn("货位台账", detail)
        self.assertEqual(_order(client)[-2:], [("verify", "unverify"), ("delete", "")])

    def test_verify_error_after_commit_still_unverifies(self) -> None:
        late = U8CoError(504, "outcome_unknown", "审核结果未知")
        client = _client(**{"/v1/vouchers/verify": [late, {"ok": True}]})
        status, detail = _run(client, _sql(heads=("PA0001||1", "PA0001|张三|1")))
        self.assertEqual(status, "FAIL")
        self.assertIn("审核货位调整单", detail)
        self.assertEqual(_order(client)[-3:], [("verify", "verify"), ("verify", "unverify"), ("delete", "")])

    def test_verify_refused_skips_unverify(self) -> None:
        refused = U8CoError(409, "u8_rejected", "U8 拒绝")
        client = _client(**{"/v1/vouchers/verify": [refused]})
        status, _detail = _run(client, _sql(heads=("PA0001||1", "PA0001||1")))
        self.assertEqual(status, "FAIL")
        self.assertEqual(_order(client)[-2:], [("verify", "verify"), ("delete", "")])

    def test_failed_settle_is_retried_at_cleanup(self) -> None:
        stuck = U8CoError(409, "state_mismatch", "弃审失败")
        client = _client(**{"/v1/vouchers/verify": [
            {"ok": True, "ledger_rows": 2, "bin_moves": [{}, {}], "verified_by": "张三"}, stuck, {"ok": True},
        ]})
        status, detail = _run(client, _sql(heads=("PA0001||1", "PA0001|张三|1")))
        self.assertEqual(status, "FAIL", detail)
        self.assertEqual(_order(client)[-3:], [("verify", "unverify"), ("verify", "unverify"), ("delete", "")])

    def test_nothing_to_pick_is_na(self) -> None:
        client = _client()
        status, detail = _run(client, _Sql([("CROSS APPLY", [""])]))
        self.assertEqual(status, "N/A", detail)
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
