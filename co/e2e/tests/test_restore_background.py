"""还原比对里 U8 后台表的容忍：只放行能归到还原之后的后台写入，业务表的差异一律失败。不连接 U8。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from co.e2e.context import Run
from co.e2e.fingerprint import normalize
from co.e2e.fingerprint_bg import split_background
from co.e2e.flow import _diff_of
from co.e2e.multi_report import LaneResult, lane_row
from co.e2e.multi_report import render_markdown as render_multi
from co.e2e.report import assemble, render_markdown
from co.e2e.session_reset import RESETS_FILE, reset_note
from co.e2e.tests import support

_DATA = "UFDATA_801_2024"
_META = "UFMeta_801"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _t(name: str, rows: int, checksum: int) -> dict:
    return {"schema": "dbo", "name": name, "rows": rows, "checksum": checksum, "error": ""}


def _doc(data: list[dict], meta: list[dict]) -> dict:
    return normalize({"databases": {_DATA: {"tables": data}, _META: {"tables": meta}}})


_BASE_DATA = [
    _t("SO_SOMain", 3, 9),
    _t("U8LoginProcess", 77, 5),
    _t("U8TaskLog", 34493, 6),
    _t("tc_ufrefresh", 34389, 7),
]
_BASE_META = [_t("UA_Account", 1, 4), _t("UAP_ReportStaticRpt", 2, 8)]


def _ctx(after: dict, post: dict | None) -> Run:
    ctx = Run(False, Path("/nonexistent/u8co-report"), None)
    ctx.snapshots_made = True
    ctx.reverted = True
    ctx.before = _doc(list(_BASE_DATA), list(_BASE_META))
    ctx.reference = _doc(list(_BASE_DATA), list(_BASE_META))
    ctx.after = after
    ctx.post = post
    return ctx


def _background_after() -> dict:
    data = [
        _t("SO_SOMain", 3, 9),
        _t("U8LoginProcess", 84, 15),
        _t("U8TaskLog", 34519, 16),
        _t("UFTmpTable_12_345", 0, 0),
        _t("tc_ufrefresh", 34443, 7),
    ]
    return _doc(data, [_t("UA_Account", 1, 4), _t("UAP_ReportStaticRpt", 2, 18)])


def _background_post() -> dict:
    # 放开多用户之后同几张后台表仍在变。
    data = [
        _t("SO_SOMain", 3, 9),
        _t("U8LoginProcess", 90, 25),
        _t("U8TaskLog", 34530, 26),
        _t("UFTmpTable_12_345", 0, 0),
        _t("tc_ufrefresh", 34460, 7),
    ]
    return _doc(data, [_t("UA_Account", 1, 4), _t("UAP_ReportStaticRpt", 2, 28)])


def _report(ctx: Run) -> dict:
    diff = _diff_of(ctx)
    return assemble(
        {
            "dry_run": False,
            "failed": False,
            "snapshots_made": True,
            "reverted": True,
            "fingerprint_diff": diff,
            "background_tolerated": ctx.background_notes,
            "tests": [{"name": "健康检查", "status": "PASS", "detail": "ok"}],
            "before": ctx.before,
            "after": ctx.after,
        }
    )


class BackgroundToleranceTests(unittest.TestCase):
    def test_background_only_passes_with_section(self) -> None:
        ctx = _ctx(_background_after(), _background_post())
        report = _report(ctx)
        self.assertEqual(report["fingerprint_diff"], [])
        self.assertTrue(report["rollback_verified"])
        self.assertEqual(report["overall"], "PASS")
        tolerated = report["background_tolerated"]
        self.assertEqual(len(tolerated), 5)
        self.assertTrue(any("UFTmpTable_12_345 仅出现在测试后（还原之后 U8 新建的临时表）" in line for line in tolerated))
        self.assertTrue(any(line.startswith(_META + " dbo.UAP_ReportStaticRpt ") for line in tolerated))
        text = render_markdown(report)
        self.assertIn("## U8 后台表（已容忍）", text)
        self.assertIn("U8LoginProcess 行数 77 -> 84", text)

    def test_business_table_fails(self) -> None:
        data = [_t("SO_SOMain", 3, 10)] + _BASE_DATA[1:]
        after = _doc(data, list(_BASE_META))
        post = _doc([_t("SO_SOMain", 3, 11)] + _BASE_DATA[1:], list(_BASE_META))
        ctx = _ctx(after, post)
        report = _report(ctx)
        self.assertEqual(report["overall"], "FAIL")
        self.assertFalse(report["rollback_verified"])
        self.assertTrue(any("dbo.SO_SOMain" in line for line in report["fingerprint_diff"]))
        self.assertEqual(report["background_tolerated"], [])
        self.assertNotIn("U8 后台表（已容忍）", render_markdown(report))

    def test_mixed_fails_and_hides_nothing(self) -> None:
        after = _background_after()
        after["databases"][_DATA]["tables"][0]["checksum"] = 10
        post = _background_post()
        ctx = _ctx(_doc(after["databases"][_DATA]["tables"], after["databases"][_META]["tables"]), post)
        report = _report(ctx)
        self.assertEqual(report["overall"], "FAIL")
        diff = report["fingerprint_diff"]
        self.assertTrue(any("dbo.SO_SOMain" in line for line in diff))
        # 后台表的差异照样列在判定依据里，不因为混了业务表就被吞掉。
        self.assertTrue(any("dbo.U8LoginProcess" in line for line in diff))
        self.assertEqual(report["background_tolerated"], [])

    def test_background_without_post_evidence_fails(self) -> None:
        # 放开多用户之后没有再变（或没取到之后的指纹）：不能归到还原之后，不容忍；只剩新建的临时表可以放行。
        ctx = _ctx(_background_after(), None)
        report = _report(ctx)
        self.assertEqual(report["overall"], "FAIL")
        self.assertTrue(any("dbo.U8LoginProcess" in line for line in report["fingerprint_diff"]))

    def test_listed_name_in_other_database_is_not_tolerated(self) -> None:
        # UAP_ReportStaticRpt 只在元数据库的名单里；同名表出现在账套库不放行。
        diff = [_DATA + " dbo.UAP_ReportStaticRpt 行数 1 -> 2 校验 1 -> 2"]
        tolerated, rest = split_background(diff, list(diff))
        self.assertEqual(tolerated, [])
        self.assertEqual(rest, diff)

    def test_temp_table_dropped_is_not_tolerated(self) -> None:
        diff = [_DATA + " dbo.UFTmpTable_1_2 仅出现在测试前"]
        tolerated, rest = split_background(diff, [])
        self.assertEqual(tolerated, [])
        self.assertEqual(rest, diff)


class ToleratedSummaryTests(unittest.TestCase):
    def test_reset_note_counts_tolerated_and_scheduler_rows(self) -> None:
        rows = [
            {"ok": True, "background_tolerated": ["a", "b"], "scheduler_notes": ["t"]},
            {"ok": True, "background_tolerated": ["c"], "scheduler_notes": []},
            {"ok": True},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / RESETS_FILE).write_text(json.dumps(rows), encoding="utf-8")
            note = reset_note(Path(tmp))
        self.assertIn("reset 过 3 次", note)
        self.assertIn("已容忍 U8 后台表 3 行", note)
        self.assertIn("U8 调度记账 1 行", note)

    def test_multi_report_lists_tolerated_per_lane(self) -> None:
        report = {"overall": "PASS", "tests": [], "background_tolerated": ["x", "y"]}
        quiet = {"overall": "PASS", "tests": []}
        rows = [lane_row(LaneResult("lane-a", "802", 0, report)), lane_row(LaneResult("lane-b", "803", 0, quiet))]
        self.assertEqual([row["tolerated"] for row in rows], [2, 0])
        text = render_multi({"overall": "PASS", "lanes": rows, "tests": [], "missing": [], "catalog_size": 0})
        self.assertIn("## U8 后台表（已容忍）", text)
        self.assertIn("lane-a：已容忍 U8 后台表 2 行", text)
        self.assertNotIn("lane-b：已容忍", text)


if __name__ == "__main__":
    unittest.main()
