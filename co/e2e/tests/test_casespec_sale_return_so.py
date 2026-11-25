"""退货单参照销售订单用例的登记、挑单 SQL 和自定义步骤。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e.casespec import srcless as sl
from co.e2e.casespec import sale_return_so as srso
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.ops_run import has_op
from co.e2e.casespec.tags import auto_tags
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers.pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: _Replay) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="op001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-29", bag={})


def _spec():
    return srso.sale_return_so_cases()[0]


def _rows(*cells: str):
    answers = iter(["ROW:" + cell if cell else "" for cell in cells])
    fake = mock.Mock(side_effect=lambda *_a: next(answers))
    return mock.patch.object(sl, "run_guarded", fake), mock.patch.object(srso, "run_guarded", fake)


def _run(client: _Replay, *cells: str) -> tuple[str, str]:
    one, two = _rows(*cells)
    with one, two:
        return srso.sale_return_from_so(_env(client), _spec(), _spec().steps[0])


def _ok() -> list:
    return [{"ok": True, "id": 9}, {"ok": True}, {"ok": True, "deleted": True}, U8CoError(404, "not_found", "x")]


class SaleReturnSoTests(unittest.TestCase):
    def test_registered(self) -> None:
        spec = _spec()
        self.assertEqual(spec.name, "sale-return-from-so")
        self.assertEqual(known_names().count(spec.name), 1)
        self.assertTrue(spec.optional and has_op(spec.steps[0].sql_name) and auto_tags(spec))

    def test_sql(self) -> None:
        pick = srso.pick_sql()
        self.assertIn("ISNULL(d.iFHQuantity, 0) >= 1", pick)
        self.assertNotIn("d.iQuantity - ", pick)  # 可退是累计发货数量，不是未发货余量
        self.assertNotIn("d.cWhCode", pick)  # SO_SODetails 没有仓库列
        self.assertIn("i.cDefWareHouse", pick)
        self.assertIn("ISNULL(i.bFree10, 0) = 0", pick)
        self.assertIn("d.iSOsID = 42;", srso.so_qty_sql(42))
        self.assertIn("ISNULL(d.iCorID, 0) = 0", srso.shape_sql(7, 42))
        with self.assertRaises(ValueError):
            srso.so_qty_sql("1;--")  # type: ignore[arg-type]

    def test_return_one_and_restore(self) -> None:
        client = _Replay(_ok())
        got = _run(client, "100|200|01", "2.000000|5.000000", "3.000000|4.000000", "1", "2.000000|5.000000")
        self.assertEqual(got[0], "PASS", got)
        route, fields = client.calls[0]
        self.assertEqual(route, "/v1/vouchers/generate")
        self.assertEqual((fields["type"], fields["id"], fields["source_type"]), ("sale_return", 100, "sale_order"))
        self.assertEqual(fields["lines"], [{"source_line_id": 200, "quantity": 1, "cwhcode": "01"}])
        self.assertEqual([call[0] for call in client.calls[1:]], ["/v1/vouchers/load", "/v1/vouchers/delete", "/v1/vouchers/load"])

    def test_shipped_qty_unchanged_fails_after_delete(self) -> None:
        client = _Replay(_ok())
        got = _run(client, "100|200|01", "2|5", "3|5", "1", "2|5")
        self.assertEqual(got[0], "FAIL")
        self.assertIn("iFHQuantity", got[1])
        self.assertEqual(client.calls[2][0], "/v1/vouchers/delete")

    def test_not_restored_after_delete_fails(self) -> None:
        got = _run(_Replay(_ok()), "100|200|01", "2|5", "3|4", "1", "3|4")
        self.assertEqual(got[0], "FAIL")
        self.assertIn("删除后订单行", got[1])

    def test_refused_generate_fails_and_none_is_na(self) -> None:
        refused = U8CoError(409, "u8_rejected", "退货数量不能大于应发货数量")
        got = _run(_Replay([refused]), "100|200|01", "0|0")
        self.assertEqual(got[0], "FAIL")
        self.assertIn("应发货数量", got[1])
        self.assertEqual(_run(_Replay([]), "")[0], "N/A")


if __name__ == "__main__":
    unittest.main()
