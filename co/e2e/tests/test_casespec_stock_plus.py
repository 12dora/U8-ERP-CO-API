"""库存补全用例（stock_plus）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import stock_plus_sql as sq
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_NAMES = ("sale-out-batch-refuse", "sale-out-bin-split", "sale-out-batch-override", "purchase-in-fx", "purchase-in-red-nosrc")


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
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag={})


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _run(op, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return op(_env(client), _SPEC, _STEP)


class StockPlusCaseTests(unittest.TestCase):
    def test_cases_registered_with_ops_and_tags(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in sp.stockplus_cases()), _NAMES)
        for spec in sp.stockplus_cases():
            self.assertEqual(names.count(spec.name), 1, spec.name)
            self.assertTrue(spec.requires_login)
            self.assertTrue(spec.tags, spec.name)
            self.assertTrue(all(ops_run.has_op(step.sql_name) for step in spec.steps))

    def test_samples_pass_the_sql_guard(self) -> None:
        for name, query in sq.stockplus_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("ROW:", query, name)

    def test_unsafe_values_refused(self) -> None:
        for bad in ("", "a'b", "x" * 61, "01;drop"):
            with self.assertRaises(ValueError):
                sq.safe_lot(bad)
        with self.assertRaises(ValueError):
            sq.out_rows_sql("1 or 1=1", "2")

    def test_refuse_both_keys_before_generation(self) -> None:
        client = _Replay({"/v1/vouchers/generate": [
            U8CoError(400, "bad_request", "存货 A 未启用批次管理，不能填批号 cbatch"),
            U8CoError(400, "bad_request", "仓库 01 未启用货位管理，不能填货位 cposition"),
        ]})
        status, _detail = _run(sp.sop_refuse, client, _Sql([("DispatchList", ["ROW:10|20\n"])]))
        self.assertEqual(status, "PASS")
        sent = client.calls[0][1]["lines"][0]
        self.assertEqual((sent["source_line_id"], sent["cbatch"]), (20, "E2E-LOT"))

    def test_refuse_made_voucher_is_deleted(self) -> None:
        client = _Replay({"/v1/vouchers/generate": [_ok(id=77)], "/v1/vouchers/delete": [_ok(deleted=True)]})
        status, detail = _run(sp.sop_refuse, client, _Sql([("DispatchList", ["ROW:10|20\n"])]))
        self.assertEqual(status, "FAIL")
        self.assertIn("cbatch", detail)
        route, fields = client.calls[-1]
        self.assertEqual((route, fields["type"], fields["id"]), ("/v1/vouchers/delete", "sale_out", 77))

    def test_bin_split_chain_and_cleanup_order(self) -> None:
        client = _Replay({
            "/v1/vouchers/create": [_ok(id=1)],
            "/v1/vouchers/verify": [_ok(), _ok(), _ok(), _ok()],
            "/v1/vouchers/generate": [_ok(id=2), _ok(id=3)],
            "/v1/vouchers/delete": [_ok(deleted=True), _ok(deleted=True), _ok(deleted=True)],
        })
        sql = _Sql([
            ("InvPositionSum AS s", ["ROW:C1|XS|D1|P1|I1|04|A01|10.0000|A02\n"]),
            ("SO_SODetails", ["ROW:11\n"]),
            ("MIN(x.iDLsID)", ["ROW:21\n"]),
            ("b.iDLsID", ["ROW:|A01|1.0000|1\nROW:|A02|1.0000|1\n"]),
            ("x.fOutQuantity", ["ROW:2.0000\n", "ROW:0.0000\n"]),
            ("COUNT(*))", ["ROW:0\n"]),
        ])
        status, detail = _run(sp.sop_bin, client, sql)
        self.assertEqual(status, "PASS", detail)
        lines = client.calls[4][1]["lines"]
        self.assertEqual([row["cposition"] for row in lines], ["A01", "A02"])
        order = [(route.rsplit("/", 1)[1], fields["type"], fields.get("action", "")) for route, fields in client.calls[5:]]
        self.assertEqual(order, [
            ("delete", "sale_out", ""), ("verify", "dispatch", "unverify"), ("delete", "dispatch", ""),
            ("verify", "sale_order", "unverify"), ("delete", "sale_order", ""),
        ])

    def test_bin_rows_mismatch_fails_but_cleans_up(self) -> None:
        client = _Replay({
            "/v1/vouchers/create": [_ok(id=1)],
            "/v1/vouchers/verify": [_ok(), _ok(), _ok(), _ok()],
            "/v1/vouchers/generate": [_ok(id=2), _ok(id=3)],
            "/v1/vouchers/delete": [_ok(deleted=True), _ok(deleted=True), _ok(deleted=True)],
        })
        sql = _Sql([
            ("InvPositionSum AS s", ["ROW:C1|XS|D1|P1|I1|04|A01|10.0000|\n"]),
            ("SO_SODetails", ["ROW:11\n"]),
            ("MIN(x.iDLsID)", ["ROW:21\n"]),
            ("b.iDLsID", ["ROW:|A01|2.0000|0\n"]),
            ("x.fOutQuantity", ["ROW:2.0000\n"]),
        ])
        status, detail = _run(sp.sop_bin, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("出库行", detail)
        self.assertEqual(client.calls[4][1]["lines"], [{"source_line_id": 21, "quantity": 2, "cposition": "A01"}])
        self.assertEqual(len(client.answers["/v1/vouchers/delete"]), 0)

    def test_red_created_when_havepo_on(self) -> None:
        # 必有订单打开时红字退库照常新建、删除（同 U8 客户端），不再期望 409。
        client = _Replay({
            "/v1/vouchers/create": [_ok(id=9)],
            "/v1/vouchers/delete": [_ok(deleted=True)],
            "/v1/vouchers/load": [U8CoError(404, "not_found", "单据不存在")],
        })
        sql = _Sql([
            ("RdRecord01 AS h", ["ROW:01|V1|D1|I1|PT1\n"]),
            ("bPTHavePO", ["ROW:1\n"]),
            ("bredvouch", ["ROW:1|-1.0000|-1.13\n"]),
        ])
        status, detail = _run(sp.pin_red, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("必有订单打开", detail)
        head = client.calls[0][1]["head"]
        self.assertIs(head["red"], True)
        self.assertEqual(head["cptcode"], "PT1")

    def test_red_refused_when_havepo_on_fails(self) -> None:
        refused = U8CoError(409, "state_mismatch", "账套设置了普通业务必有订单，不能无来源录入采购入库单")
        client = _Replay({"/v1/vouchers/create": [refused]})
        sql = _Sql([("RdRecord01 AS h", ["ROW:01|V1|D1|I1|PT1\n"]), ("bPTHavePO", ["ROW:1\n"])])
        status, detail = _run(sp.pin_red, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("409", detail)

    def test_red_created_when_option_off(self) -> None:
        client = _Replay({
            "/v1/vouchers/create": [_ok(id=9)],
            "/v1/vouchers/delete": [_ok(deleted=True)],
            "/v1/vouchers/load": [U8CoError(404, "not_found", "单据不存在")],
        })
        sql = _Sql([
            ("RdRecord01 AS h", ["ROW:01|V1|D1|I1|PT1\n"]),
            ("bPTHavePO", ["ROW:0\n"]),
            ("bredvouch", ["ROW:1|-1.0000|-1.13\n"]),
        ])
        self.assertEqual(_run(sp.pin_red, client, sql)[0], "PASS")

    def test_fx_uses_existing_order(self) -> None:
        client = _Replay({"/v1/vouchers/generate": [_ok(id=5)], "/v1/vouchers/delete": [_ok(deleted=True)]})
        sql = _Sql([("nflat, 1) <> 1", ["ROW:40|41|01\n"]), ("iExchRate", ["ROW:7.100000|0.00|113.00\n"])])
        self.assertEqual(_run(sp.pin_fx, client, sql)[0], "PASS")
        wrong = _Replay({"/v1/vouchers/generate": [_ok(id=5)], "/v1/vouchers/delete": [_ok(deleted=True)]})
        sql = _Sql([("nflat, 1) <> 1", ["ROW:40|41|01\n"]), ("iExchRate", ["ROW:7.100000|0.50|113.00\n"])])
        status, detail = _run(sp.pin_fx, wrong, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("相差", detail)

    def test_fx_order_refused_by_u8_is_not_applicable(self) -> None:
        refused = U8CoError(409, "u8_rejected", "供应商币种与订单币种不一致")
        client = _Replay({"/v1/vouchers/create": [refused]})
        tpl = "ROW:V1|D1||01|普通采购|13.000000|I1|30.000000|U|美元|06\n"
        sql = _Sql([("nflat, 1) <> 1", [""]), ("foreigncurrency", [tpl])])
        status, detail = _run(sp.pin_fx, client, sql)
        self.assertEqual(status, "N/A", detail)
        self.assertEqual(client.calls[0][1]["head"]["nflat"], 7.1)


if __name__ == "__main__":
    unittest.main()
