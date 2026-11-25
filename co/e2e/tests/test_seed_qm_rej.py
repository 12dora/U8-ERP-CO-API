"""造数 qmr_*（seed/steps_docs_qm_rej）：检验单正文、步骤顺序、按标记找不到上游时不报错。不连接服务器。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from co.e2e.casespec.qm_reject_sql import ARRIVAL_MARK, MO_MARK
from co.e2e.seed import steps_docs_qm_rej as qmr
from co.e2e.seed.context import SeedContext, SeedError
from co.e2e.seed.runner import ALL_STEPS, select
from co.e2e.seed.steps_docs_base import marker
from co.e2e.tests import support
from co.e2e.tests.test_seed_docs import FakeDocServer

_NAMES = ("qmr_po", "qmr_arrival", "qmr_inspect", "qmr_check", "qmr_approve",
          "qmr_mo", "qmr_pinspect", "qmr_pcheck", "qmr_papprove")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class QmrBodyTest(unittest.TestCase):
    def test_split_check_body(self) -> None:
        body = qmr.split_check_body("qm_incoming_check", 7, 8, "P01")
        self.assertEqual((body["type"], body["id"]), ("qm_incoming_check", 7))
        self.assertEqual(body["lines"], [{"source_line_id": 8, "quantity": 2, "fRegQuantity": 1, "fDisQuantity": 1}])
        self.assertEqual(body["head"]["project_code"], "P01")

    def test_marks_match_the_case_sql(self) -> None:
        self.assertEqual(marker(qmr.ARRIVAL_KEY), ARRIVAL_MARK)
        self.assertEqual(marker(qmr.MO_KEY), MO_MARK)


class QmrOrderTest(unittest.TestCase):
    def test_steps_follow_the_existing_qm_chains(self) -> None:
        names = [step.name for step in ALL_STEPS]
        self.assertEqual([step.name for step in qmr.STEPS], list(_NAMES))
        self.assertEqual(sorted(_NAMES, key=names.index), list(_NAMES))
        self.assertLess(names.index("qm04_approve"), names.index("qmr_po"))
        self.assertLess(names.index("bom"), names.index("qmr_mo"))
        self.assertEqual(qmr.SKIP_QMR.split(","), list(_NAMES))
        kept = [step.name for step in select("", qmr.SKIP_QMR)]
        self.assertFalse(set(_NAMES) & set(kept))


class QmrFindTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.server = FakeDocServer()
        self.ctx = SeedContext(Path(self.tmp.name), self.server.link())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_nothing_upstream_finds_nothing(self) -> None:
        for find in (qmr.find_qmr_inspect, qmr.find_qmr_check, qmr.find_qmr_pinspect, qmr.find_qmr_pcheck):
            self.assertEqual(find(self.ctx), [])

    def test_mo_found_by_remark_marker(self) -> None:
        self.server.marked["seed:qmr_mo"] = ["31"]
        self.assertEqual(qmr.find_qmr_mo(self.ctx), [31])
        self.assertTrue(any("Remark = N'seed:qmr_mo'" in sql for sql in self.server.sqls))

    def test_check_needs_inspect_first(self) -> None:
        with self.assertRaises(SeedError) as caught:
            next(step for step in qmr.STEPS if step.name == "qmr_check").apply(self.ctx)
        self.assertIn("qmr_inspect", str(caught.exception))
        self.assertEqual(self.server.posts, [])


if __name__ == "__main__":
    unittest.main()
