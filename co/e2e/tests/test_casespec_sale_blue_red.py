"""红冲蓝字销售发票（sale_blue_red）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import sale_blue_red as m
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.sale_blue_red_sql import blue_line_sql, blue_red_samples, inv_gone_sql, red_shape_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_PICK = "ROW:C1|ST|D1|P1|I1|W1\n"


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


def _no() -> U8CoError:
    return U8CoError(409, "state_mismatch", "超过可生单数量")


_HELD = "蓝字发票已被红冲，请先删除红字发票"


class _BlueReplay(_Replay):
    """红字发票 4 还在时，蓝字发票 3 的弃复、删除按桥的规则 409；日期早于蓝字的红冲 400。都不占用排好的回答。"""

    def __init__(self, answers: dict[str, list], held: str = _HELD) -> None:
        super().__init__(answers)
        self.held = held
        self.red_live = False

    def call(self, route: str, fields: dict) -> dict:
        refused = self._refused(route, fields)
        if refused is not None:
            self.calls.append((route, dict(fields)))
            raise refused
        got = super().call(route, fields)
        if route == _GEN and got.get("id") == 4:
            self.red_live = True
        if route == _DELETE and fields.get("id") == 4:
            self.red_live = False
        return got

    def _refused(self, route: str, fields: dict) -> U8CoError | None:
        if route == _GEN and (fields.get("head") or {}).get("ddate") == "2000-01-01":
            return U8CoError(400, "bad_request", "红字发票日期不能早于蓝字发票", "head.ddate")
        unverify = route == _VERIFY and fields.get("action") == "unverify"
        if self.red_live and fields.get("id") == 3 and (unverify or route == _DELETE):
            return U8CoError(409, "state_mismatch", self.held)
        return None


def _client(blue_delete: object = None, held: str = _HELD) -> _Replay:
    return _BlueReplay({
        "/v1/vouchers/create": [_ok(id=1)],
        _VERIFY: [_ok() for _ in range(8)],
        _GEN: [_ok(id=2), _ok(id=3), _no(), _no(), _ok(id=4), _no()],
        _DELETE: [_ok(deleted=True), blue_delete or _ok(deleted=True), _ok(deleted=True), _ok(deleted=True)],
    }, held)


def _sql(shape: str = "1|26|-1.0000|1|1", gone: str = "0") -> _Sql:
    return _Sql([
        ("AS v\nFROM dbo.Warehouse AS w", [_PICK]),
        ("MIN(d.iSOsID)", ["ROW:11\n"]),
        ("MIN(x.iDLsID)", ["ROW:21\n"]),
        ("CONVERT(varchar(20), b.AutoID) + N'|'", ["ROW:31|26\n"]),
        ("COUNT(b.AutoID)", [f"ROW:{shape}\n"]),
        ("SELECT COUNT(*) FROM dbo.SaleBillVouch AS h", [f"ROW:{gone}\n"]),
    ])


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return m.sale_red_from_blue(_env(client), _SPEC, _STEP)


def _order(client: _Replay) -> list[tuple[str, str, str]]:
    return [(route.rsplit("/", 1)[1], fields["type"], fields.get("action", "")) for route, fields in client.calls]


_TAIL = [
    ("verify", "sale_invoice", "verify"), ("verify", "sale_invoice", "unverify"), ("delete", "sale_invoice", ""),
    ("verify", "sale_invoice", "unverify"), ("delete", "sale_invoice", ""),
    ("verify", "dispatch", "unverify"), ("delete", "dispatch", ""), ("verify", "sale_order", "unverify"),
    ("delete", "sale_order", ""),
]


class RegistryTests(unittest.TestCase):
    def test_registered_once_with_op_and_tags(self) -> None:
        cases = m.blue_red_cases()
        self.assertEqual([spec.name for spec in cases], ["sale-red-from-blue"])
        spec = cases[0]
        self.assertEqual(known_names().count(spec.name), 1)
        self.assertTrue(spec.requires_login)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertIn("stock", auto_tags(spec))
        self.assertIn("new:sale_invoice", spec.tags)

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in blue_red_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("SELECT TOP (1) N'ROW:'", blue_line_sql("7"))
        self.assertIn("b.iSBVID = 8", red_shape_sql("7", "8"))
        for call in (lambda: blue_line_sql("0"), lambda: red_shape_sql("7", "x"), lambda: inv_gone_sql("7 OR 1=1")):
            with self.assertRaises(ValueError):
                call()


class FlowTests(unittest.TestCase):
    def test_partial_red_then_cleanup(self) -> None:
        client = _client()
        status, detail = _run(client, _sql())
        self.assertEqual(status, "PASS", detail)
        gens = [fields for route, fields in client.calls if route == _GEN]
        self.assertEqual(gens[1]["lines"], [{"source_line_id": 21, "quantity": 3}])
        reds = [(g["id"], g["source_type"], g["lines"][0]["source_line_id"], g["lines"][0]["quantity"]) for g in gens[2:6]]
        self.assertEqual(reds, [(3, "sale_invoice", 31, 1), (3, "sale_invoice", 31, 4), (3, "sale_invoice", 31, 1),
                                (3, "sale_invoice", 31, 3)])
        self.assertEqual(_order(client)[-9:], _TAIL)
        self.assertIn("已清理", detail)
        early = [g for g in gens if (g.get("head") or {}).get("ddate") == "2000-01-01"]
        self.assertEqual(len(early), 1)

    def test_held_refusal_must_name_red_link(self) -> None:
        client = _client(held="单据已复核")
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("蓝字发票已被红冲", detail)
        self.assertEqual(_order(client)[-3:], _TAIL[-3:])

    def test_blue_delete_refused_leaves_chain(self) -> None:
        client = _client(U8CoError(409, "state_mismatch", "发票复核后又弃复"))
        status, detail = _run(client, _sql())
        self.assertEqual(status, "PASS", detail)
        self.assertIn("快照还原", detail)
        self.assertEqual(_order(client)[-1], ("delete", "sale_invoice", ""))

    def test_wrong_shape_fails_and_cleans_up(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(shape="1|26|-1.0000|1|0"))
        self.assertEqual(status, "FAIL")
        self.assertIn("关联蓝字行", detail)
        self.assertEqual(_order(client)[-3:], _TAIL[-3:])

    def test_missing_pick_is_na(self) -> None:
        client = _client()
        status, detail = _run(client, _Sql([("AS v\nFROM dbo.Warehouse AS w", [""])]))
        self.assertEqual(status, "N/A", detail)
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
