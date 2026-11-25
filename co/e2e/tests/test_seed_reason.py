"""造数 qm_reason（seed/steps_archives）：原因码 01（所属类型 1）缺时经 archives/create 建，已有就跳过，排在 qmr_* 之前。不连接服务器。"""

from __future__ import annotations

import unittest

from co.e2e.seed import data
from co.e2e.seed.base import ensure_archives
from co.e2e.seed.runner import ALL_STEPS, run, select
from co.e2e.tests import support
from co.e2e.tests.test_seed import SeedCase


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class ReasonSeedTest(SeedCase):
    def test_creates_missing_reason(self) -> None:
        self.assertEqual(run(self.ctx, select("qm_reason")), 0)
        (path, body), = self.server.posts
        self.assertTrue(path.endswith("archives/create"))
        self.assertEqual((body["archive"], body["code"]), ("reason", data.QM_REASON))
        self.assertEqual(body["fields"], {"name": "检测指标不合格", "Reasontype": 1})

    def test_skips_existing_reason(self) -> None:
        self.server.existing.add(data.QM_REASON)
        self.assertEqual(ensure_archives(self.ctx, data.REASONS), (0, 1))
        self.assertEqual(self.server.posts, [])

    def test_probe_reads_reason_table(self) -> None:
        self.assertEqual(data.KIND_TABLE["reason"], ("Reason", "cReasonCode"))


class ReasonOrderTest(unittest.TestCase):
    def test_runs_before_reject_sources(self) -> None:
        names = [step.name for step in ALL_STEPS]
        self.assertLess(names.index("qm_reason"), names.index("qmr_po"))
        self.assertLess(names.index("qm_reason"), names.index("qmr_check"))


if __name__ == "__main__":
    unittest.main()
