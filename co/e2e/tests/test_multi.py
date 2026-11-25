"""多通道分片回归 co/e2e/multi.py：分片、子进程命令行、报告查找、合并与判定。不起子进程，不连服务器。"""

from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from co.e2e import multi
from co.e2e import multi_report as mr
from co.e2e import settings
from co.e2e.settings import ENV_PATH
from co.e2e.tests import support

_WAVE0 = ["health", "bad-signature", "account-foreign", "login"]
_CATALOG = tuple(_WAVE0 + ["dispatch-verify", "arrival-verify", "product-in-update", "report-gl-balance"])
_EXAMPLE = Path(__file__).resolve().parents[1] / "shards.example.json"


def _test(case: str, status: str = "PASS") -> dict:
    return {"name": case, "status": status, "detail": "", "case": case, "duration_ms": 10}


def _report(cases: list[str], **changes: object) -> dict:
    body = {"overall": "PASS", "rollback_verified": True, "tests": [_test(case) for case in cases]}
    body.update(changes)
    return body


def _result(label: str, report: dict | None, code: int | None = 0) -> mr.LaneResult:
    account = {"main": "801", "lane2": "802", "lane3": "803"}.get(label, "")
    return mr.LaneResult(label=label, account=account, exit_code=code, report=report, wall_ms=1000, planned=4)


def _ok_pair() -> list[mr.LaneResult]:
    main = _result("main", _report(_WAVE0 + ["report-gl-balance", "arrival-verify"]))
    lane2 = _result("lane2", _report(_WAVE0 + ["dispatch-verify", "product-in-update"]))
    return [main, lane2]


def _write_lane(root: Path, code: str) -> Path:
    data = support.lane_dict(code)
    sample = str(data["account"]["code"])
    if sample != code:
        data = json.loads(json.dumps(data).replace(sample, code))
    port = 18100 + int(code) - 800
    data["report"] = {"dir": str(root / ("reports-" + code))}
    data["bridge"]["port"] = port
    data["bridge"]["url"] = f"http://192.0.2.10:{port}/u8co"
    path = root / (code + ".json")
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class LaneArgTests(unittest.TestCase):
    def test_parse_lane(self) -> None:
        label, path = multi.parse_lane("main=/nonexistent/a.json")
        self.assertEqual(label, "main")
        self.assertEqual(path, Path("/nonexistent/a.json"))

    def test_parse_lane_refuses_bad_forms(self) -> None:
        for text in ("main", "=a.json", "Main=a.json", "m=a.json", "main="):
            with self.subTest(text=text), self.assertRaises(multi.UsageError):
                multi.parse_lane(text)

    def test_load_lanes_two_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items = multi.load_lanes(["main=" + str(_write_lane(root, "802")), "lane2=" + str(_write_lane(root, "803"))])
        self.assertEqual([lane.label for lane in items], ["main", "lane2"])
        self.assertEqual([lane.cfg.account for lane in items], ["802", "803"])

    def test_load_lanes_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            items = multi.load_lanes(["main=" + str(_write_lane(Path(tmp), "802"))])
        self.assertEqual([lane.label for lane in items], ["main"])
        self.assertEqual(multi.lane_clashes(items), [])

    def test_load_lanes_refuses_duplicate_label_and_same_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(_write_lane(Path(tmp), "802"))
            with self.assertRaises(multi.UsageError):
                multi.load_lanes(["main=" + path, "main=" + path])
            with self.assertRaises(multi.UsageError) as caught:
                multi.load_lanes(["main=" + path, "lane2=" + path])
        self.assertIn("撞名", str(caught.exception))

    def test_load_lanes_refuses_missing_config(self) -> None:
        with self.assertRaises(multi.UsageError):
            multi.load_lanes(["main=/nonexistent/u8co-lane.json"])
        with self.assertRaises(multi.UsageError):
            multi.load_lanes([])


class HostTests(unittest.TestCase):
    def _lane(self, label: str, code: str, host: str) -> multi.Lane:
        data = support.lane_dict(code)
        sample = str(data["account"]["code"])
        if sample != code:
            data = json.loads(json.dumps(data).replace(sample, code))
        port = 18100 + int(code) - 800
        data["server"]["host"] = host
        data["bridge"]["port"] = port
        data["bridge"]["url"] = f"http://{host}:{port}/u8co"
        return multi.Lane(label, Path("/nonexistent/" + code + ".json"), settings.from_dict(data))

    def test_alias_of_same_server_checks_full_fingerprint(self) -> None:
        def fake(host, port):
            return [(2, 1, 6, "", ("198.51.100.10", 0))]

        left = self._lane("main", "802", "u8srv.example.com")
        right = self._lane("lane2", "802", "u8srv")
        with mock.patch.object(multi.socket, "getaddrinfo", side_effect=fake):
            problems = multi.lane_clashes([left, right])
        self.assertTrue(any("account" in item for item in problems))

    def test_unresolvable_hosts_still_refuse_same_account_and_suffix(self) -> None:
        left = self._lane("main", "802", "u8srv-a.example.com")
        right = self._lane("lane2", "802", "u8srv-b.example.com")
        with mock.patch.object(multi.socket, "getaddrinfo", side_effect=OSError("no dns")):
            problems = multi.lane_clashes([left, right])
            self.assertFalse(multi.same_server("u8srv-a.example.com", "u8srv-b.example.com"))
        self.assertTrue(any("account、snapshot_suffix" in item for item in problems))


class PlanTests(unittest.TestCase):
    def _assign(self, root: Path, lanes: dict) -> Path:
        path = root / "shards.json"
        doc = {"version": 1, "wave0": _WAVE0, "lanes": lanes, "default_lane": "main"}
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def test_plan_wave0_everywhere_rest_disjoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._assign(Path(tmp), {"lane2": ["dispatch-verify", "product-in-update"], "main": ["report-gl-balance"]})
            planned, default_lane, owners, known_na = multi.plan_cases(path, ["main", "lane2"], _CATALOG)
        self.assertEqual(default_lane, "main")
        self.assertEqual(known_na, {})
        self.assertEqual(owners["dispatch-verify"], "lane2")
        self.assertEqual(owners["arrival-verify"], "main")
        for label in ("main", "lane2"):
            self.assertEqual({name for name in planned[label] if name in _WAVE0}, set(_WAVE0))
        rest_main = set(planned["main"]) - set(_WAVE0)
        rest_lane2 = set(planned["lane2"]) - set(_WAVE0)
        self.assertFalse(rest_main & rest_lane2)
        self.assertEqual(rest_main | rest_lane2 | set(_WAVE0), set(_CATALOG))
        self.assertIn("arrival-verify", planned["main"])

    def test_plan_refuses_case_in_two_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._assign(Path(tmp), {"lane2": ["dispatch-verify"], "main": ["dispatch-verify"]})
            with self.assertRaises(multi.UsageError):
                multi.plan_cases(path, ["main", "lane2"], _CATALOG)

    def test_plan_class_lanes_optional_and_everywhere(self) -> None:
        classes = {"opts": {"lanes": ["lane3"], "everywhere": ["report-gl-balance"]}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shards.json"
            doc = {"version": 2, "wave0": _WAVE0, "lanes": {"lane2": ["dispatch-verify"], "lane3": ["product-in-update"]}}
            path.write_text(json.dumps(dict(doc, default_lane="main", lane_classes=classes)), encoding="utf-8")
            two, _, two_owner, _ = multi.plan_cases(path, ["main", "lane2"], _CATALOG)
            three, _, owners, _ = multi.plan_cases(path, ["main", "lane2", "lane3"], _CATALOG)
        self.assertEqual(two["main"], _WAVE0 + ["arrival-verify", "product-in-update", "report-gl-balance"])
        self.assertEqual(two_owner["product-in-update"], "main")
        self.assertEqual(three["lane3"], _WAVE0 + ["product-in-update", "report-gl-balance"])
        self.assertEqual(owners["report-gl-balance"], "main")
        self.assertEqual(owners["product-in-update"], "lane3")

    def test_plan_returns_known_na(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shards.json"
            doc = {"version": 2, "wave0": _WAVE0, "lanes": {"lane2": ["dispatch-verify"]}, "default_lane": "main"}
            path.write_text(json.dumps(dict(doc, known_na={"lane2": {"dispatch-verify": "U8 不支持"}})), encoding="utf-8")
            _, _, _, known_na = multi.plan_cases(path, ["main", "lane2"], _CATALOG)
        self.assertEqual(known_na, {"lane2": {"dispatch-verify": "U8 不支持"}})

    def test_plan_refuses_missing_file(self) -> None:
        with self.assertRaises(multi.UsageError):
            multi.plan_cases(Path("/nonexistent/shards.json"), ["main"], _CATALOG)

    def test_example_table_single_lane_plan(self) -> None:
        planned, default_lane, owners, known_na = multi.plan_cases(_EXAMPLE, ["main"], _CATALOG)
        self.assertEqual(default_lane, "main")
        self.assertEqual(known_na, {})
        self.assertEqual(planned["main"], list(_CATALOG))
        self.assertEqual(set(owners), set(_CATALOG) - set(_WAVE0))
        self.assertEqual(set(owners.values()), {"main"})


class CommandTests(unittest.TestCase):
    def test_minimal_command(self) -> None:
        cmd = multi.child_command(["health", "login"], multi.Options())
        self.assertEqual(cmd, [sys.executable, "-m", "co.e2e.run_e2e", "--cases", "health,login"])

    def test_passthrough_flags(self) -> None:
        opts = multi.Options(dry_run=True, allow_daytime=True, watchdog_min=40, force_deploy=True)
        cmd = multi.child_command(["health"], opts)
        self.assertEqual(cmd[3:], ["--cases", "health", "--dry-run", "--watchdog-min", "40", "--force-deploy", "--allow-daytime"])

    def test_env_points_at_lane_config(self) -> None:
        cfg = support.lane_config("802")
        lane = multi.Lane("main", Path("/nonexistent/802.json"), cfg)
        env = multi.child_env(lane, {"PATH": "/usr/bin", ENV_PATH: "/nonexistent/other.json"})
        self.assertEqual(env[ENV_PATH], "/nonexistent/802.json")
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")


class FindReportTests(unittest.TestCase):
    def test_printed_path_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "20261001-120000-00-abcdef"
            run.mkdir()
            (run / "report.json").write_text("{}", encoding="utf-8")
            found, _ = multi.find_report(["...", str(run), "PASS"], root, time.time() + 3600)
            relative, _ = multi.find_report([os.path.relpath(run, multi._ROOT)], root.resolve(), time.time() + 3600)
        self.assertEqual(found, (run / "report.json").resolve())
        self.assertEqual(relative, (run / "report.json").resolve())

    def test_fallback_newest_after_start_skips_multi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("20261001-120000-00-aaaaaa", "multi-20261001-120000"):
                (root / name).mkdir()
                (root / name / "report.json").write_text("{}", encoding="utf-8")
            found, _ = multi.find_report(["没有路径"], root, time.time() - 60)
            stale, note = multi.find_report([], root, time.time() + 3600)
            (root / "20261001-120007-00-bbbbbb").mkdir()
            (root / "20261001-120007-00-bbbbbb" / "report.json").write_text("{}", encoding="utf-8")
            twice, twice_note = multi.find_report([], root, time.time() - 60)
        self.assertEqual(found, root / "20261001-120000-00-aaaaaa" / "report.json")
        self.assertIsNone(stale)
        self.assertIn("没有找到", note)
        self.assertIsNone(twice)
        self.assertIn("2 份", twice_note)

    def test_read_report_tolerates_garbage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(multi.read_report(path))
        self.assertIsNone(multi.read_report(None))


class ChildTests(unittest.TestCase):
    def test_drain_reads_new_lines_and_keeps_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "multi.log"
            log.write_bytes("第一行\n第二".encode())
            child = multi.Child(support_lane(), ["health"], log)
            multi.drain(child)
            self.assertEqual(child.tail, ["第一行"])
            with log.open("ab") as out:
                out.write("行\r\n/abs/report\n".encode())
            multi.drain(child)
        self.assertEqual(child.tail, ["第一行", "第二行", "/abs/report"])
        self.assertEqual(child.lines, 3)

    def test_forwarder_signals_once(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        done = mock.Mock()
        done.poll.return_value = 0
        children = [multi.Child(support_lane(), ["health"], Path("/nonexistent/a.log"), proc=proc)]
        children.append(multi.Child(support_lane(), ["health"], Path("/nonexistent/b.log"), proc=done))
        forward = multi.Forwarder(children)
        with mock.patch("builtins.print"):
            forward(signal.SIGINT, None)
            forward(signal.SIGINT, None)
        proc.send_signal.assert_called_once_with(signal.SIGINT)
        done.send_signal.assert_not_called()
        self.assertEqual(forward.hits, 2)

    def test_collect_not_started(self) -> None:
        child = multi.Child(support_lane(), ["health"], Path("/nonexistent/a.log"), note="收到中断，未启动")
        result = multi.collect(child)
        self.assertIsNone(result.report)
        self.assertIn("未启动", mr.lane_reasons(result, False)[0])


def support_lane() -> multi.Lane:
    return multi.Lane("main", Path("/nonexistent/802.json"), support.lane_config("802"))


class MergeTests(unittest.TestCase):
    def test_na_on_non_default_lane_is_warning_only(self) -> None:
        main, lane2 = _ok_pair()
        tests = [_test(case, "N/A" if case == "product-in-update" else "PASS") for case in _WAVE0 + ["dispatch-verify", "product-in-update"]]
        lane2 = _result("lane2", _report([], tests=tests))
        main_na = _result("main", _report([], tests=[_test(case, "N/A") for case in _WAVE0 + ["report-gl-balance", "arrival-verify"]]))
        merged = mr.merge([main_na, lane2], _CATALOG, False, "main")
        self.assertEqual(merged["overall"], "PASS", merged["reasons"])
        self.assertEqual(len(merged["warnings"]), 1)
        self.assertIn("lane2", merged["warnings"][0])
        self.assertIn("product-in-update", merged["warnings"][0])
        self.assertIn("## 警告", mr.render_markdown(merged))

    def test_known_na_not_warned(self) -> None:
        main, _ = _ok_pair()
        names = ["dispatch-verify", "product-in-update"]
        lane2 = _result("lane2", _report([], tests=[_test(case, "N/A" if case in names else "PASS") for case in _WAVE0 + names]))
        owners = {"report-gl-balance": "main", "arrival-verify": "main", "dispatch-verify": "lane2", "product-in-update": "lane2"}
        known = {"lane2": {"product-in-update": "U8 不支持"}, "main": {"dispatch-verify": "只认 main 上登记的"}}
        merged = mr.merge([main, lane2], _CATALOG, False, "main", mr.warn_owners(owners, known))
        self.assertEqual(len(merged["warnings"]), 1)
        self.assertIn("dispatch-verify", merged["warnings"][0])
        self.assertNotIn("product-in-update", merged["warnings"][0])
        known["lane2"]["dispatch-verify"] = "U8 不支持"
        self.assertEqual(mr.merge([main, lane2], _CATALOG, False, "main", mr.warn_owners(owners, known))["warnings"], [])

    def test_everywhere_copies_judged_and_na_only_warned_for_owner(self) -> None:
        main, lane2 = _ok_pair()
        tests = [_test(case, "N/A" if case == "report-gl-balance" else "PASS") for case in _WAVE0 + ["report-gl-balance"]]
        extra = _result("lane3", _report([], tests=tests))
        owners = {"report-gl-balance": "main", "arrival-verify": "main", "dispatch-verify": "lane2", "product-in-update": "lane2"}
        merged = mr.merge([main, lane2, extra], _CATALOG, False, "main", owners)
        self.assertEqual(merged["overall"], "PASS", merged["reasons"])
        self.assertEqual(merged["warnings"], [])
        self.assertEqual(merged["spread"], {"report-gl-balance": {"main": "PASS", "lane3": "N/A"}})
        self.assertIn("| report-gl-balance | main PASS；lane3 N/A |", mr.render_markdown(merged))
        failed = _result("lane3", _report([], tests=[*tests[:-1], _test("report-gl-balance", "FAIL")]))
        merged = mr.merge([main, lane2, failed], _CATALOG, False, "main", owners)
        self.assertEqual(merged["overall"], "FAIL")
        rolled = _result("lane3", _report([], tests=tests, rollback_verified=None))
        self.assertEqual(mr.merge([main, lane2, rolled], _CATALOG, False, "main", owners)["overall"], "FAIL")

    def test_pass_with_wave0_duplicates(self) -> None:
        merged = mr.merge(_ok_pair(), _CATALOG, False)
        self.assertEqual(merged["overall"], "PASS", merged["reasons"])
        self.assertEqual(merged["missing"], [])
        self.assertEqual(len(merged["tests"]), 12)
        self.assertEqual({item["lane"] for item in merged["tests"]}, {"main", "lane2"})
        self.assertEqual(merged["lanes"][0]["counts"], {"PASS": 6, "SKIP": 0, "N/A": 0, "FAIL": 0})
        self.assertEqual(mr.exit_code(merged["overall"]), 0)

    def test_counts_unknown_status_as_fail(self) -> None:
        tests = [_test("a"), _test("b", "SKIP"), _test("c", "N/A"), _test("d", "FAIL"), _test("e", "ERROR")]
        self.assertEqual(mr.counts(tests), {"PASS": 1, "SKIP": 1, "N/A": 1, "FAIL": 2})

    def test_skip_fails(self) -> None:
        main, lane2 = _ok_pair()
        tests = [_test(case, "SKIP" if case == "dispatch-verify" else "PASS") for case in _WAVE0 + ["dispatch-verify", "product-in-update"]]
        lane2 = _result("lane2", _report([], tests=tests))
        merged = mr.merge([main, lane2], _CATALOG, False)
        self.assertEqual(merged["overall"], "FAIL")
        self.assertTrue(any("SKIP" in item and "dispatch-verify" in item for item in merged["reasons"]))

    def test_rollback_false_fails(self) -> None:
        main, lane2 = _ok_pair()
        main = _result("main", dict(main.report or {}, rollback_verified=False))
        merged = mr.merge([main, lane2], _CATALOG, False)
        self.assertEqual(merged["overall"], "FAIL")
        self.assertFalse(merged["lanes"][0]["rollback_verified"])

    def test_missing_case_fails(self) -> None:
        main = _result("main", _report(_WAVE0 + ["report-gl-balance"]))
        lane2 = _ok_pair()[1]
        merged = mr.merge([main, lane2], _CATALOG, False)
        self.assertEqual(merged["overall"], "FAIL")
        self.assertEqual(list(merged["missing"]), ["arrival-verify"])
        self.assertIn("缺 1 条", mr.completeness_line(merged))

    def test_child_exit_or_verdict_or_no_report_fails(self) -> None:
        main, lane2 = _ok_pair()
        cases = [
            [_result("main", main.report, code=1), lane2],
            [_result("main", dict(main.report or {}, overall="FAIL")), lane2],
            [_result("main", None, code=1), lane2],
        ]
        for results in cases:
            with self.subTest(results=results[0]):
                merged = mr.merge(results, _CATALOG, False)
                self.assertEqual(merged["overall"], "FAIL")
                self.assertEqual(mr.exit_code(merged["overall"]), 1)

    def test_dry_run(self) -> None:
        reports = [_result(label, {"overall": "DRY-RUN", "rollback_verified": False, "tests": []}) for label in ("main", "lane2")]
        merged = mr.merge(reports, _CATALOG, True)
        self.assertEqual(merged["overall"], "DRY-RUN")
        self.assertEqual(mr.exit_code(merged["overall"]), 0)
        failed = mr.merge([reports[0], _result("lane2", None, code=2)], _CATALOG, True)
        self.assertEqual(failed["overall"], "FAIL")

    def test_markdown_has_lane_table_and_lane_column(self) -> None:
        text = mr.render_markdown(mr.merge(_ok_pair(), _CATALOG, False))
        self.assertIn("结论：PASS", text)
        self.assertIn("全部有结果", text)
        self.assertIn("| main | 801 | 0 | PASS | 6 | 0 | 0 | 0 | true |", text)
        self.assertIn("| lane2 | product-in-update |", text)
        self.assertIn("可作完整回归结论", text)


if __name__ == "__main__":
    unittest.main()
