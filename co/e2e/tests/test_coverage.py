"""按改动挑用例（coverage.select_changed）和 --cases 交集（select.merge_changed）。不调 git、不连服务器。"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import (
    ALL,
    ALWAYS,
    FAIL_CLOSED,
    IA,
    NONE,
    OPENINGS,
    PATH_RULES,
    PERIODS,
    matches,
    rule_for,
    select_changed,
)
from co.e2e.casespec.select import merge_changed

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "co" / "bridge" / "src"


class SelectChangedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.names = list(known_names())

    def test_unknown_bridge_file_selects_all(self) -> None:
        self.assertEqual(select_changed(["co/bridge/src/NewThing.cs"], self.names), self.names)

    def test_fail_closed_roots_select_all(self) -> None:
        for path in (
            "co/bridge/build.ps1",
            "co/client/u8co_new.py",
            "api/u8co_api/new_mod.py",
            "co/e2e/engine.py",
            "co/e2e/casespec/wiring.py",
            "co/SafePath.ps1",
        ):
            with self.subTest(path=path):
                self.assertEqual(select_changed([path], self.names), self.names)

    def test_shared_infra_selects_all(self) -> None:
        for name in ("Json.cs", "Dispatch.cs", "StaPool.cs", "CoTrans.cs", "Kinds.cs", "ConfigRules.cs", "AuthCache.cs"):
            with self.subTest(name=name):
                self.assertEqual(select_changed(["co/bridge/src/" + name], self.names), self.names)

    def test_docs_only_selects_always(self) -> None:
        paths = ["README.md", "CHANGELOG.md", "docs/testing.md", "events/u8co_events/x.py", "scripts/docs/build.sh"]
        self.assertEqual(select_changed(paths, self.names), list(ALWAYS))
        self.assertEqual(select_changed(["README.md"], self.names), ["health", "login"])

    def test_empty_change_selects_always(self) -> None:
        self.assertEqual(select_changed([], self.names), list(ALWAYS))

    def test_sale_edit_selects_sale_subset(self) -> None:
        got = select_changed(["co/bridge/src/SaleEdit.cs"], self.names)
        for name in ("health", "login", "sale-verify", "sale-update", "dispatch-update", "load-sale_order"):
            self.assertIn(name, got)
        for name in ("gl-cycle", "archive-list", "bom-lifecycle", "po-cycle"):
            self.assertNotIn(name, got)
        self.assertLess(len(got), len(self.names))

    def test_report_file_selects_report(self) -> None:
        got = select_changed(["co/bridge/src/ReportsStockLedger.cs"], self.names)
        self.assertIn("report-stock-ledger", got)
        self.assertNotIn("gl-cycle", got)

    def test_catalog_order_kept(self) -> None:
        paths = ["co/bridge/src/GlSave.cs", "co/bridge/src/SaleEdit.cs", "docs/faq.md"]
        got = select_changed(paths, self.names)
        self.assertEqual(got, [name for name in self.names if name in set(got)])
        self.assertEqual(len(got), len(set(got)))

    def test_sql_scripts_select_ia(self) -> None:
        # 嵌进桥的存货核算脚本：只跑存货核算用例；结账脚本也给 periods/close 用。
        post = rule_for("co/bridge/sql/ia/post.sql")
        self.assertEqual(post, ("co/bridge/sql/*", IA))
        for name in ("close.sql", "unclose.sql"):
            with self.subTest(name=name):
                hit = rule_for("co/bridge/sql/ia/" + name)
                self.assertIsNotNone(hit)
                self.assertEqual(hit[1], IA + PERIODS)
        self.assertEqual(rule_for("co/bridge/src/SqlScript.cs")[1], IA + PERIODS + ("openings-ia-",))
        self.assertEqual(rule_for("co/bridge/src/SqlScriptSelfTest.cs")[1], IA + PERIODS + ("openings-ia-",))
        # 存货核算期初记账的脚本、桥代码：只跑 openings-ia-*（OpeningIa 另跑存货核算用例）。
        for name in ("qc_keep.sql", "qc_recover.sql"):
            self.assertEqual(rule_for("co/bridge/sql/ia/" + name)[1], ("openings-ia-",))
        self.assertIn("openings-ia-post-live", select_changed(["co/bridge/src/OpeningIa.cs"], self.names))
        for name in ("TestAccountGate.cs", "TestAccountGateSelfTest.cs"):
            self.assertEqual(rule_for("co/bridge/src/" + name)[1], OPENINGS + IA + PERIODS)
        got = select_changed(["co/bridge/sql/ia/period_end.sql"], self.names)
        self.assertLess(len(got), len(self.names))
        self.assertNotIn("gl-cycle", got)

    def test_windows_and_dot_paths(self) -> None:
        self.assertEqual(rule_for(".\\co\\bridge\\src\\GlSave.cs"), rule_for("co/bridge/src/GlSave.cs"))
        self.assertIsNone(rule_for(""))

    def test_every_bridge_source_has_explicit_rule(self) -> None:
        # 现有桥源码都要落在具体规则里；兜底规则只给以后新加、还没登记的文件。
        loose = []
        for name in sorted(os.listdir(SRC)):
            hit = rule_for("co/bridge/src/" + name)
            if hit is None or hit[0] == "co/bridge/*":
                loose.append(name)
        self.assertEqual(loose, [])


class RuleTableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.names = known_names()

    def test_always_in_catalog(self) -> None:
        for name in ALWAYS:
            self.assertIn(name, self.names)

    def test_rule_targets_exist(self) -> None:
        for glob, targets in PATH_RULES:
            if targets == ALL:
                continue
            for target in targets:
                with self.subTest(glob=glob, target=target):
                    self.assertTrue(any(matches(name, target) for name in self.names))

    def test_every_case_reachable_without_all(self) -> None:
        # 每个用例都要能被某条具体规则（不是全跑）或 ALWAYS 选中；新用例落不进任何一族就在这里报。
        targets = set(ALWAYS)
        for _glob, rule_targets in PATH_RULES:
            if rule_targets != ALL:
                targets.update(rule_targets)
        missing = [name for name in self.names if not any(matches(name, t) for t in targets)]
        self.assertEqual(missing, [])

    def test_fail_closed_rules_last(self) -> None:
        tail = PATH_RULES[-len(FAIL_CLOSED):]
        self.assertEqual(tail, tuple((glob, ALL) for glob in FAIL_CLOSED))

    def test_none_is_empty(self) -> None:
        self.assertEqual(NONE, ())


class MergeChangedTest(unittest.TestCase):
    def test_no_cases_keeps_changed(self) -> None:
        changed = ["health", "login", "sale-verify"]
        self.assertEqual(merge_changed(None, changed), tuple(changed))

    def test_intersection_keeps_always(self) -> None:
        changed = ["health", "login", "sale-verify", "sale-update"]
        self.assertEqual(merge_changed(("sale-update", "gl-cycle"), changed), ("health", "login", "sale-update"))

    def test_empty_intersection_runs_always(self) -> None:
        changed = ["health", "login", "sale-verify"]
        self.assertEqual(merge_changed(("gl-cycle",), changed), ("health", "login"))


if __name__ == "__main__":
    unittest.main()
