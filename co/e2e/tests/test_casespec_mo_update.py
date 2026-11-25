"""生产订单修改（mo_update）用例的登记、接线、正文和回读 SQL。不连接服务器。"""

from __future__ import annotations

import unittest

from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.env import Env
from co.e2e.casespec.wiring import EDIT_BUILDERS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.mo_create import MOC_BUILDERS, moc_compensate
from co.e2e.casespec.mo_update import MOU_BUILDERS, MOU_CLEANUP, mou_cases, mou_double, mou_memo
from co.e2e.casespec.mo_update_sql import MEMO_UPD, mou_sql
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.mo_update_subs import MOS_BUILDERS
from co.e2e.casespec.mo_edit import MOE_BUILDERS
from co.e2e.casespec.runner import _CLEANUP, _HANDLERS
from co.e2e.casespec.steps import _make
from co.e2e.tests import support

_NAME = "mo-update-allocates"
_BAG = {"moc_inv": "P001", "moc_dept": "D01", "moc_type": "1", "mou_line": "2000000301"}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(focus: str = "", **bag: str) -> Env:
    env = Env(None, None, None, "2026-09-28", "2026", "", dict(_BAG, **bag))  # type: ignore[arg-type]
    env.focus = focus
    return env


class MoUpdateCaseTests(unittest.TestCase):
    def test_registered_once_and_wired(self) -> None:
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertIs(_CLEANUP[_NAME], MOU_CLEANUP[_NAME])
        self.assertIs(MOU_CLEANUP[_NAME], moc_compensate)
        self.assertIn(_NAME, select_changed(["co/bridge/src/MoUpdateCom.cs"], [_NAME]))

    def test_steps_resolve(self) -> None:
        keys = set(pick_keys())
        spec = mou_cases()[0]
        self.assertTrue(spec.optional and spec.requires_login)
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
        self.assertIs(EDIT_BUILDERS["moc_new"], MOC_BUILDERS["moc_new"])
        applied = [step.sql_name for step in spec.steps if step.kind == "apply"]
        self.assertEqual(applied, ["mou_start", "mou_memo", "mou_double"])
        refused = [step.sql_name for step in spec.steps if step.status == 400]
        self.assertEqual(refused, ["mou_start"])

    def test_sql(self) -> None:
        self.assertIn(MEMO_UPD, mou_sql("mou_memo", "5", {}))
        self.assertIn("iStrsQuanDecDgt", mou_sql("mou_scaled", "5", {}))
        self.assertIn("OpComponentId", mou_sql("mou_digest", "5", {}))
        self.assertIn("a.MoDId = 77", mou_sql("mou_gone", "5", {"mou_line": "77"}))
        self.assertIn("a.MoDId = 0", mou_sql("mou_gone", "5", {}))
        preds = [step.code for step in mou_cases()[0].steps if step.sql_name == "mou_digest" and step.kind == "pred"]
        self.assertEqual(preds, ["same:mou_digest", "same:mou_digest"])
        with self.assertRaises(ValueError):
            mou_sql("mou_acnt", "1; DROP", {})
        names = [name for name, _sql in edit_samples()]
        self.assertIn("more-mou-scaled", names)


class MoUpdateBodyTests(unittest.TestCase):
    def test_bodies(self) -> None:
        self.assertEqual(set(MOU_BUILDERS), {"mou_memo", "mou_double", "mou_start"} | set(MOE_BUILDERS) | set(MOS_BUILDERS))
        self.assertEqual(MOU_BUILDERS["mou_start"](_env("900"))["lines"][0]["start_date"], "2026-09-29")
        memo = mou_memo(_env("900"))
        self.assertEqual((memo["type"], memo["id"], memo["head"]), ("production_order", 900, {"remark": MEMO_UPD}))
        self.assertEqual(memo["lines"], [{"op": "update", "line_id": 2000000301, "due_date": "2026-10-01"}])
        double = mou_double(_env("900"))
        self.assertEqual(double["lines"], [{"op": "update", "line_id": 2000000301, "qty": 2}])
        self.assertNotIn("head", double)
        with self.assertRaises(ValueError):
            mou_double(_env("x"))
        with self.assertRaises(ValueError):
            mou_double(_env("900", mou_line=""))


if __name__ == "__main__":
    unittest.main()
