"""形态转换单、调拨申请单、盘点单（stock_other_docs）用例的登记、接线、正文和补偿。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.env import Env
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.stock_other_docs import (
    SM_BUILDERS,
    SM_CLEANUP,
    sm_av_new,
    sm_av_upd,
    sm_cases,
    sm_cv_new,
    sm_tr_new,
    sm_tr_upd,
)
from co.e2e.casespec.stock_other_docs_sql import SM_PACKS
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.runner import _CLEANUP, _HANDLERS
from co.e2e.casespec.steps import _make
from co.e2e.tests import support

_NAMES = ["shape-change-cycle", "transfer-request-cycle", "stock-check-create-delete"]
_BAG = {
    "sm_av": "06", "sm_av_from": "A001", "sm_av_to": "A002", "sm_av_dep": "D01", "sm_av_line": "701",
    "sm_tr": "06", "sm_tr_in": "10", "sm_tr_inv": "A003", "sm_tr_line": "702",
    "sm_cv": "06", "sm_cv_inv": "A004",
}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(focus: str = "", **extra: str) -> Env:
    bag = dict(_BAG)
    bag.update(extra)
    env = Env(None, None, None, "2026-09-28", "2026", "", bag)  # type: ignore[arg-type]
    env.focus = focus
    return env


class StockMiscCaseTests(unittest.TestCase):
    def test_registered_once_and_wired(self) -> None:
        names = known_names()
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
            self.assertIs(_CLEANUP[name], SM_CLEANUP[name])
        self.assertEqual([spec.name for spec in sm_cases()], _NAMES)
        for pack in SM_PACKS:
            self.assertIn(pack, EDIT_PACKS)

    def test_needs_are_picked_and_steps_resolve(self) -> None:
        keys = set(pick_keys())
        for spec in sm_cases():
            self.assertTrue(spec.optional and spec.requires_login)
            for need in spec.needs:
                self.assertIn(need, keys)
            for step in spec.steps:
                self.assertIn(step.kind, _HANDLERS)
                if step.kind in ("apply", "create"):
                    self.assertIs(_make(step.sql_name), SM_BUILDERS[step.sql_name])
                    self.assertIs(EDIT_BUILDERS[step.sql_name], SM_BUILDERS[step.sql_name])
                if step.kind in ("pred", "stash"):
                    self.assertTrue(has_edit(step.sql_name), step.sql_name)
                    self.assertIn("ROW:", edit_sql(step.sql_name, "100", dict(_BAG)))

    def test_each_case_deletes_what_it_made(self) -> None:
        for spec in sm_cases():
            deletes = [step for step in spec.steps if step.route.endswith("/delete") and step.status == 200]
            self.assertEqual(len(deletes), 1, spec.name)
            self.assertEqual(spec.steps[-1].kind, "pred", spec.name)
        check = sm_cases()[2]
        refused = [step for step in check.steps if step.kind == "create" and step.status == 409]
        self.assertEqual(len(refused), 1)

    def test_changes_select_cases(self) -> None:
        picked = select_changed(["co/bridge/src/StockMisc.cs"], list(_NAMES))
        for name in _NAMES:
            self.assertIn(name, picked)

    def test_samples_present(self) -> None:
        names = [name for name, _sql in edit_samples()]
        for name in ("sdoc-sm-av", "sdoc-sm-tr", "sdoc-sm-cv", "sdoc-sm-av-kids", "sdoc-sm-cv-book"):
            self.assertIn(name, names)


class StockMiscBodyTests(unittest.TestCase):
    def test_shape_bodies(self) -> None:
        body = sm_av_new(_env())
        self.assertEqual(body["type"], "shape_change")
        self.assertEqual(body["head"]["cDepCode"], "D01")
        kinds = [(line["bAVType"], line["cInvCode"], line["cWhCode"], line["iGroupNO"]) for line in body["lines"]]
        self.assertEqual(kinds, [("转换前", "A001", "06", 1), ("转换后", "A002", "06", 1)])
        edit = sm_av_upd(_env("900"))
        self.assertEqual((edit["id"], edit["lines"][0]["line_id"]), (900, 701))

    def test_request_bodies(self) -> None:
        body = sm_tr_new(_env())
        self.assertEqual((body["head"]["cOWhCode"], body["head"]["cIWhCode"]), ("06", "10"))
        edit = sm_tr_upd(_env("901"))
        self.assertEqual([line["op"] for line in edit["lines"]], ["update", "add"])
        self.assertEqual(edit["lines"][0]["line_id"], 702)

    def test_check_body_leaves_book_to_bridge(self) -> None:
        body = sm_cv_new(_env())
        self.assertEqual(body["lines"], [{"cInvCode": "A004", "iCVCQuantity": 1}])

    def test_bad_picks_refused(self) -> None:
        with self.assertRaises(ValueError):
            sm_av_new(_env(sm_av="0'6"))
        with self.assertRaises(ValueError):
            sm_tr_upd(_env("abc"))


class StockMiscCleanupTests(unittest.TestCase):
    def test_nothing_made(self) -> None:
        # 不带盘点仓库：补偿不去列表查多建的盘点单（没有客户端）。
        bag = {key: value for key, value in _BAG.items() if key != "sm_cv"}
        env = Env(None, None, None, "2026-09-28", "2026", "", bag)  # type: ignore[arg-type]
        for name in _NAMES:
            self.assertEqual(SM_CLEANUP[name](env, name), "没有新建的单据")


class _Lister:
    def __init__(self, items: list) -> None:
        self.items = items
        self.sent: list = []

    def call(self, route: str, fields: dict) -> dict:
        self.sent.append((route, fields))
        if route.endswith("/list"):
            return {"ok": True, "items": self.items}
        return {"ok": True, "deleted": True}


class StockMiscStrayTests(unittest.TestCase):
    def test_unexpected_second_check_is_deleted(self) -> None:
        client = _Lister([{"id": 55, "maker": "张三"}, {"id": 56, "maker": "李四"}, {"id": 57, "maker": "张三"}])
        operator = SimpleNamespace(acc="801", year="2026", code="op001", password="x")
        env = Env(None, client, operator, "2026-09-28", "2026", "", dict(_BAG, sm_cv_id="57"))  # type: ignore[arg-type]
        env.operator_name = "张三"
        note = SM_CLEANUP["stock-check-create-delete"](env, "stock-check-create-delete")
        deleted = [fields["id"] for route, fields in client.sent if route.endswith("/delete")]
        self.assertEqual(sorted(deleted), [55, 57], note)
        listed = [fields for route, fields in client.sent if route.endswith("/list")][0]
        self.assertEqual(listed["filter"], {"wh_code": "06", "verified": False})


if __name__ == "__main__":
    unittest.main()
