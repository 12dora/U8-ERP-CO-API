"""销售出库按行部分生单（sale_out_trim）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import sale_out_trim as m
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.sale_out_trim_sql import clear_pos_sql, sale_out_trim_samples, set_pos_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("sale-out-trim-multi", "sale-out-trim-compensate")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_GEN = "/v1/vouchers/generate"
_UNDONE = "已撤回生成的销售出库单：生成的销售出库单有货位记录，桥暂不支持按数量调整"


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


def _rows(*values: str) -> str:
    return "".join(f"ROW:{value}\n" for value in values)


def _run(op, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return op(_env(client), _SPEC, _STEP)


def _order(client: _Replay) -> list[tuple[str, str, str]]:
    return [(route.rsplit("/", 1)[1], fields["type"], fields.get("action", "")) for route, fields in client.calls]


class RegistryTests(unittest.TestCase):
    def test_registered_with_op_and_tags(self) -> None:
        names = known_names()
        cases = m.sale_out_trim_cases()
        self.assertEqual(tuple(spec.name for spec in cases), _NAMES)
        for spec in cases:
            with self.subTest(case=spec.name):
                self.assertEqual(names.count(spec.name), 1)
                self.assertTrue(spec.requires_login and spec.routes)
                self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
                self.assertIn("stock", auto_tags(spec))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in sale_out_trim_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("LTRIM(RTRIM(ISNULL(cPosition, N''))) = N''", set_pos_sql("7", "8", "A01"))
        self.assertIn("WHERE DLID = 7 AND iDLsID = 8", clear_pos_sql("7", "8"))
        for call in (lambda: set_pos_sql("7", "8", "A'1"), lambda: set_pos_sql("0", "8", "A1"), lambda: clear_pos_sql("7", "x")):
            with self.assertRaises(ValueError):
                call()


# ---- sale-out-trim-multi ----

_DISP = _rows("21|11|W1|2.0000|0.0000", "22|12|W1|1.0000|0.0000", "23|13|W2|1.0000|0.0000")


def _multi_client(gen: object) -> _Replay:
    return _Replay({
        "/v1/vouchers/create": [_ok(id=1)],
        "/v1/vouchers/verify": [_ok(), _ok(), _ok(), _ok()],
        _GEN: [_ok(id=2), gen],
        "/v1/vouchers/delete": [_ok(deleted=True), _ok(deleted=True), _ok(deleted=True), _ok(deleted=True)],
    })


def _multi_sql(after_gen: str, docs: list[str], doc_rows: str, after_del: str) -> _Sql:
    return _Sql([
        ("AS w2", ["ROW:C1|ST|D1|P1|I1|W1|W2\n"]),
        ("SO_SODetails AS d WHERE d.ID", [_rows("11", "12", "13")]),
        ("DispatchLists AS x WHERE x.DLID", [_DISP, after_gen, after_del]),
        ("SELECT DISTINCT b.ID", docs),
        ("rdrecords32 AS b WHERE b.ID = 5", [doc_rows]),
    ])


_AFTER_GEN = _rows("21|11|W1|2.0000|1.0000", "22|12|W1|1.0000|0.0000", "23|13|W2|1.0000|0.0000")
_AFTER_DEL = _DISP


class MultiTests(unittest.TestCase):
    def test_kept_one_doc_and_back_to_zero(self) -> None:
        client = _multi_client(_ok(id=5))
        sql = _multi_sql(_AFTER_GEN, [_rows("5"), ""], _rows("21|1.0000|W1|0"), _AFTER_DEL)
        status, detail = _run(m.sale_out_trim_multi, client, sql)
        self.assertEqual(status, "PASS", detail)
        order = client.calls[1][1]
        self.assertEqual(order["type"], "sale_order")
        self.assertEqual([line["iQuantity"] for line in client.calls[0][1]["lines"]], [2, 1, 1])
        disp = client.calls[2][1]["lines"]
        self.assertEqual([(line["source_line_id"], line["quantity"], line["cWhCode"]) for line in disp],
                         [(11, 2, "W1"), (12, 1, "W1"), (13, 1, "W2")])
        self.assertEqual(client.calls[4][1]["lines"], [{"source_line_id": 21, "quantity": 1}])
        self.assertEqual(_order(client)[5:], [
            ("delete", "sale_out", ""), ("verify", "dispatch", "unverify"), ("delete", "dispatch", ""),
            ("verify", "sale_order", "unverify"), ("delete", "sale_order", ""),
        ])

    def test_two_docs_left_fail_and_both_dropped(self) -> None:
        client = _multi_client(_ok(id=5, ids=[5, 6]))
        sql = _multi_sql(_AFTER_GEN, [_rows("5", "6")], _rows("21|1.0000|W1|0"), _AFTER_DEL)
        status, detail = _run(m.sale_out_trim_multi, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("应只剩出库单 5", detail)
        dropped = [fields["id"] for route, fields in client.calls if route.endswith("delete") and fields["type"] == "sale_out"]
        self.assertEqual(sorted(dropped), [5, 6])

    def test_refused_generate_drops_leftovers(self) -> None:
        client = _multi_client(U8CoError(409, "u8_rejected", "已撤回生成的销售出库单：x"))
        sql = _multi_sql(_AFTER_GEN, [""], "", _AFTER_DEL)
        status, detail = _run(m.sale_out_trim_multi, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("应 200", detail)
        self.assertNotIn(("delete", "sale_out", ""), _order(client))

    def test_wrong_cumulative_fails(self) -> None:
        wrong = _rows("21|11|W1|2.0000|2.0000", "22|12|W1|1.0000|0.0000", "23|13|W2|1.0000|0.0000")
        client = _multi_client(_ok(id=5))
        status, detail = _run(m.sale_out_trim_multi, client, _multi_sql(wrong, [_rows("5")], _rows("21|1.0000|W1|0"), _DISP))
        self.assertEqual(status, "FAIL")
        self.assertIn("1 / 0 / 0", detail)


# ---- sale-out-trim-compensate ----


def _comp_client(gen: object) -> _Replay:
    return _Replay({
        "/v1/vouchers/create": [_ok(id=1)],
        "/v1/vouchers/verify": [_ok(), _ok(), _ok(), _ok()],
        _GEN: [_ok(id=2), gen],
        "/v1/vouchers/delete": [_ok(deleted=True), _ok(deleted=True), _ok(deleted=True)],
    })


def _comp_sql(docs: str, outq: str = "", sums: tuple[str, str] = ("5.0000", "9.0000"), doc_rows: str = "") -> _Sql:
    return _Sql([
        ("p2.cPosCode", ["ROW:C1|ST|D1|P1|I1|W1|A01|5.0000|\n"]),
        ("COL_LENGTH", [_rows("1")]),
        ("InvPositionSum AS p", [_rows("5.0000"), _rows(sums[0])]),
        ("CurrentStock AS c", [_rows("9.0000"), _rows(sums[1])]),
        ("SO_SODetails", [_rows("11")]),
        ("MIN(x.iDLsID)", [_rows("21")]),
        ("SET cPosition = N'A01'", [_rows("1")]),
        ("SELECT DISTINCT b.ID", [docs]),
        ("SET cPosition = NULL", [_rows("1")]),
        ("DispatchLists AS x WHERE x.DLID", [outq or _rows("21|11|W1|2.0000|0.0000")]),
        ("rdrecords32 AS b WHERE b.ID = 5", [doc_rows]),
    ])


class CompensateTests(unittest.TestCase):
    def test_undone_409_passes(self) -> None:
        client = _comp_client(U8CoError(409, "u8_rejected", _UNDONE))
        status, detail = _run(m.sale_out_trim_compensate, client, _comp_sql(""))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("已撤回", detail)
        self.assertEqual(client.calls[4][1]["lines"], [{"source_line_id": 21, "quantity": 1}])
        self.assertNotIn(("delete", "sale_out", ""), _order(client))

    def test_undone_but_stock_moved_fails(self) -> None:
        client = _comp_client(U8CoError(409, "u8_rejected", _UNDONE))
        status, detail = _run(m.sale_out_trim_compensate, client, _comp_sql("", sums=("4.0000", "9.0000")))
        self.assertEqual(status, "FAIL")
        self.assertIn("货位存量", detail)

    def test_outcome_unknown_fails_and_drops(self) -> None:
        client = _comp_client(U8CoError(504, "outcome_unknown", "撤回失败"))
        status, detail = _run(m.sale_out_trim_compensate, client, _comp_sql(_rows("5")))
        self.assertEqual(status, "FAIL")
        self.assertIn("补偿也失败", detail)
        self.assertIn(("delete", "sale_out", ""), _order(client))

    def test_step2_passed_is_na_and_cleaned(self) -> None:
        client = _comp_client(_ok(id=5))
        status, detail = _run(m.sale_out_trim_compensate, client, _comp_sql(_rows("5"), doc_rows=_rows("21|1.0000|W1|0")))
        self.assertEqual(status, "N/A", detail)
        self.assertIn("补偿未覆盖", detail)
        self.assertEqual(_order(client)[5], ("delete", "sale_out", ""))

    def test_no_position_column_is_na(self) -> None:
        sql = _Sql([("p2.cPosCode", ["ROW:C1|ST|D1|P1|I1|W1|A01|5.0000|\n"]), ("COL_LENGTH", [_rows("0")])])
        client = _comp_client(_ok(id=5))
        status, detail = _run(m.sale_out_trim_compensate, client, sql)
        self.assertEqual(status, "N/A")
        self.assertIn("cPosition", detail)
        self.assertEqual(client.calls, [])

    def test_small_bin_is_na(self) -> None:
        sql = _Sql([("p2.cPosCode", ["ROW:C1|ST|D1|P1|I1|W1|A01|1.0000|A02\n"])])
        status, detail = _run(m.sale_out_trim_compensate, _comp_client(_ok(id=5)), sql)
        self.assertEqual(status, "N/A")
        self.assertIn("不够", detail)


if __name__ == "__main__":
    unittest.main()
