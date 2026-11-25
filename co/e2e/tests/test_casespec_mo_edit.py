"""已审核 / 已领料生产订单修改（mo_edit）用例的登记、接线、正文、补偿和回读 SQL。不连接服务器。"""

from __future__ import annotations

import unittest

from co.e2e.cases import Operator
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.env import Env
from co.e2e.casespec.wiring import EDIT_BUILDERS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.mo_create import moc_compensate
from co.e2e.casespec.mo_update import MOU_BUILDERS, MOU_CLEANUP, mou_cases
from co.e2e.casespec.mo_update_sql import mou_sql
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.mo_edit import MEMO_ISSUE, MOE_BUILDERS, MOE_CLEANUP, mou_one, moe_cases, wh_decode
from co.e2e.casespec.runner import _CLEANUP, _HANDLERS
from co.e2e.casespec.steps import _make
from co.e2e.tests import support

_NAMES = ("mo-update-verified-nodown", "mo-update-verified-issued", "mo-update-issued-below-409", "mo-update-closed-409")
_BAG = {
    "moc_inv": "P001",
    "moc_dept": "D01",
    "moc_type": "1",
    "mou_line": "2000000301",
    "mou_maxid": "500",
    "moi_mo": "900",
    "moi_out": "2000000302",
    "moi_aid": "2000000304",
    "moi_qty": "0.140000",
    "moi_wh": "048049",
    "mob_mo": "901",
    "mob_out": "2000000303",
    "mob_aid": "2000000305",
}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(focus: str = "", **bag: str) -> Env:
    env = Env(None, None, None, "2026-10-03", "2026", "", dict(_BAG, **bag))  # type: ignore[arg-type]
    env.focus = focus
    return env


def _spec(name: str):
    return next(spec for spec in moe_cases() if spec.name == name)


class MoEditCaseTests(unittest.TestCase):
    def test_registered_once_and_wired(self) -> None:
        names = known_names()
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
            self.assertIs(_CLEANUP[name], MOU_CLEANUP[name])
            self.assertIs(MOU_CLEANUP[name], MOE_CLEANUP[name])
        self.assertIs(MOE_CLEANUP[_NAMES[0]], moc_compensate)
        self.assertEqual([spec.name for spec in mou_cases()[1 : 1 + len(_NAMES)]], list(_NAMES))
        for key, builder in MOE_BUILDERS.items():
            self.assertIs(MOU_BUILDERS[key], builder)
            self.assertIs(EDIT_BUILDERS[key], builder)

    def test_steps_resolve(self) -> None:
        keys = set(pick_keys())
        for spec in moe_cases():
            self.assertTrue(spec.optional and spec.requires_login, spec.name)
            self.assertIn("bom", spec.tags)
            for need in spec.needs:
                self.assertIn(need, keys)
            for step in spec.steps:
                self.assertIn(step.kind, _HANDLERS)
                if step.kind in ("apply", "create"):
                    self.assertIs(_make(step.sql_name), EDIT_BUILDERS[step.sql_name])
                if step.kind in ("pred", "stash"):
                    self.assertTrue(has_edit(step.sql_name), step.sql_name)
                    self.assertIn("ROW:", edit_sql(step.sql_name, "100", dict(_BAG)))
            created = [step.sql_name for step in spec.steps if step.kind == "create"]
            self.assertEqual(created, ["moc_new"])

    def test_issued_flow(self) -> None:
        steps = _spec(_NAMES[1]).steps
        kinds = [(step.kind, step.sql_name or step.route, step.id_from) for step in steps]
        self.assertIn(("keep", "", "made"), kinds)
        self.assertIn(("apply", "moi_issue", "bag:moi_mo"), kinds)
        self.assertIn(("apply", "mou_double", "bag:moi_mo"), kinds)
        self.assertIn(("pred", "moi_link", "bag:moi_mo"), kinds)
        self.assertIn(("pred", "mou_iss0", "bag:moi_mo"), kinds)
        keeps = [step.code for step in steps if step.kind == "keep"]
        self.assertEqual(keeps, ["moi_mo", "moi_out"])
        self.assertIn("w:material_out", _spec(_NAMES[1]).tags)
        deletes = [step.id_from for step in steps if step.route == "/v1/vouchers/delete"]
        self.assertEqual(deletes, ["bag:moi_out", "bag:moi_mo"])

    def test_refusals(self) -> None:
        below = [(step.sql_name, step.status, step.code) for step in _spec(_NAMES[2]).steps if step.status != 200]
        self.assertEqual(below, [("mou_one", 409, "state_mismatch")])
        closed = [(step.sql_name, step.status, step.code) for step in _spec(_NAMES[3]).steps if step.status != 200]
        self.assertEqual(closed, [("mou_double", 409, "state_mismatch")])
        saved = [step.checks for step in _spec(_NAMES[0]).steps if step.sql_name == "mou_double"][0]
        self.assertIn(("state.verified", "true"), saved)


class MoEditSqlTests(unittest.TestCase):
    def test_pick(self) -> None:
        sql = mou_sql("mou_pick_aid", "5", {})
        self.assertIn("d.MoId = 5", sql)
        self.assertIn("a.FVFlag = 1", sql)
        self.assertIn("w.qty >= a.Qty", sql)
        self.assertIn("FOR XML PATH", mou_sql("mou_pick_wh", "5", {}))
        with self.assertRaises(ValueError):
            mou_sql("mou_pick_qty", "1; DROP", {})

    def test_links(self) -> None:
        link = mou_sql("moi_link", "5", {"moi_out": "9", "moi_aid": "7"})
        self.assertIn("r.ID = 9", link)
        self.assertIn("a.AllocateId <> 7", link)
        self.assertIn("d.MoId = 5", link)
        self.assertIn("r.iMPoIds = 8", mou_sql("mob_keep", "5", {"mob_out": "9", "mob_aid": "8"}))
        self.assertIn("r.ID = 0", mou_sql("moi_keep", "5", {}))
        self.assertIn("MIN(a.AllocateId) > 42", mou_sql("mou_newids", "5", {"mou_maxid": "42"}))
        with self.assertRaises(ValueError):
            mou_sql("moi_link", "5", {"moi_out": "9 OR 1=1"})
        names = [name for name, _sql in edit_samples()]
        self.assertIn("more-mou-pick-wh", names)
        self.assertIn("more-moi-link", names)


class MoEditBodyTests(unittest.TestCase):
    def test_issue(self) -> None:
        body = MOE_BUILDERS["moi_issue"](_env("900"))
        self.assertEqual((body["type"], body["id"]), ("material_out", 900))
        self.assertEqual(body["lines"], [{"source_line_id": 2000000304, "quantity": 0.14}])
        self.assertEqual((body["head"]["cWhCode"], body["head"]["dDate"], body["head"]["cMemo"]), ("01", "2026-10-03", MEMO_ISSUE))
        self.assertIn("cRdCode", body["head"])
        with self.assertRaises(ValueError):
            MOE_BUILDERS["mob_issue"](_env("901"))  # mob_qty / mob_wh 没记下

    def test_wh_decode(self) -> None:
        self.assertEqual(wh_decode("048049"), "01")
        self.assertEqual(wh_decode("087072045065"), "WH-A")
        for bad in ("", "04804", "abc", "032", "039"):
            with self.assertRaises(ValueError):
                wh_decode(bad)

    def test_qty_and_close(self) -> None:
        one = mou_one(_env("900"))
        self.assertEqual(one["lines"], [{"op": "update", "line_id": 2000000301, "qty": 1}])
        shut = MOE_BUILDERS["mou_close"](_env("900"))
        self.assertEqual(shut, {"type": "production_order", "id": 900, "action": "close", "line_ids": [2000000301]})
        self.assertEqual(MOE_BUILDERS["mou_open"](_env("900"))["action"], "open")
        with self.assertRaises(ValueError):
            mou_one(_env("x"))


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, {k: v for k, v in fields.items() if k in ("type", "id", "action")}))
        return {"ok": True}


class MoEditCleanupTests(unittest.TestCase):
    def test_issued_cleanup_order(self) -> None:
        env = _env("")
        client = _Client()
        env.client = client  # type: ignore[assignment]
        env.operator = Operator("801", "2026", "op001", "pw")
        env.made[_NAMES[1]] = "2000000302"
        note = MOE_CLEANUP[_NAMES[1]](env, _NAMES[1])
        routes = [(route, fields.get("type"), fields.get("id")) for route, fields in client.calls]
        self.assertEqual(routes[0], ("/v1/vouchers/delete", "material_out", 2000000302))
        self.assertIn(("/v1/vouchers/delete", "production_order", 900), routes)
        self.assertEqual(env.made[_NAMES[1]], "900")
        self.assertIn("材料出库单", note)


if __name__ == "__main__":
    unittest.main()
