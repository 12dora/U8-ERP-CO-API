"""生产订单修改保留替代料（mo_update_subs）用例的登记、接线、正文和回读 SQL。不连接服务器。"""

from __future__ import annotations

import unittest

from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.env import Env
from co.e2e.casespec.mo_update_subs import MOS_BUILDERS, NAME, mos_cases, mos_new
from co.e2e.casespec.mo_update_subs_sql import MOS_PACKS, mos_src_sql
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.mo_create import moc_compensate
from co.e2e.casespec.mo_create_sql import moc_src_sql
from co.e2e.casespec.mo_update import MOU_BUILDERS, MOU_CLEANUP, mou_cases
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.runner import _CLEANUP, _HANDLERS
from co.e2e.casespec.steps import _make
from co.e2e.tests import support

_BAG = {
    "mos_inv": "P001",
    "mos_dept": "D01",
    "mos_type": "1",
    "mos_bom_from": "2026-01-01",
    "mos_bom_to": "2099-12-31",
    "mou_line": "2000000301",
    "mos_amax": "2000000304",
}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(**bag: str) -> Env:
    return Env(None, None, None, "2026-10-03", "2026", "", dict(_BAG, **bag))  # type: ignore[arg-type]


class MoUpdateSubsCaseTests(unittest.TestCase):
    def test_registered_once_and_wired(self) -> None:
        self.assertEqual(known_names().count(NAME), 1)
        self.assertIn(NAME, [spec.name for spec in mou_cases()])
        self.assertIs(_CLEANUP[NAME], MOU_CLEANUP[NAME])
        self.assertIs(MOU_CLEANUP[NAME], moc_compensate)
        self.assertIs(MOU_BUILDERS["mos_new"], MOS_BUILDERS["mos_new"])
        self.assertIs(EDIT_BUILDERS["mos_new"], mos_new)
        self.assertIn(MOS_PACKS[0], EDIT_PACKS)

    def test_steps_resolve(self) -> None:
        keys = set(pick_keys())
        spec = mos_cases()[0]
        self.assertTrue(spec.optional and spec.requires_login)
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
        applied = [step.sql_name for step in spec.steps if step.kind == "apply"]
        self.assertEqual(applied, ["mou_memo", "mou_double"])
        kept = [step.code for step in spec.steps if step.kind == "pred" and step.sql_name == "mos_digest"]
        self.assertEqual(kept, ["same:mos_digest", "same:mos_digest"])
        moved = [step.code for step in spec.steps if step.kind == "pred" and step.sql_name == "mos_moved"]
        self.assertEqual(moved, ["eq:1", "eq:1"])
        order = [step.sql_name for step in spec.steps[:5]]
        self.assertEqual(order, ["mos_new", "mos_created", "mos_seed", "mos_scnt", "mos_bom_match"])

    def test_body(self) -> None:
        body = mos_new(_env())
        line = body["lines"][0]
        self.assertEqual(body["type"], "production_order")
        self.assertEqual((line["inv_code"], line["qty"], line["mo_type"], line["dept_code"]), ("P001", 1, "1", "D01"))
        self.assertEqual((line["start_date"], line["due_date"]), ("2026-10-03", "2026-10-05"))
        with self.assertRaises(ValueError):
            mos_new(_env(mos_bom_to="2026-10-04"))

    def test_sql(self) -> None:
        src = mos_src_sql()
        self.assertIn("dbo.bom_opcomponentsub", src)
        self.assertNotIn("bom_opcomponentsub", moc_src_sql())
        self.assertTrue(src.rstrip().endswith("ORDER BY b.BomId DESC;"))
        moved = edit_sql("mos_moved", "5", dict(_BAG))
        self.assertIn("MIN(s.AllocateId) > 2000000304", moved)
        self.assertIn("d.MoId = 5", moved)
        self.assertIn("MIN(s.AllocateId) > 0", edit_sql("mos_moved", "5", {}))
        self.assertIn("s.Define37", edit_sql("mos_digest", "5", {}))
        self.assertIn("x.PartId = s.PartId", edit_sql("mos_bom_match", "5", {}))
        seed = edit_sql("mos_seed", "5", {})
        self.assertIn("IF NOT EXISTS", seed)
        self.assertIn("INSERT INTO dbo.mom_moallocatesub", seed)
        self.assertIn("NEWID()", seed)
        self.assertEqual(seed.count("d.MoId = 5"), 3)
        with self.assertRaises(ValueError):
            edit_sql("mos_scnt", "1; DROP", {})
        with self.assertRaises(ValueError):
            edit_sql("mos_moved", "5", {"mos_amax": "1 OR 1=1"})
        names = [name for name, _sql in edit_samples()]
        self.assertIn("more-mos-moved", names)
        self.assertIn("mos-mos-inv", names)
