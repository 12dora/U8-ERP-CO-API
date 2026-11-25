"""红字销售发票参照退货单用例（sale_red_invoice）的登记、接线、正文和补偿。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.client.u8co_client import U8CoError

from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.env import Env
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.sale_red_invoice import RS_BUILDERS, RS_CLEANUP, rs_cases, rs_red, rs_return
from co.e2e.casespec.sale_red_invoice_sql import RS_PACKS, rs_sql
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.tests import support

_NAME = "sale-red-invoice"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(focus: str = "100", **bag: str) -> Env:
    base = {"rs_line": "11", "rs_retl": "21"}
    env = Env(None, None, None, "2026-09-28", "2026", "", dict(base, **bag))  # type: ignore[arg-type]
    env.focus = focus
    return env


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


def _cleanup_env(client: _Replay, bag: dict[str, str]) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag=bag)


class RedSaleCaseTests(unittest.TestCase):
    def test_registered_once_and_wired(self) -> None:
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertEqual([spec.name for spec in rs_cases()], [_NAME])
        for key, fn in RS_BUILDERS.items():
            self.assertIs(EDIT_BUILDERS[key], fn)
        for pack in RS_PACKS:
            self.assertIn(pack, EDIT_PACKS)
        names = [name for name, _sql in edit_samples()]
        self.assertIn("more-rs-src", names)

    def test_needs_are_picked_and_steps_resolve(self) -> None:
        keys = set(pick_keys())
        spec = rs_cases()[0]
        self.assertTrue(spec.optional and spec.requires_login)
        for need in spec.needs:
            self.assertIn(need, keys)
        for step in spec.steps:
            self.assertIn(step.kind, _HANDLERS)
            if step.kind in ("pred", "stash"):
                self.assertTrue(has_edit(step.sql_name), step.sql_name)
                self.assertIn("ROW:", edit_sql(step.sql_name, "100", {}))

    def test_invoices_deleted_then_return_deleted(self) -> None:
        spec = rs_cases()[0]
        deletes = [(step.id_from, step.status) for step in spec.steps if step.route.endswith("/delete")]
        self.assertEqual(
            deletes,
            [("bag:rs_inv1", 200), ("bag:rs_inv2", 409), ("bag:rs_inv2", 200), ("bag:rs_ret", 200)],
        )
        gens = [(step.id_from, step.status) for step in spec.steps if step.route.endswith("/generate")]
        self.assertEqual(gens[0], ("need", 200))
        self.assertEqual(gens[1], ("bag:rs_ret", 409))  # 退货单未审核
        self.assertTrue({"new:sale_invoice", "new:sale_return", "src:dispatch:rs_src"} <= spec.tags)


class RedSaleBodyTests(unittest.TestCase):
    def test_return_body_is_invoiced(self) -> None:
        body = rs_return(_env())
        self.assertEqual((body["type"], body["id"], body["head"]), ("sale_return", 100, {"invoiced": True}))
        self.assertEqual(body["lines"], [{"source_line_id": 11, "quantity": 1}])

    def test_red_body_names_the_return(self) -> None:
        body = rs_red(_env("200"))
        self.assertEqual((body["type"], body["id"], body["source_type"]), ("sale_invoice", 200, "sale_return"))
        self.assertEqual(body["lines"], [{"source_line_id": 21, "quantity": 1}])
        self.assertNotIn("head", body)

    def test_bad_ids_raise(self) -> None:
        with self.assertRaises(ValueError):
            rs_red(_env(rs_retl="1;DROP"))
        with self.assertRaises(ValueError):
            rs_return(_env("0"))


class RedSaleCleanupTests(unittest.TestCase):
    def test_drops_invoices_before_the_return(self) -> None:
        client = _Replay(
            {
                "/v1/vouchers/verify": [
                    U8CoError(409, "state_mismatch", "单据未审核"),
                    {"ok": True},
                ],
                "/v1/vouchers/delete": [{"ok": True, "deleted": True}, {"ok": True, "deleted": True}],
            }
        )
        env = _cleanup_env(client, {"rs_inv1": "31", "rs_ret": "41"})
        note = RS_CLEANUP[_NAME](env, _NAME)
        self.assertIn("sale_invoice 31 已清理", note)
        self.assertIn("sale_return 41 已清理", note)
        deleted = [(fields["type"], fields["id"]) for route, fields in client.calls if route.endswith("/delete")]
        self.assertEqual(deleted, [("sale_invoice", 31), ("sale_return", 41)])

    def test_nothing_made(self) -> None:
        self.assertEqual(RS_CLEANUP[_NAME](_cleanup_env(_Replay({}), {}), _NAME), "没有新建的单据")


class RedSaleSqlTests(unittest.TestCase):
    def test_readbacks_reject_bad_ids(self) -> None:
        self.assertIn("h.SBVID = 7", rs_sql("rs_inv_shape", "7", {}))
        with self.assertRaises(ValueError):
            rs_sql("rs_set", "7 OR 1=1", {})
        with self.assertRaises(ValueError):
            rs_sql("nope", "7", {})


if __name__ == "__main__":
    unittest.main()
