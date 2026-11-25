"""发货单修改新增行（dispatch_add）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import dispatch_add as m
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dispatch_add_sql import added_sql, dispatch_add_samples, fh_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("dispatch-update-add-line", "dispatch-update-add-over-remain", "dispatch-update-add-other-so")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_UPDATE = "/v1/vouchers/update"
_PICK = "ROW:C1|ST|D1|P1|I1|W1\n"
# 键按顺序找第一个还有回答的：订单行累计发货、订单行列表、发货单上某订单行、发货单各行。
_FH = "d.iFHQuantity"
_SO_LINES = "CONVERT(varchar(20), d.iSOsID) AS v"
_ADDED = "AND x.iSOsID"
_DISP = "x.fOutQuantity"


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


def _rows(*values: str) -> str:
    return "".join(f"ROW:{value}\n" for value in values)


def _run(op, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return op(_env(client), _SPEC, _STEP)


def _order(client: _Replay) -> list[tuple[str, str, str]]:
    return [(route.rsplit("/", 1)[1], fields["type"], fields.get("action", "")) for route, fields in client.calls]


def _client(update: object, orders: int = 1, load: object = None) -> _Replay:
    return _Replay({
        "/v1/vouchers/create": [_ok(id=1 + 9 * i) for i in range(orders)],
        "/v1/vouchers/verify": [_ok() for _ in range(2 * orders)],
        "/v1/vouchers/generate": [_ok(id=2)],
        _UPDATE: [update],
        "/v1/vouchers/load": [load or _ok(lines=[{}, {}])],
        "/v1/vouchers/delete": [_ok(deleted=True) for _ in range(orders + 1)],
    })


_CLEANUP = [("delete", "dispatch", ""), ("verify", "sale_order", "unverify"), ("delete", "sale_order", "")]


class RegistryTests(unittest.TestCase):
    def test_registered_once_with_ops_and_tags(self) -> None:
        names = known_names()
        cases = m.dispatch_add_cases()
        self.assertEqual(tuple(spec.name for spec in cases), _NAMES)
        for spec in cases:
            with self.subTest(case=spec.name):
                self.assertEqual(names.count(spec.name), 1)
                self.assertTrue(spec.requires_login)
                self.assertIn(_UPDATE, spec.routes)
                self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
                self.assertIn("stock", auto_tags(spec))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in dispatch_add_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("WHERE x.DLID = 7 AND x.iSOsID = 8", added_sql("7", "8"))
        for call in (lambda: fh_sql("0"), lambda: added_sql("7", "x"), lambda: added_sql("7 OR 1=1", "8")):
            with self.assertRaises(ValueError):
                call()


# ---- dispatch-update-add-line ----


def _add_sql(after_add: str = "11|2.0000|12|1.0000", added: str = "1.0000|W1|1", disp: int = 2) -> _Sql:
    first, second = after_add.split("|")[:2], after_add.split("|")[2:]
    rows = [_rows("21|11|W1|2.0000|0.0000"), _rows("21|11|W1|2.0000|0.0000", "22|12|W1|1.0000|0.0000")][disp - 1]
    return _Sql([
        ("AS v\nFROM dbo.Warehouse AS w", [_PICK]),
        (_FH, [
            _rows("11|2.0000", "12|0.0000"),
            _rows("|".join(first), "|".join(second)),
            _rows("11|0.0000", "12|0.0000"),
        ]),
        (_SO_LINES, [_rows("11", "12")]),
        (_ADDED, [_rows(added)]),
        (_DISP, [rows]),
    ])


class AddLineTests(unittest.TestCase):
    def test_added_line_counts_and_rolls_back(self) -> None:
        client = _client(_ok(lines=2))
        status, detail = _run(m.dispatch_add_line, client, _add_sql())
        self.assertEqual(status, "PASS", detail)
        order = client.calls[0][1]
        self.assertEqual([line["iQuantity"] for line in order["lines"]], [2, 2])
        self.assertEqual(client.calls[2][1]["lines"], [{"source_line_id": 11, "quantity": 2, "cWhCode": "W1"}])
        upd = client.calls[3][1]
        self.assertEqual((upd["type"], upd["id"]), ("dispatch", 2))
        self.assertEqual(upd["lines"], [{"op": "add", "source_line_id": 12, "iQuantity": 1, "cWhCode": "W1", "cMemo": "CO桥回归"}])
        self.assertEqual(_order(client)[-3:], _CLEANUP)

    def test_refused_add_fails_and_cleans_up(self) -> None:
        client = _client(U8CoError(409, "u8_rejected", "U8 保存的明细与请求不一致"))
        status, detail = _run(m.dispatch_add_line, client, _add_sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("应 200", detail)
        self.assertEqual(_order(client)[-3:], _CLEANUP)

    def test_order_line_not_shipped_fails(self) -> None:
        client = _client(_ok(lines=2))
        status, detail = _run(m.dispatch_add_line, client, _add_sql(after_add="11|2.0000|12|0.0000"))
        self.assertEqual(status, "FAIL")
        self.assertIn("累计发货", detail)

    def test_wrong_amount_fails(self) -> None:
        client = _client(_ok(lines=2))
        status, detail = _run(m.dispatch_add_line, client, _add_sql(added="1.0000|W1|0"))
        self.assertEqual(status, "FAIL")
        self.assertIn("金额对上", detail)

    def test_missing_pick_is_na(self) -> None:
        client = _client(_ok(lines=2))
        status, detail = _run(m.dispatch_add_line, client, _Sql([("AS v\nFROM dbo.Warehouse AS w", [""])]))
        self.assertEqual(status, "N/A", detail)
        self.assertEqual(client.calls, [])


# ---- 拒绝 ----


def _refuse_sql(orders: int = 1) -> _Sql:
    fh = [_rows("11|2.0000", "12|0.0000")] * 2
    if orders == 2:
        fh = [_rows("11|2.0000", "12|0.0000"), _rows("13|0.0000"), _rows("11|2.0000", "12|0.0000"), _rows("13|0.0000")]
    return _Sql([
        ("AS v\nFROM dbo.Warehouse AS w", [_PICK]),
        (_FH, fh),
        (_SO_LINES, [_rows("11", "12"), _rows("13")]),
        (_DISP, [_rows("21|11|W1|2.0000|0.0000")]),
    ])


class RefuseTests(unittest.TestCase):
    def test_over_remain_409_passes(self) -> None:
        client = _client(U8CoError(409, "state_mismatch", "超过可生单数量"))
        status, detail = _run(m.dispatch_add_over, client, _refuse_sql())
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[3][1]["lines"][0]["iQuantity"], 3)
        self.assertEqual(_order(client)[-3:], _CLEANUP)

    def test_over_remain_saved_fails_and_drops_dispatch(self) -> None:
        client = _client(_ok(lines=2))
        status, detail = _run(m.dispatch_add_over, client, _refuse_sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("409 / 400", detail)
        self.assertEqual(_order(client)[-3:], _CLEANUP)

    def test_other_order_400_passes(self) -> None:
        client = _client(U8CoError(400, "bad_request", "只能新增本发货单来源销售订单的行"), orders=2)
        status, detail = _run(m.dispatch_add_other, client, _refuse_sql(orders=2))
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[-6][1]["lines"][0]["source_line_id"], 13)
        self.assertEqual(_order(client)[-5:], [
            ("verify", "sale_order", "unverify"), ("delete", "sale_order", ""),
        ] + _CLEANUP)

    def test_other_order_409_fails(self) -> None:
        client = _client(U8CoError(409, "state_mismatch", "超过可生单数量"), orders=2)
        status, detail = _run(m.dispatch_add_other, client, _refuse_sql(orders=2))
        self.assertEqual(status, "FAIL")
        self.assertIn("应 400", detail)


if __name__ == "__main__":
    unittest.main()
