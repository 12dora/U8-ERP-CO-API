"""生产订单新增 / 删除用例的登记、接线和正文。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.client.u8co_client import U8CoError

from co.e2e.casespec.assertutil import EITHER
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.env import Env
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.mo_create import MOC_BUILDERS, MOC_CLEANUP, moc_bad_due, moc_bad_inv, moc_cases, moc_new
from co.e2e.casespec.mo_create_sql import MOC_KEYS, MOC_PACKS, MOC_STASH, moc_samples, moc_sql
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.steps import _make
from co.e2e.tests import support

_NAMES = ("mo-create-delete", "mo-create-bad")
_BAG = {
    "moc_inv": "P001",
    "moc_dept": "D01",
    "moc_type": "1",
    "moc_bom_from": "2026-01-21",
    "moc_bom_to": "2099-12-31",
}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(**bag: str) -> Env:
    # 正文构造只用 today 和 bag；客户端、操作员不用。
    return Env(None, None, None, "2026-09-28", "2026", "", dict(_BAG, **bag))  # type: ignore[arg-type]


class _Replay:
    """按路由回放：值是 dict 原样返回，是 U8CoError 就抛出。记录调用的路由和正文。"""

    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


def _cleanup_env(client: _Replay, made: str) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", made={"mo-create-delete": made})


class MoCreateCaseTests(unittest.TestCase):
    def test_names_registered_once(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in moc_cases()), _NAMES)
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
        self.assertTrue(all(spec.optional and spec.requires_login for spec in moc_cases()))

    def test_needs_are_picked_and_steps_resolve(self) -> None:
        keys = set(pick_keys())
        for spec in moc_cases():
            for need in spec.needs:
                self.assertIn(need, keys, spec.name)
            for step in spec.steps:
                self.assertIn(step.kind, _HANDLERS, spec.name)
                if step.kind == "create":
                    self.assertIs(_make(step.sql_name), MOC_BUILDERS[step.sql_name])
                if step.kind in ("pred", "stash"):
                    self.assertTrue(has_edit(step.sql_name), step.sql_name)
                    self.assertIn("ROW:", edit_sql(step.sql_name, "100", {}))

    def test_create_is_deleted_and_bad_creates_make_nothing(self) -> None:
        good, bad = moc_cases()
        routes = [step.route for step in good.steps]
        deletes = [step for step in good.steps if step.route.endswith("/delete")]
        self.assertEqual([(step.status, step.code) for step in deletes], [(409, "state_mismatch"), (200, "")])
        self.assertLess(routes.index("/v1/vouchers/verify"), routes.index("/v1/vouchers/delete"))
        self.assertNotIn(EITHER, [step.status for step in good.steps])
        self.assertEqual([(step.kind, step.status, step.code) for step in bad.steps], [("create", 400, "bad_request")] * 2)

    def test_ids_are_stashed_before_the_first_delete(self) -> None:
        good = moc_cases()[0]
        kinds = [(step.kind, step.sql_name, step.code) for step in good.steps]
        first_delete = next(i for i, step in enumerate(good.steps) if step.route.endswith("/delete"))
        stashed = [i for i, (kind, name, key) in enumerate(kinds) if kind == "stash" and name == key]
        self.assertEqual([kinds[i][1] for i in stashed], list(MOC_STASH))
        self.assertLess(max(stashed), first_delete)


class MoCreateBodyTests(unittest.TestCase):
    def test_new_body_uses_picked_codes_and_auto_number(self) -> None:
        body = moc_new(_env())
        self.assertEqual(body["type"], "production_order")
        self.assertNotIn("mo_code", body["head"])
        line = body["lines"][0]
        self.assertEqual((line["inv_code"], line["dept_code"], line["mo_type"]), ("P001", "D01", "1"))
        self.assertEqual((line["start_date"], line["due_date"], line["qty"]), ("2026-09-28", "2026-09-30", 1))

    def test_new_body_checks_bom_validity_on_today(self) -> None:
        for bag in ({"moc_bom_from": "2026-09-29"}, {"moc_bom_to": "2026-09-29"}, {"moc_bom_to": ""}):
            with self.assertRaises(ValueError):
                moc_new(_env(**bag))
        self.assertEqual(moc_new(_env(moc_bom_to="2026-09-30"))["lines"][0]["start_date"], "2026-09-28")

    def test_bad_bodies(self) -> None:
        self.assertNotEqual(moc_bad_inv(_env())["lines"][0]["inv_code"], "P001")
        line = moc_bad_due(_env())["lines"][0]
        self.assertLess(line["due_date"], line["start_date"])


class MoCreateCleanupTests(unittest.TestCase):
    def test_compensation_unverifies_then_deletes_the_made_order(self) -> None:
        client = _Replay(
            {
                "/v1/vouchers/load": [{"ok": True}],
                "/v1/vouchers/verify": [U8CoError(409, "state_mismatch", "单据未审核")],
                "/v1/vouchers/delete": [{"ok": True, "deleted": True}],
            }
        )
        note = MOC_CLEANUP["mo-create-delete"](_cleanup_env(client, "2000000201"), "mo-create-delete")
        self.assertIn("已补偿删除", note)
        self.assertEqual([route for route, _ in client.calls], ["/v1/vouchers/load", "/v1/vouchers/verify", "/v1/vouchers/delete"])
        self.assertEqual(client.calls[1][1]["action"], "unverify")
        self.assertEqual(client.calls[2][1]["id"], 2000000201)

    def test_compensation_skips_gone_or_missing_orders(self) -> None:
        client = _Replay({"/v1/vouchers/load": [U8CoError(404, "not_found", "单据不存在")]})
        self.assertIn("已不在", MOC_CLEANUP["mo-create-delete"](_cleanup_env(client, "9"), "mo-create-delete"))
        empty = _Replay({})
        self.assertEqual(MOC_CLEANUP["mo-create-delete"](_cleanup_env(empty, ""), "mo-create-delete"), "没有新建的生产订单")
        self.assertEqual(empty.calls, [])


class MoCreateSqlTests(unittest.TestCase):
    def test_pick_returns_bom_window_keys(self) -> None:
        self.assertEqual(MOC_PACKS[0][1], MOC_KEYS)
        self.assertEqual(len(MOC_PACKS[0][2]), len(MOC_KEYS))
        self.assertIn("VersionEndDate, 23)", MOC_PACKS[0][3]())

    def test_gone_counts_allocates_and_subs_by_stashed_ids(self) -> None:
        bag = {"moc_dmin": "31", "moc_dmax": "31", "moc_amin": "500", "moc_amax": "504"}
        sql = moc_sql("moc_gone", "100", bag)
        self.assertIn("a.MoDId IN (31, 31)", sql)
        self.assertIn("mom_moallocatesub AS s WHERE s.AllocateId BETWEEN 500 AND 504", sql)
        with self.assertRaises(ValueError):
            moc_sql("moc_gone", "100", dict(bag, moc_amin="1;DROP"))


class MoCreateWiringTests(unittest.TestCase):
    def test_packs_builders_and_samples_are_wired(self) -> None:
        for pack in MOC_PACKS:
            self.assertIn(pack, EDIT_PACKS)
            self.assertIn("ROW:", pack[3]())
        for name in MOC_BUILDERS:
            self.assertIs(EDIT_BUILDERS[name], MOC_BUILDERS[name])
        text = dict(sample_sql())
        mine = dict(edit_samples())
        for name, sql in moc_samples():
            self.assertEqual(text[name], sql, name)
            self.assertEqual(mine[name], sql, name)
        with self.assertRaises(ValueError):
            edit_sql("moc_gone", "0", {})


if __name__ == "__main__":
    unittest.main()
