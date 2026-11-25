"""质量单据修改用例（qm_update、qm_other_update）的登记、标签、正文、SQL 形状。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.e2e.casespec import ops_run, wiring, runner
from co.e2e.casespec import qm_other_update as qo
from co.e2e.casespec import qm_update as qu
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.qm_update_sql import QMU_PACKS, qmu_samples
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_CHECK_CASES = ("qm03-update-split", "qm03-update-verified-409", "qm04-update-split", "qm02-unverify")
_OTHER_CASES = ("qm11-update-nodown", "qm11-update-verified-409", "qm15-update-cycle", "qm15-update-verified-409")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _spec(name: str):
    return next(spec for spec in build_cases({}) if spec.name == name)


class RegistrationTests(unittest.TestCase):
    def test_cases_are_registered_once(self) -> None:
        names = known_names()
        for name in _CHECK_CASES + _OTHER_CASES:
            self.assertEqual(names.count(name), 1, name)

    def test_cleanup_builders_ops_and_packs_are_wired(self) -> None:
        for name in _CHECK_CASES:
            self.assertIn(name, runner._CLEANUP)
            self.assertTrue(_spec(name).optional)
        for builder in qu.QMU_BUILDERS:
            self.assertIn(builder, wiring.EDIT_BUILDERS)
        for name in _OTHER_CASES:
            self.assertTrue(ops_run.has_op(_spec(name).steps[0].sql_name))
        for pack in QMU_PACKS:
            self.assertIn(pack, wiring.EDIT_PACKS)
        self.assertTrue(wiring.has_edit("qmu_q3_res") and wiring.has_edit("qmu_q4_mod"))

    def test_tags_keep_the_create_cases_apart(self) -> None:
        self.assertTrue(conflicts(auto_tags(_spec("qm03-update-split")), auto_tags(_spec("qm01-create-delete"))))
        self.assertTrue(conflicts(auto_tags(_spec("qm04-update-split")), auto_tags(_spec("qm02-create-delete"))))
        self.assertTrue(conflicts(auto_tags(_spec("qm02-unverify")), auto_tags(_spec("qm02-create-delete"))))
        self.assertTrue(conflicts(auto_tags(_spec("qm15-update-cycle")), auto_tags(_spec("qm-other-cycle"))))


class BodyTests(unittest.TestCase):
    def _env(self) -> SimpleNamespace:
        return SimpleNamespace(focus="321", bag={"qmu_rsn": "01", "qmu_ylr": "P01"})

    def test_bodies(self) -> None:
        env = self._env()
        split = qu.qmu_qm03_split(env)
        self.assertEqual((split["type"], split["id"]), ("qm_incoming_check", 321))
        self.assertEqual(split["head"], {"fRegQuantity": 0, "fDisQuantity": 1, "cChkConclusion": "不合格", "cReasonCode": "01"})
        self.assertNotIn("lines", split)
        self.assertIs(qu.qmu_qm03_split_dry(env)["dry_run"], True)
        self.assertEqual(qu.qmu_qm03_touch(env)["head"], {"cChkConclusion": "合格"})
        self.assertEqual(qu.qmu_qm04_split(env)["head"]["fConQuantiy"], 1)
        self.assertEqual(qu.qmu_qm04_split(env)["head"]["cYielderCode"], "P01")

    def test_bad_reason_is_refused(self) -> None:
        env = SimpleNamespace(focus="321", bag={"qmu_rsn": "0'1"})
        with self.assertRaises(ValueError):
            qu.qmu_qm03_split(env)

    def test_bumped_values(self) -> None:
        self.assertEqual(qo._bumped(""), "QMUE2E")
        self.assertEqual(qo._bumped("LOT1"), "LOT1R")
        self.assertEqual(len(qo._bumped("x" * 60)), 60)


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, sql in qmu_samples():
            self.assertEqual(sql_block_reason(sql), "", name)
            self.assertIn("SELECT", sql)

    def test_ids_are_checked(self) -> None:
        from co.e2e.casespec.qm_update_sql import check_state_sql

        with self.assertRaises(ValueError):
            check_state_sql("1;DROP")
