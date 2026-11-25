"""e2e 分片：分线、完整性、按报告重算。不连接服务器，只读写临时目录。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from co.e2e import shard_assign
from co.e2e import shard_recompute as sr
from co.e2e.casespec import catalog

CATALOG = ["health", "bad-signature", "account-foreign", "login", "a", "b", "c", "d"]
LANES = {"main": "甲", "lane2": "乙"}
HEAD = ["health", "bad-signature", "account-foreign", "login"]


def _assign(lane2: tuple[str, ...] = ("b", "d"), main: tuple[str, ...] = ()) -> shard_assign.Assign:
    return shard_assign.Assign(tuple(HEAD), {"main": main, "lane2": lane2}, "main")


def _report(path: Path, statuses: dict[str, str]) -> Path:
    tests = [{"name": name, "case": name, "status": status} for name, status in statuses.items()]
    path.write_text(json.dumps({"overall": "PASS", "tests": tests}, ensure_ascii=False), encoding="utf-8")
    return path


class PlanTest(unittest.TestCase):
    def test_wave0_every_lane_rest_disjoint(self) -> None:
        got = shard_assign.plan(_assign(), CATALOG, LANES)
        self.assertEqual(got["乙"], ["health", "bad-signature", "account-foreign", "login", "b", "d"])
        self.assertEqual(got["甲"], ["health", "bad-signature", "account-foreign", "login", "a", "c"])
        self.assertEqual(shard_assign.completeness(got, CATALOG), [])

    def test_explicit_main_listing_same_as_default(self) -> None:
        got = shard_assign.plan(_assign(main=("a",)), CATALOG, LANES)
        self.assertEqual(got["甲"][-2:], ["a", "c"])

    def test_unknown_case_warns_and_drops(self) -> None:
        notes: list[str] = []
        got = shard_assign.plan(_assign(lane2=("b", "zz")), CATALOG, LANES, notes)
        self.assertEqual(got["乙"][-1:], ["b"])
        self.assertEqual(shard_assign.completeness(got, CATALOG), [])
        self.assertEqual(len(notes), 1)
        self.assertIn("zz", notes[0])

    def test_unknown_warning_goes_to_stderr_without_notes(self) -> None:
        with redirect_stderr(io.StringIO()) as err:
            shard_assign.plan(_assign(lane2=("zz",)), CATALOG, LANES)
        self.assertIn("警告：", err.getvalue())
        self.assertIn("zz", err.getvalue())

    def test_new_catalog_case_goes_to_default(self) -> None:
        notes: list[str] = []
        got = shard_assign.plan(_assign(), [*CATALOG, "e"], LANES, notes)
        self.assertEqual(got["甲"][-1], "e")
        self.assertEqual(notes, [])

    def test_wave0_differs_warns_and_follows_catalog(self) -> None:
        notes: list[str] = []
        stale = shard_assign.Assign(("health", "login", "gone"), {"lane2": ("b",)}, "main")
        got = shard_assign.plan(stale, CATALOG, LANES, notes)
        self.assertEqual(got["甲"][:4], HEAD)
        self.assertEqual(got["乙"], [*HEAD, "b"])
        self.assertTrue(any("第 0 波" in line for line in notes))
        self.assertTrue(any("gone" in line for line in notes))

    def test_lane_case_now_in_wave0_runs_everywhere(self) -> None:
        notes: list[str] = []
        stale = shard_assign.Assign(("health", "login"), {"lane2": ("account-foreign",)}, "main")
        got = shard_assign.plan(stale, CATALOG, LANES, notes)
        self.assertIn("account-foreign", got["甲"])
        self.assertIn("account-foreign", got["乙"])
        self.assertTrue(any("现属第 0 波" in line for line in notes))

    def test_refuse_case_in_two_lanes(self) -> None:
        with self.assertRaisesRegex(ValueError, "同时列在"):
            shard_assign.plan(_assign(lane2=("b",), main=("b",)), CATALOG, LANES)

    def test_refuse_wave0_in_lane(self) -> None:
        with self.assertRaisesRegex(ValueError, "第 0 波"):
            shard_assign.plan(_assign(lane2=("login",)), CATALOG, LANES)

    def test_refuse_unknown_label(self) -> None:
        with self.assertRaisesRegex(ValueError, "没有标签 other"):
            shard_assign.plan(_assign(), CATALOG, {"main": "甲", "lane2": "乙", "other": "丙"})

    def test_refuse_unmapped_label(self) -> None:
        with self.assertRaisesRegex(ValueError, "lane2"):
            shard_assign.plan(_assign(), CATALOG, {"main": "甲"})

    def test_refuse_same_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "不同的线"):
            shard_assign.plan(_assign(), CATALOG, {"main": "甲", "lane2": "甲"})


class CompletenessTest(unittest.TestCase):
    def test_missing_in_catalog_order(self) -> None:
        got = shard_assign.completeness({"甲": ["health", "a"], "乙": ["health", "login"]}, CATALOG)
        self.assertEqual(got, ["bad-signature", "account-foreign", "b", "c", "d"])


class LoadTest(unittest.TestCase):
    def _load(self, data: object) -> shard_assign.Assign:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shards.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return shard_assign.load(path)

    def test_roundtrip(self) -> None:
        got = self._load({"version": 1, "wave0": ["health"], "lanes": {"lane2": ["a"]}, "default_lane": "main"})
        self.assertEqual(got.lanes, {"lane2": ("a",)})
        self.assertEqual(got.labels(), {"main", "lane2"})

    def test_refuse_bad_shapes(self) -> None:
        bad = [
            [],
            {"version": 3, "wave0": [], "lanes": {}, "default_lane": "main"},
            {"version": 1, "wave0": [], "default_lane": "main"},
            {"version": 1, "wave0": [], "lanes": {}},
            {"version": 1, "wave0": "health", "lanes": {}, "default_lane": "main"},
            {"version": 1, "wave0": [], "lanes": {"lane2": ["a", "a"]}, "default_lane": "main"},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self._load(data)

    def test_refuse_missing_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "读不了分片文件"):
            shard_assign.load("/nonexistent/shards.json")

    def test_repo_file_covers_catalog(self) -> None:
        # 有私有 shards.json 就测它，公开树里没有，测 shards.example.json。
        names = catalog.known_names()
        table = shard_assign.default_table()
        assign = shard_assign.load(table)
        notes: list[str] = []
        mapping = {label: label for label in assign.labels()}
        got = shard_assign.plan(assign, names, mapping, notes)
        self.assertEqual(notes, [])
        self.assertEqual(shard_assign.completeness(got, names), [])
        self.assertEqual(list(assign.wave0), shard_assign.wave0_of(list(names)))
        text = table.read_text(encoding="utf-8")
        self.assertNotIn("/", text)
        required = {label: label for label in assign.labels() - assign.optional()}
        two = shard_assign.plan(assign, names, required, notes)
        self.assertEqual(notes, [])
        self.assertEqual(shard_assign.completeness(two, names), [])
        for item in assign.classes.values():
            present = [label for label in item.lanes if label in mapping]
            for label in present:
                self.assertLessEqual(set(item.everywhere), set(got[label]))

    def test_default_table_falls_back_to_example(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing, example = Path(tmp) / "shards.json", Path(tmp) / "shards.example.json"
            example.write_bytes(shard_assign.EXAMPLE_FILE.read_bytes())
            with mock.patch.multiple(shard_assign, DEFAULT_FILE=missing, EXAMPLE_FILE=example):
                self.assertEqual(shard_assign.default_table(), example)
                assign = shard_assign.load()
        self.assertEqual((assign.default_lane, assign.lanes), ("main", {"main": ()}))
        self.assertEqual(shard_assign.plan(assign, [*HEAD, "a"], {"main": "甲"})["甲"], [*HEAD, "a"])


def _classed(lane3: tuple[str, ...] = (), everywhere: tuple[str, ...] = ("c",)) -> shard_assign.Assign:
    lanes = {"lane2": ("b",), "lane3": lane3}
    classes = {"opts": shard_assign.LaneClass(("lane3",), everywhere)}
    return shard_assign.Assign(tuple(HEAD), lanes, "main", classes)


class LaneClassTest(unittest.TestCase):
    def test_two_lane_invocation_unchanged(self) -> None:
        got = shard_assign.plan_detail(_classed(lane3=("d",)), CATALOG, LANES)
        self.assertEqual(got.cases, {"甲": [*HEAD, "a", "c", "d"], "乙": [*HEAD, "b"]})
        self.assertEqual(got.owner, {"a": "甲", "b": "乙", "c": "甲", "d": "甲"})

    def test_everywhere_runs_on_present_class_lanes(self) -> None:
        lanes = {"main": "甲", "lane2": "乙", "lane3": "丙"}
        got = shard_assign.plan_detail(_classed(lane3=("d",)), CATALOG, lanes)
        self.assertEqual(got.cases["丙"], [*HEAD, "c", "d"])
        self.assertEqual(got.cases["甲"], [*HEAD, "a", "c"])
        self.assertEqual(got.owner["d"], "丙")
        self.assertEqual(got.owner["c"], "甲")

    def test_everywhere_owner_on_class_lane_not_duplicated(self) -> None:
        lanes = {"main": "甲", "lane2": "乙", "lane3": "丙"}
        got = shard_assign.plan(_classed(lane3=("c",)), CATALOG, lanes)
        self.assertEqual(got["丙"], [*HEAD, "c"])
        self.assertNotIn("c", got["甲"])

    def test_class_everywhere_unknown_warns_wave0_refused(self) -> None:
        notes: list[str] = []
        shard_assign.plan(_classed(everywhere=("c", "gone")), CATALOG, LANES, notes)
        self.assertTrue(any("lane_classes" in line and "gone" in line for line in notes))
        with self.assertRaisesRegex(ValueError, "第 0 波"):
            shard_assign.plan(_classed(everywhere=("login",)), CATALOG, LANES)

    def test_unknown_label_still_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "没有标签 other"):
            shard_assign.plan(_classed(), CATALOG, {**LANES, "other": "戊"})

    def test_load_v2_and_refuse_bad_classes(self) -> None:
        base = {"version": 2, "wave0": [], "lanes": {"lane2": []}, "default_lane": "main"}
        good = dict(base, lane_classes={"opts": {"lanes": ["lane3"], "everywhere": ["a"]}})
        got = shard_assign.from_dict(good)
        self.assertEqual(got.classes["opts"], shard_assign.LaneClass(("lane3",), ("a",)))
        self.assertEqual(shard_assign.from_dict(base).classes, {})
        bad = [
            dict(base, version=1, lane_classes={}),
            dict(base, lane_classes=[]),
            dict(base, lane_classes={"opts": {"lanes": []}}),
            dict(base, lane_classes={"opts": {"lanes": ["main"]}}),
            dict(base, lane_classes={"a": {"lanes": ["p1"]}, "b": {"lanes": ["p1"]}}),
            dict(base, lane_classes={"opts": {"lanes": ["p1"], "everywhere": ["a", "a"]}}),
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                shard_assign.from_dict(data)


def _run(statuses: dict[str, str], durations: dict[str, int] | None = None, fixed_ms: int = 0) -> sr.Run:
    return sr.Run(statuses, durations or {}, fixed_ms)


ALL_PASS = {name: "PASS" for name in CATALOG}


class RecomputeTest(unittest.TestCase):
    def test_lane2_only_pass_goes_lane2_rest_main(self) -> None:
        lane2 = _run({"health": "PASS", "login": "PASS", "a": "PASS", "b": "SKIP", "c": "N/A", "d": "PASS"})
        main = _run({"a": "FAIL", "b": "PASS", "c": "PASS", "d": "SKIP"})
        got = sr.recompute({"lane2": lane2, "main": main}, CATALOG, _assign())
        self.assertEqual(got["wave0"], HEAD)
        self.assertEqual(got["lanes"], {"main": ["b", "c"], "lane2": ["a", "d"]})
        self.assertEqual(got["default_lane"], "main")
        self.assertEqual(got["version"], 1)
        self.assertNotIn("lane_classes", got)

    def test_recompute_without_old_uses_first_label(self) -> None:
        lane2 = _run({"a": "PASS", "b": "SKIP", "c": "N/A", "d": "PASS"})
        main = _run({"a": "FAIL", "b": "PASS", "c": "PASS", "d": "SKIP"})
        got = sr.recompute({"lane2": lane2, "main": main}, CATALOG)
        self.assertEqual(got["default_lane"], "lane2")
        self.assertEqual(got["lanes"]["lane2"], ["a", "d"])
        self.assertEqual(got["lanes"]["main"], ["b", "c"])

    def test_both_pass_balanced_longest_first(self) -> None:
        cost = {"a": 50, "b": 40, "c": 30, "d": 20}
        runs = {"lane2": _run(ALL_PASS, cost, fixed_ms=100), "main": _run(ALL_PASS, cost)}
        got = sr.recompute(runs, CATALOG, _assign())
        self.assertEqual(got["lanes"], {"main": ["a", "b", "c"], "lane2": ["d"]})
        self.assertEqual(sr.predict(got, runs), {"lane2": 120, "main": 120})

    def test_lane_specific_duration_used(self) -> None:
        runs = {"lane2": _run(ALL_PASS, {"a": 10, "b": 10, "c": 10, "d": 10}), "main": _run(ALL_PASS, {"a": 100, "b": 100})}
        got = sr.recompute(runs, CATALOG, _assign())
        self.assertEqual(got["lanes"], {"main": ["c", "d"], "lane2": ["a", "b"]})
        self.assertEqual(sr.predict(got, runs), {"lane2": 20, "main": 20})

    def test_wave0_counts_on_every_lane(self) -> None:
        runs = {"lane2": _run(ALL_PASS, {"login": 7, "a": 1}), "main": _run({"a": "N/A", "b": "N/A", "c": "N/A", "d": "N/A"}, {"login": 9})}
        got = sr.recompute(runs, CATALOG, _assign())
        self.assertEqual(got["lanes"]["main"], [])
        self.assertEqual(sr.predict(got, runs), {"lane2": 8, "main": 9})

    def test_partial_report_refused_for_any_lane(self) -> None:
        with self.assertRaisesRegex(ValueError, "lane2 报告没覆盖登记表，缺 2 条：c,d"):
            sr.recompute({"lane2": _run({"a": "PASS", "b": "PASS"}), "main": _run(ALL_PASS)}, CATALOG, _assign())
        with self.assertRaisesRegex(ValueError, "main 报告没覆盖登记表，缺 4 条"):
            sr.recompute({"lane2": _run(ALL_PASS), "main": _run({})}, CATALOG, _assign())

    def test_partial_report_allowed(self) -> None:
        got = sr.recompute({"lane2": _run({"a": "PASS", "b": "SKIP"}), "main": _run({})}, CATALOG, _assign(), allow_partial=True)
        self.assertEqual(got["lanes"], {"main": ["b", "c", "d"], "lane2": ["a"]})

    def test_labels_checked_against_old_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "没有标签 lane3"):
            sr.recompute({"lane2": _run(ALL_PASS), "main": _run(ALL_PASS), "lane3": _run(ALL_PASS)}, CATALOG, _assign())
        with self.assertRaisesRegex(ValueError, "缺标签 lane2 的报告"):
            sr.recompute({"main": _run(ALL_PASS)}, CATALOG, _assign())
        got = sr.recompute({"lane2": _run(ALL_PASS), "main": _run(ALL_PASS)}, CATALOG, _classed())
        self.assertEqual(got["version"], 2)
        self.assertEqual(set(got["lanes"]), {"main", "lane2"})

    def test_class_lane_balance_and_everywhere(self) -> None:
        old = _classed()
        cost = {"a": 40, "b": 30, "c": 50, "d": 20}
        runs = {
            "main": _run(ALL_PASS, cost),
            "lane2": _run(dict(ALL_PASS, a="N/A", c="N/A"), cost),
            "lane3": _run(ALL_PASS, cost),
        }
        got = sr.recompute(runs, CATALOG, old)
        self.assertEqual(got["lane_classes"], {"opts": {"lanes": ["lane3"], "everywhere": ["c"]}})
        owner = {name: label for label, names in got["lanes"].items() for name in names}
        self.assertEqual(owner["c"], "main")
        self.assertEqual(set(owner), {"a", "b", "c", "d"})
        self.assertEqual(sr.predict(got, runs), {"main": 50, "lane2": 50, "lane3": 90})
        self.assertEqual(got["lanes"]["lane2"], ["b", "d"])
        shard_assign.plan(shard_assign.from_dict(got), CATALOG, {"main": "甲", "lane2": "乙"})

    def test_report_without_tests_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _report(Path(tmp) / "report.json", {})
            with self.assertRaisesRegex(ValueError, "没有用例结果"):
                sr.report_statuses(path)

    def test_read_report_durations_and_fixed(self) -> None:
        phases = {"prep": {"ms": 100}, "deploy": {"ms": 20}, "cases": {"ms": 999}, "revert": {"ms": 30}, "x": "bad"}
        tests = [{"case": "a", "status": "PASS", "duration_ms": 5}, {"case": "b", "status": "FAIL"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps({"tests": tests, "phases": phases}), encoding="utf-8")
            got = sr.read_report(path)
        self.assertEqual(got.fixed_ms, 150)
        self.assertEqual(got.durations, {"a": 5, "b": 0})
        self.assertEqual(got.statuses, {"a": "PASS", "b": "FAIL"})

    def test_summary_lists_moves_weak_and_totals(self) -> None:
        old = _assign(lane2=("b", "c"))
        runs = {"lane2": _run({"a": "PASS", "b": "PASS", "x": "PASS"}, {"a": 60000}, fixed_ms=60000), "main": _run({"c": "PASS"})}
        new = sr.recompute(runs, CATALOG, old, allow_partial=True)
        text = "\n".join(sr.summary(old, new, runs, CATALOG))
        self.assertIn("lane2 线新进 1 条：a；移出 1 条：c", text)
        self.assertIn("main 线里 main 报告不是 PASS 的：d", text)
        self.assertIn("登记表外的用例（已忽略）：x", text)
        self.assertIn("lane2 线归属 2 条；预计 2.0 分钟（固定 1.0 分钟）", text)


def _pinned(pinned: dict | None = None, tail: dict | None = None, lane2: tuple[str, ...] = ("b", "d"), main: tuple[str, ...] = ("a",)) -> shard_assign.Assign:
    return shard_assign.Assign(tuple(HEAD), {"main": main, "lane2": lane2}, "main", {}, pinned or {}, tail or {})


class PinTest(unittest.TestCase):
    def test_load_pins_and_refuse_bad(self) -> None:
        base = {"version": 2, "wave0": HEAD, "lanes": {"main": ["a"], "lane2": ["b", "d"]}, "default_lane": "main"}
        got = shard_assign.from_dict(dict(base, pinned={"lane2": ["b"], "main": ["a"]}, tail={"lane2": ["d"]}))
        self.assertEqual(got.pinned, {"lane2": ("b",), "main": ("a",)})
        self.assertEqual(got.fixed(), {"b": "lane2", "a": "main", "d": "lane2"})
        classed = dict(base, lanes={"lane2": ["b"], "lane3": ["c"]}, lane_classes={"opts": {"lanes": ["lane3"]}})
        bad = {
            "第 1 版": dict(base, version=1, pinned={}),
            "必须是对象": dict(base, pinned=[]),
            "没列在 lanes.lane2": dict(base, pinned={"lane2": ["a"]}),
            "必到线": dict(base, pinned={"other": ["a"]}),
            "同时固定": dict(base, pinned={"lane2": ["d"]}, tail={"lane2": ["d"]}),
            "最后几条": dict(base, tail={"lane2": ["b"]}),
        }
        for text, data in bad.items():
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, text):
                shard_assign.from_dict(data)
        with self.assertRaisesRegex(ValueError, "必到线"):
            shard_assign.from_dict(dict(classed, pinned={"lane3": ["c"]}))

    def test_load_known_na_and_refuse_bad(self) -> None:
        base = {"version": 2, "wave0": HEAD, "lanes": {"main": ["a"], "lane2": ["b", "d"]}, "default_lane": "main"}
        got = shard_assign.from_dict(dict(base, known_na={"lane2": {"b": "U8 不支持"}}))
        self.assertEqual(got.known_na, {"lane2": {"b": "U8 不支持"}})
        self.assertEqual(shard_assign.from_dict(base).known_na, {})
        bad = {
            "第 1 版": dict(base, version=1, known_na={}),
            "必须是": dict(base, known_na={"lane2": ["b"]}),
            "写明原因": dict(base, known_na={"lane2": {"b": " "}}),
            "没有的线": dict(base, known_na={"other": {"b": "U8 不支持"}}),
        }
        for text, data in bad.items():
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, text):
                shard_assign.from_dict(data)

    def test_recompute_keeps_known_na(self) -> None:
        runs = {"main": _run(ALL_PASS), "lane2": _run(dict(ALL_PASS, b="N/A"))}
        old = shard_assign.Assign(tuple(HEAD), {"main": ("a",), "lane2": ("b", "d")}, "main", known_na={"lane2": {"b": "原因", "gone": "原因"}})
        got = sr.recompute(runs, CATALOG, old)
        self.assertEqual(got["known_na"], {"lane2": {"b": "原因"}})
        self.assertEqual(got["version"], 2)
        self.assertEqual(shard_assign.from_dict(got).known_na, {"lane2": {"b": "原因"}})

    @unittest.skipUnless(shard_assign.DEFAULT_FILE.is_file(), "没有私有 shards.json")
    def test_repo_file_known_na_matches_pins(self) -> None:
        assign = shard_assign.load(shard_assign.DEFAULT_FILE)
        self.assertTrue(assign.known_na)
        for label, known in assign.known_na.items():
            pinned = set(assign.pinned.get(label, ()))
            self.assertLessEqual(set(known), pinned)

    def test_tail_not_last_in_catalog_warns(self) -> None:
        notes: list[str] = []
        shard_assign.plan(_pinned(tail={"lane2": ("d",)}), CATALOG, LANES, notes)
        self.assertEqual(notes, [])
        shard_assign.plan(_pinned(tail={"lane2": ("b",)}, lane2=("d", "b")), CATALOG, LANES, notes)
        self.assertTrue(any("收尾用例" in line and "b" in line for line in notes))

    def test_recompute_keeps_pins_and_tail_last(self) -> None:
        cost = {"a": 10, "b": 10, "c": 10, "d": 10}
        runs = {"main": _run(ALL_PASS, cost), "lane2": _run(dict(ALL_PASS, b="FAIL", d="N/A"), cost)}
        old = _pinned(pinned={"lane2": ("b",), "main": ("gone",)}, tail={"lane2": ("d",)}, lane2=("b", "c", "d"), main=("a", "gone"))
        got = sr.recompute(runs, CATALOG, old)
        self.assertEqual(got["lanes"]["lane2"][-1], "d")
        self.assertIn("b", got["lanes"]["lane2"])
        self.assertEqual(got["pinned"], {"lane2": ["b"]})
        self.assertEqual(got["tail"], {"lane2": ["d"]})
        self.assertEqual(got["version"], 2)
        self.assertEqual(sorted(got["lanes"]["main"] + got["lanes"]["lane2"]), ["a", "b", "c", "d"])
        text = "\n".join(sr.summary(old, got, runs, CATALOG))
        self.assertIn("pinned.lane2 固定 1 条（不重分）；本线报告不是 PASS 的：b", text)
        self.assertIn("tail.lane2 固定 1 条（不重分）；本线报告不是 PASS 的：d", text)
        self.assertIn("已去掉：gone", text)
        shard_assign.plan(shard_assign.from_dict(got), CATALOG, LANES)

    @unittest.skipUnless(shard_assign.DEFAULT_FILE.is_file(), "没有私有 shards.json")
    def test_repo_file_pins_survive_recompute(self) -> None:
        assign = shard_assign.load(shard_assign.DEFAULT_FILE)
        names = list(catalog.known_names())
        self.assertTrue(assign.tail and assign.pinned)
        slow = dict.fromkeys(names, 1000)
        required = sorted(assign.labels() - assign.optional())
        fast = sorted(assign.optional())[0]
        runs = {label: _run(dict.fromkeys(names, "PASS"), slow) for label in required}
        runs[fast] = _run(dict.fromkeys(names, "PASS"), dict.fromkeys(names, 1))
        got = sr.recompute(runs, names, assign)
        for name, label in assign.fixed().items():
            if label in got["lanes"]:
                self.assertIn(name, got["lanes"][label], name)
        for label, last in assign.tail.items():
            if label in got["lanes"] and last:
                self.assertEqual(got["lanes"][label][-len(last):], list(last))
        notes: list[str] = []
        shard_assign.plan(shard_assign.from_dict(got), names, {label: label for label in runs}, notes)
        self.assertEqual(notes, [])


def _cli(root: Path, *extra: str) -> list[str]:
    return ["recompute", *extra, "--assign", str(root / "shards.json")]


class RecomputeCliTest(unittest.TestCase):
    def _main(self, argv: list[str]) -> tuple[int, str, str]:
        with mock.patch.object(catalog, "known_names", return_value=tuple(CATALOG)):
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                code = shard_assign.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_cli_dry_then_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lane2 = _report(root / "lane2.json", {"health": "PASS", "a": "PASS", "b": "N/A", "c": "FAIL", "d": "SKIP"})
            main = _report(root / "main.json", {"health": "PASS", "a": "N/A", "b": "N/A", "c": "PASS", "d": "N/A"})
            argv = _cli(root, "--report", "main=" + str(main), "--report", "lane2=" + str(lane2))
            code, out, _ = self._main(argv)
            self.assertEqual(code, 0)
            self.assertFalse((root / "shards.json").exists())
            self.assertIn("未写入", out)
            self.assertEqual(self._main([*argv, "--write"])[0], 0)
            got = shard_assign.load(root / "shards.json")
        self.assertEqual(got.lanes["lane2"], ("a",))
        self.assertEqual(got.default_lane, "main")

    def test_cli_report_pairs_keep_classes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            classes = {"opts": {"lanes": ["lane3"], "everywhere": ["c"]}}
            doc = {"version": 2, "wave0": HEAD, "lanes": {"lane2": []}, "default_lane": "main", "lane_classes": classes}
            (root / "shards.json").write_text(json.dumps(doc), encoding="utf-8")
            paths = {label: str(_report(root / (label + ".json"), dict(ALL_PASS))) for label in ("main", "lane2", "lane3")}
            argv = _cli(root, *[f"--report={label}={path}" for label, path in paths.items()], "--write")
            self.assertEqual(self._main(argv)[0], 0)
            got = shard_assign.load(root / "shards.json")
            self.assertEqual(got.classes["opts"].everywhere, ("c",))
            self.assertNotIn("c", got.lanes["lane3"])
            twice = self._main([*argv, "--report", "main=" + paths["main"]])
        self.assertEqual(twice[0], 2)
        self.assertIn("两份报告", twice[2])

    def test_cli_partial_needs_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lane2 = _report(root / "lane2.json", {"a": "PASS"})
            main = _report(root / "main.json", {"a": "N/A", "b": "N/A", "c": "PASS", "d": "N/A"})
            argv = _cli(root, "--report", "main=" + str(main), "--report", "lane2=" + str(lane2))
            code, _, err = self._main(argv)
            self.assertEqual(code, 2)
            self.assertIn("--allow-partial", err)
            self.assertEqual(self._main([*argv, "--allow-partial", "--write"])[0], 0)
            self.assertEqual(shard_assign.load(root / "shards.json").lanes["lane2"], ("a",))

    def test_cli_bad_report_exit_2(self) -> None:
        code, _, err = self._main(["recompute", "--report", "main=/nonexistent/a.json"])
        self.assertEqual(code, 2)
        self.assertIn("读不了报告", err)
        code, _, err = self._main(["recompute"])
        self.assertEqual(code, 2)
        self.assertIn("--report", err)


if __name__ == "__main__":
    unittest.main()
