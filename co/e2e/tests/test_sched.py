"""并行调度、每条用例一份 Env、SQL 通道池和命令行参数。不连接服务器，不调用 git。"""

from __future__ import annotations

import io
import sys
import threading
import time
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from co.e2e import remote, run_e2e
from co.e2e.casespec import runner, sched
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.gate import GatedClient
from co.e2e.casespec.sqlexec import run_guarded, slot_name
from co.client.u8co_client import U8CoError
from co.e2e.remote import RemoteResult
from co.e2e.sqlchan import SqlChannel, SqlPool
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _case(name: str, tags: tuple[str, ...] = (), exclusive: bool = False, login: bool = True) -> CaseSpec:
    return CaseSpec(name, "用例 " + name, (), (Step("nope"),), login, tags=frozenset(tags), exclusive=exclusive)


_LOGIN = _case("login", login=False)


def _clash(a: frozenset[str], b: frozenset[str], a_excl: bool, b_excl: bool) -> bool:
    return a_excl or b_excl or bool(a & b)


def _bind(spec: CaseSpec, _bag: object) -> frozenset[str]:
    return spec.tags


class Recorder:
    """记下谁和谁同时在跑。hold 里的用例要等另一条跑完才结束（最多 2 秒），用来制造确定的重叠和完成顺序。"""

    def __init__(self, names: list[str], hold: dict[str, str] | None = None, boom: dict[str, BaseException] | None = None) -> None:
        self.lock = threading.Lock()
        self.active: set[str] = set()
        self.pairs: set[frozenset[str]] = set()
        self.started: list[str] = []
        self.finished: list[str] = []
        self.peak = 0
        self.hold = hold or {}
        self.boom = boom or {}
        self.done = {name: threading.Event() for name in names}

    def __call__(self, spec: CaseSpec) -> tuple[str, str]:
        with self.lock:
            for other in self.active:
                self.pairs.add(frozenset((spec.name, other)))
            self.active.add(spec.name)
            self.started.append(spec.name)
            self.peak = max(self.peak, len(self.active))
        waited = self.hold.get(spec.name)
        if waited:
            self.done[waited].wait(2)
        else:
            time.sleep(0.02)
        with self.lock:
            self.active.discard(spec.name)
            self.finished.append(spec.name)
        self.done[spec.name].set()
        error = self.boom.get(spec.name)
        if error is not None:
            raise error
        return "PASS", spec.name

    def overlapped(self, a: str, b: str) -> bool:
        return frozenset((a, b)) in self.pairs


def _names(specs: list[CaseSpec]) -> list[str]:
    return [spec.name for spec in specs]


class WaveTests(unittest.TestCase):
    def test_conflicting_pair_waits_disjoint_case_overlaps(self) -> None:
        specs = [_LOGIN, _case("a", ("x",)), _case("b", ("x",)), _case("c", ("y",))]
        rec = Recorder(_names(specs), hold={"a": "c"})
        results = sched.run_parallel(specs, rec, jobs=2, conflicts=_clash, bind=_bind)
        self.assertTrue(rec.overlapped("a", "c"))
        self.assertFalse(rec.overlapped("a", "b"))
        self.assertEqual(rec.started[0], "login")
        self.assertEqual(set(results), {"login", "a", "b", "c"})

    def test_results_come_back_in_catalog_order(self) -> None:
        specs = [_LOGIN, _case("a", ("x",)), _case("b", ("x",)), _case("c", ("y",))]
        rec = Recorder(_names(specs), hold={"a": "c"})
        results = sched.run_parallel(specs, rec, jobs=2, conflicts=_clash, bind=_bind)
        # c 比 a 先跑完，结果仍按登记顺序。
        self.assertLess(rec.finished.index("c"), rec.finished.index("a"))
        self.assertEqual(list(results), ["login", "a", "b", "c"])
        self.assertEqual(results["c"], ("PASS", "c"))

    def test_serial_never_overlaps(self) -> None:
        specs = [_LOGIN] + [_case(name, (name,)) for name in ("a", "b", "c", "d")]
        for jobs, conflicts in ((1, _clash), (4, sched.all_conflict)):
            with self.subTest(jobs=jobs):
                rec = Recorder(_names(specs))
                results = sched.run_parallel(specs, rec, jobs=jobs, conflicts=conflicts, bind=_bind)
                self.assertEqual(rec.peak, 1)
                self.assertEqual(rec.started, _names(specs))
                self.assertEqual(list(results), _names(specs))

    def test_exclusive_case_runs_alone(self) -> None:
        specs = [_LOGIN, _case("d", ("d",)), _case("x", ("x",), exclusive=True), _case("e", ("e",)), _case("f", ("f",))]
        rec = Recorder(_names(specs))
        sched.run_parallel(specs, rec, jobs=4, conflicts=_clash, bind=_bind)
        self.assertFalse([pair for pair in rec.pairs if "x" in pair])
        self.assertEqual(set(rec.started), set(_names(specs)))

    def test_exclusive_rule_comes_from_rules(self) -> None:
        # 标签里的 burst 算独占（tags.is_exclusive），spec.exclusive 没置位也一样。
        specs = [_LOGIN, _case("d", ("d",)), _case("x", ("burst",)), _case("e", ("e",))]
        rec = Recorder(_names(specs))
        rules = sched.Rules(_clash, _bind, {}, lambda spec: "burst" in spec.tags)
        sched.run_waves(specs, rec, 4, rules)
        self.assertFalse([pair for pair in rec.pairs if "x" in pair])

    def test_wave2_waits_for_every_other_case(self) -> None:
        # r 带 wave:2，排在前面、资源也不冲突，仍要等 a、b、c 都跑完才开。
        specs = [_LOGIN, _case("r", ("wave:2", "rpt:gl")), _case("a", ("a",)), _case("b", ("b",)), _case("c", ("c",))]
        rec = Recorder(_names(specs))
        results = sched.run_parallel(specs, rec, jobs=4, conflicts=_clash, bind=_bind)
        self.assertEqual(rec.started[-1], "r")
        self.assertFalse([pair for pair in rec.pairs if "r" in pair])
        self.assertEqual(list(results), _names(specs))

    def test_wave0_is_serial_and_runs_first(self) -> None:
        head = [_case("health", login=False), _case("bad-signature", login=False), _LOGIN]
        specs = head + [_case("a", ("a",)), _case("b", ("b",))]
        rec = Recorder(_names(specs))
        sched.run_parallel(specs, rec, jobs=4, conflicts=_clash, bind=_bind)
        self.assertEqual(rec.started[:3], ["health", "bad-signature", "login"])
        self.assertFalse([pair for pair in rec.pairs if pair & {"health", "bad-signature", "login"}])

    def test_split_wave0(self) -> None:
        self.assertEqual(sched.split_wave0([_case("health", login=False), _LOGIN, _case("a")]), 2)
        self.assertEqual(sched.split_wave0([_case("health", login=False), _case("a"), _case("b", login=False)]), 1)
        self.assertEqual(sched.split_wave0([_case("a")]), 0)

    def test_bind_sees_bag_after_wave0(self) -> None:
        bag: dict[str, str] = {}
        seen: list[str] = []

        def one(spec: CaseSpec) -> str:
            if spec.name == "login":
                bag["login"] = "1"
            return spec.name

        def bind(spec: CaseSpec, got: dict[str, str]) -> frozenset[str]:
            seen.append(got.get("login", ""))
            return spec.tags

        sched.run_waves([_LOGIN, _case("a", ("a",))], one, 2, sched.Rules(_clash, bind, bag))
        self.assertEqual(seen, ["1"])


class StopTests(unittest.TestCase):
    def test_interrupt_in_case_stops_new_starts_and_keeps_in_flight(self) -> None:
        # c 与 a 冲突，a 结束前 c 开不了；a 中断后 c 就不再开。
        specs = [_LOGIN, _case("a", ("a",)), _case("b", ("b",)), _case("c", ("a",))]
        rec = Recorder(_names(specs), hold={"a": "b"}, boom={"a": KeyboardInterrupt()})
        with self.assertRaises(sched.Interrupted) as caught:
            sched.run_parallel(specs, rec, jobs=2, conflicts=_clash, bind=_bind)
        self.assertNotIn("c", rec.started)
        self.assertEqual(list(caught.exception.results), ["login", "b"])

    def test_interrupt_on_main_thread_drains_in_flight(self) -> None:
        specs = [_LOGIN, _case("a", ("a",)), _case("b", ("b",)), _case("c", ("c",))]
        rec = Recorder(_names(specs))
        real = sched.wait
        calls = {"n": 0}

        def interrupted_wait(*args: object, **kwargs: object) -> object:
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt
            return real(*args, **kwargs)

        with mock.patch.object(sched, "wait", side_effect=interrupted_wait), self.assertRaises(sched.Interrupted) as caught:
            sched.run_parallel(specs, rec, jobs=2, conflicts=_clash, bind=_bind)
        self.assertEqual(rec.started, ["login", "a", "b"])
        self.assertEqual(list(caught.exception.results), ["login", "a", "b"])

    def test_interrupt_in_wave0(self) -> None:
        specs = [_case("health", login=False), _LOGIN, _case("a")]
        rec = Recorder(_names(specs), boom={"health": KeyboardInterrupt()})
        with self.assertRaises(sched.Interrupted) as caught:
            sched.run_parallel(specs, rec, jobs=4, conflicts=_clash, bind=_bind)
        self.assertEqual(rec.started, ["health"])
        self.assertEqual(caught.exception.results, {})

    def test_crash_waits_for_siblings_then_raises(self) -> None:
        specs = [_LOGIN, _case("a", ("a",)), _case("b", ("b",)), _case("c", ("a",))]
        rec = Recorder(_names(specs), hold={"a": "b"}, boom={"a": ValueError("坏了")})
        with self.assertRaises(sched.CaseCrashed) as caught:
            sched.run_parallel(specs, rec, jobs=2, conflicts=_clash, bind=_bind)
        self.assertIsInstance(caught.exception.cause, ValueError)
        self.assertIn("a ValueError", str(caught.exception))
        self.assertEqual(list(caught.exception.results), ["login", "b"])
        self.assertNotIn("c", rec.started)
        self.assertEqual([name for name, _error in caught.exception.crashes], ["a"])

    def test_interrupt_sets_stop_and_second_interrupt_still_waits(self) -> None:
        specs = [_LOGIN, _case("a", ("a",)), _case("b", ("b",)), _case("c", ("c",))]
        rec = Recorder(_names(specs))
        rules = sched.Rules(_clash, _bind)
        real = sched.wait
        calls = {"n": 0}

        def twice(*args: object, **kwargs: object) -> object:
            calls["n"] += 1
            if calls["n"] <= 2:
                raise KeyboardInterrupt
            return real(*args, **kwargs)

        with mock.patch.object(sched, "wait", side_effect=twice), self.assertRaises(sched.Interrupted) as caught:
            sched.run_waves(specs, rec, 2, rules)
        self.assertTrue(rules.stop.is_set())
        self.assertEqual(list(caught.exception.results), ["login", "a", "b"])
        self.assertEqual(caught.exception.stuck, [])

    def test_cases_still_running_after_drain_limit_are_named(self) -> None:
        specs = [_LOGIN, _case("a", ("a",))]
        rec = Recorder(_names(specs) + ["never"], hold={"a": "never"})
        real = sched.wait
        calls = {"n": 0}

        def once(*args: object, **kwargs: object) -> object:
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt
            return real(*args, **kwargs)

        with (
            mock.patch.object(sched, "DRAIN_SECONDS", 0.05),
            mock.patch.object(sched, "wait", side_effect=once),
            self.assertRaises(sched.Interrupted) as caught,
        ):
            sched.run_parallel(specs, rec, jobs=2, conflicts=_clash, bind=_bind)
        self.assertEqual(caught.exception.stuck, ["a"])


class RulesTests(unittest.TestCase):
    def test_missing_tags_module_means_everything_conflicts(self) -> None:
        # tags.py 已存在；把它在 sys.modules 里置空，模拟模块缺失（import_module 遇到 None 抛 ImportError）。
        with mock.patch.dict(sys.modules, {"co.e2e.casespec.tags": None}):
            conflicts, bind, exclusive = sched.default_rules()
        self.assertIs(conflicts, sched.all_conflict)
        self.assertIs(exclusive, sched.own_flag)
        self.assertTrue(conflicts(frozenset(), frozenset(), False, False))
        self.assertEqual(bind(_case("a", ("x",)), {}), frozenset({"x"}))

    def test_tags_module_supplies_exclusive_rule(self) -> None:
        fake = types.ModuleType("co.e2e.casespec.tags")
        fake.conflicts = _clash  # type: ignore[attr-defined]
        fake.bind_tags = _bind  # type: ignore[attr-defined]
        fake.is_exclusive = lambda spec: "burst" in spec.tags  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"co.e2e.casespec.tags": fake}):
            conflicts, bind, exclusive = sched.default_rules()
        self.assertIs(conflicts, _clash)
        self.assertTrue(exclusive(_case("x", ("burst",))))

    def test_casespec_defaults_keep_old_specs_valid(self) -> None:
        spec = CaseSpec("health", "健康检查", (), (Step("health"),), False)
        self.assertEqual((spec.tags, spec.routes, spec.exclusive), (frozenset(), (), False))


class _Transport:
    def __init__(self) -> None:
        self.closed = False

    def alive(self) -> bool:
        return not self.closed

    def send(self, _line: str) -> str:
        return '{"ok":true,"code":0,"out":"ROW:1"}'

    def close(self) -> None:
        self.closed = True


def _grab(pool: SqlPool, gate: threading.Barrier, out: list[SqlChannel]) -> None:
    gate.wait(2)
    first = pool.channel()
    out.append(first)
    if pool.channel() is not first:
        out.append(None)  # type: ignore[arg-type]
    gate.wait(2)


class PoolTests(unittest.TestCase):
    def _pool(self) -> tuple[SqlPool, list[_Transport]]:
        made: list[_Transport] = []

        def opener() -> SqlChannel:
            made.append(_Transport())
            return SqlChannel(made[-1])

        return SqlPool(opener), made

    def test_threads_get_distinct_channels(self) -> None:
        pool, made = self._pool()
        gate = threading.Barrier(2)
        out: list[SqlChannel] = []
        threads = [threading.Thread(target=_grab, args=(pool, gate, out)) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(len(out), 2)
        self.assertIsNot(out[0], out[1])
        self.assertEqual(pool.size(), 2)
        pool.close()
        self.assertTrue(all(item.closed for item in made))
        with self.assertRaises(RuntimeError):
            pool.channel()

    def test_remote_hands_pool_channels_to_other_threads_only(self) -> None:
        box = remote.Remote.__new__(remote.Remote)
        home = SqlChannel(_Transport())
        box._sql = home
        box._pool = None
        pool, _made = self._pool()
        box._pool = pool
        got: list[SqlChannel] = []
        worker = threading.Thread(target=lambda: got.append(box.sql_channel()))
        worker.start()
        worker.join(5)
        self.assertIs(box.sql_channel(), home)
        self.assertIsNot(got[0], home)
        box.close_sql_pool()
        self.assertIsNone(box._pool)

    def test_channel_execute_is_serialised(self) -> None:
        channel = SqlChannel(_Transport())
        results: list[RemoteResult] = []
        threads = [
            threading.Thread(target=lambda: results.append(channel.execute("UFDATA_801_2024", "SELECT 1", _no_legacy)))
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual([item.out for item in results], ["ROW:1"] * 4)

    def test_slot_names_are_unique_ordered_and_safe(self) -> None:
        first, second = slot_name("sale-verify", 1), slot_name("sale-verify", 1)
        self.assertNotEqual(first, second)
        self.assertLess(first, second)
        self.assertRegex(first, r"^\d{4,}-sale-verify-q1-[0-9a-f]{8}$")
        self.assertIn("-sql-q2-", slot_name("Bad Name", 2))
        self.assertIn("-load-sale-order-q1-", slot_name("load-sale_order", 1))

    def test_sql_refused_after_stop(self) -> None:
        ctx = _Ctx()
        ctx.stop.set()
        with self.assertRaises(RuntimeError):
            run_guarded(_env(ctx, {}), "SELECT 1", "pick")


def _no_legacy() -> RemoteResult:
    raise AssertionError("不应回退")


class _Ctx:
    def __init__(self) -> None:
        self.tests: list[tuple[str, str, str]] = []
        self.remote = types.SimpleNamespace(open_sql_pool=self._open, close_sql_pool=self._close)
        self.pool_calls: list[str] = []
        self.report_dir = Path("/nonexistent")
        self.stop = threading.Event()
        self.jobs = 1
        self.reasons: list[str] = []

    def fail(self, reason: str) -> None:
        self.reasons.append(reason)

    def _open(self) -> None:
        self.pool_calls.append("open")

    def _close(self) -> None:
        self.pool_calls.append("close")

    def add_test(self, name: str, status: str, detail: str, extra: dict | None = None) -> None:
        self.tests.append((name, status, detail))


class _Client:
    def __init__(self, workers: int = 4) -> None:
        self.workers = workers
        self.calls = 0

    def health(self) -> dict:
        return {"ok": True, "workers": self.workers}

    def call(self, _route: str, _fields: dict) -> dict:
        self.calls += 1
        return {"ok": True}


def _env(ctx: _Ctx, bag: dict[str, str], client: _Client | None = None) -> Env:
    return Env(ctx, client or _Client(), None, "2026-09-29", "2026", "", bag)  # type: ignore[arg-type]


class EnvTests(unittest.TestCase):
    def test_env_for_copies_bag_and_resets_case_state(self) -> None:
        base = _env(_Ctx(), {"login": "1"})
        base.made["x"], base.focus, base.last_code, base.sql_n = "1", "2", "3", 4
        copy = runner.env_for(base)
        copy.bag["pinv_code"] = "EXE2E1"
        copy.made["y"] = "9"
        self.assertNotIn("pinv_code", base.bag)
        self.assertEqual((copy.made, copy.focus, copy.last_code, copy.sql_n), ({"y": "9"}, "", "", 0))
        self.assertEqual(copy.bag["login"], "1")
        self.assertIs(copy.ctx, base.ctx)

    def test_concurrent_run_reports_in_catalog_order(self) -> None:
        ctx = _Ctx()
        env = _env(ctx, {"login": "1"})
        specs = [_LOGIN, _case("a", ("a",)), _case("b", ("b",)), _case("c", ("c",))]
        rules = (lambda *_args: False, sched.no_tags, sched.own_flag)
        with mock.patch.object(runner, "default_rules", return_value=rules):
            runner.run_concurrent(env, specs, 3)
        self.assertEqual([row[0] for row in ctx.tests], [spec.title for spec in specs])
        self.assertTrue(all(row[1] == "FAIL" for row in ctx.tests))
        self.assertEqual(ctx.pool_calls, ["open", "close"])

    def test_jobs_capped_by_bridge_workers(self) -> None:
        ctx = _Ctx()
        env = _env(ctx, {"login": "1"}, _Client(workers=2))
        rules = (lambda *_args: False, sched.no_tags, sched.own_flag)
        with mock.patch.object(runner, "default_rules", return_value=rules), redirect_stderr(io.StringIO()):
            runner.run_concurrent(env, [_LOGIN, _case("a")], 4)
        self.assertEqual(ctx.jobs, 2)
        self.assertEqual(runner.cap_jobs(_env(_Ctx(), {}, _Client(workers=8)), 4), 4)

    def test_every_crash_is_recorded(self) -> None:
        ctx = _Ctx()
        env = _env(ctx, {"login": "1"})
        specs = [_LOGIN, _case("a", ("a",)), _case("b", ("b",))]
        rules = (lambda *_args: False, sched.no_tags, sched.own_flag)

        def boom(_case_env: Env, spec: CaseSpec) -> tuple[str, str]:
            if spec.name == "login":
                return "PASS", ""
            raise ValueError("坏了 " + spec.name)

        with (
            mock.patch.object(runner, "default_rules", return_value=rules),
            mock.patch.object(runner, "judge_one", side_effect=boom),
            self.assertRaises(sched.CaseCrashed),
        ):
            runner.run_concurrent(env, specs, 2)
        self.assertEqual(len(ctx.reasons), 2)
        self.assertTrue(all("ValueError" in reason for reason in ctx.reasons))

    def test_walk_stops_before_next_step(self) -> None:
        ctx = _Ctx()
        ctx.stop.set()
        status, detail = runner.walk(_env(ctx, {}), _case("a"))
        self.assertEqual(status, "SKIP")
        self.assertIn("收到中断", detail)

    def test_gated_client_refuses_after_stop(self) -> None:
        stop = threading.Event()
        inner = _Client(workers=3)
        gated = GatedClient(inner, stop)
        gated.call("/v1/vouchers/load", {})
        self.assertEqual(gated.workers, 3)
        stop.set()
        with self.assertRaises(U8CoError):
            gated.call("/v1/vouchers/load", {})
        self.assertEqual(inner.calls, 1)


def _main(argv: list[str]) -> tuple[int, mock.MagicMock, str]:
    out = io.StringIO()
    with mock.patch.object(run_e2e, "run", return_value=0) as runner_mock, redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = run_e2e.main(["--allow-daytime", *argv])
    return code, runner_mock, out.getvalue()


def _coverage(chosen: list[str]) -> types.ModuleType:
    # 与真的 coverage 一样：select_changed 的结果总含 health、login。
    module = types.ModuleType("co.e2e.casespec.coverage")
    module.ALWAYS = ("health", "login")  # type: ignore[attr-defined]
    module.select_changed = lambda _paths, _names: ["health", "login", *chosen]  # type: ignore[attr-defined]
    module.explain = lambda paths: [(path, "Gl*", ",".join(chosen)) for path in paths]  # type: ignore[attr-defined]
    return module


class CommandTests(unittest.TestCase):
    def test_jobs_flags(self) -> None:
        for argv, jobs in (([], 1), (["--jobs", "2"], 2), (["--serial"], 1), (["--jobs", "6", "--serial"], 1)):
            with self.subTest(argv=argv):
                code, runner_mock, _out = _main(argv)
                self.assertEqual(code, 0)
                self.assertEqual(runner_mock.call_args.args[3], jobs)

    def test_jobs_out_of_range_refused(self) -> None:
        for value in ("0", "9"):
            code, runner_mock, out = _main(["--jobs", value])
            self.assertEqual(code, 2)
            runner_mock.assert_not_called()
            self.assertIn("--jobs", out)

    def test_changed_since_selects_and_intersects(self) -> None:
        with (
            mock.patch.object(run_e2e, "changed_paths", return_value=["co/bridge/src/Gl.cs"]),
            mock.patch.dict(sys.modules, {"co.e2e.casespec.coverage": _coverage(["gl-cycle", "gl-list"])}),
        ):
            code, runner_mock, _out = _main(["--changed-since", "origin/main"])
            self.assertEqual(code, 0)
            self.assertEqual(runner_mock.call_args.args[2], "health,login,gl-cycle,gl-list")
            code, runner_mock, _out = _main(["--changed-since", "origin/main", "--cases", "gl-list,sale-verify"])
            self.assertEqual(runner_mock.call_args.args[2], "health,login,gl-list")

    def test_changed_since_without_coverage_module_refused(self) -> None:
        with (
            mock.patch.object(run_e2e, "changed_paths", return_value=[]),
            mock.patch.dict(sys.modules, {"co.e2e.casespec.coverage": None}),
        ):
            code, runner_mock, _out = _main(["--changed-since", "HEAD~1"])
        self.assertEqual(code, 2)
        runner_mock.assert_not_called()

    def test_bad_ref_refused_before_git(self) -> None:
        for ref in ("--output=x", "a b"):
            with self.assertRaises(run_e2e.ChangedError):
                run_e2e.changed_paths(ref)

    def test_report_shows_jobs(self) -> None:
        from co.e2e.report import _jobs_line

        self.assertEqual(_jobs_line(4), "跑法：并行度 4")
        self.assertEqual(_jobs_line(1), "跑法：串行")
        self.assertEqual(_jobs_line(None), "跑法：串行")

    def test_porcelain_parsing(self) -> None:
        text = " M co/bridge/src/Gl.cs\0R  docs/new.md\0docs/old.md\0?? co/client/x.py\0"
        self.assertEqual(
            run_e2e.parse_porcelain(text),
            ["co/bridge/src/Gl.cs", "docs/new.md", "docs/old.md", "co/client/x.py"],
        )


if __name__ == "__main__":
    unittest.main()
