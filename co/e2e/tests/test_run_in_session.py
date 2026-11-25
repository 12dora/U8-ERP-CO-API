"""run_e2e --in-session：在已打开的调试会话里跑用例。全部用假远端，不连接服务器。"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from co.e2e import insession, multi, run_e2e, session, settings
from co.e2e.lanelock import session_lock
from co.e2e.markers import read_linux_marker, write_linux_marker
from co.e2e.multi_report import LaneResult, lane_reasons
from co.e2e.remote import RemoteResult
from co.e2e.tests import support

# 回归自己的准备、快照、看门狗和还原：会话内运行一个都不能调。
_FORBIDDEN = (
    "preflight.ps1",
    "fingerprint.ps1",
    "s4u-arm.ps1",
    "clear-inrevert.ps1",
    "snapshot-create.ps1",
    "watchdog-arm.ps1",
    "watchdog-disarm.ps1",
    "snapshot-revert.ps1",
    "snapshot-drop.ps1",
    "service-stop.ps1",
    "session-mark.ps1",
    "session-clear.ps1",
    "msgcenter-hash.ps1",
)


class FakeRemote:
    def __init__(self, status: dict) -> None:
        self.calls: list[str] = []
        self.codes: dict[str, int] = {}
        self.status = status
        # 运行结束后那次状态读取可以换成另一份（模拟看门狗中途还原）。
        self.later: dict | None = None
        self.reads = 0

    def run_script(self, name: str, named: dict | None, timeout: int) -> RemoteResult:
        del timeout
        self.calls.append(name)
        out = name
        if name == "session-status.ps1" and named and named.get("Tag"):
            out += "\ntag " + named["Tag"]
        return RemoteResult(self.codes.get(name, 0), out)

    def download(self, remote_win: str, local: Path) -> None:
        self.calls.append("download")
        doc: dict = {}
        if remote_win.endswith("session-status.json"):
            self.reads += 1
            doc = {"config": support.status_identity()}
            doc.update(self.later if self.later is not None and self.reads > 1 else self.status)
        local.write_text(json.dumps(doc), encoding="utf-8")

    def upload(self, local: Path, remote_win: str) -> None:
        del local, remote_win
        self.calls.append("upload")

    def ensure_dirs(self) -> RemoteResult:
        self.calls.append("ensure-dirs")
        return RemoteResult(0, "ok")


def _live(minutes: int = 60, owner: str = "session") -> dict:
    later = datetime.now(ZoneInfo(settings.conf().timezone)) + timedelta(minutes=minutes)
    return {
        "snapshots": list(settings.conf().snapshots),
        "done": False,
        "marker": True,
        "watchdog_state": "Ready",
        "next_run": later.isoformat(),
        "owner": owner,
    }


def _fake_suite(ctx, today: str, started: str, audit_since: str) -> None:
    del today, started, audit_since
    ctx.add_test("health", "PASS", "ok", {"case": "health", "duration_ms": 5})


def _fake_deploy(ctx) -> bool:
    ctx.secret = "fake-secret"
    return True


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        support.install(support.make_config(report__dir=self.folder.name))
        self.session_dir = Path(self.folder.name) / "session-1"
        self.session_dir.mkdir()
        self.patches = [
            mock.patch("co.e2e.insession.deploy_service", side_effect=_fake_deploy),
            mock.patch("co.e2e.engine._cred_ready"),
            mock.patch("co.e2e.engine.do_pick"),
            mock.patch("co.e2e.suite.run_suite", side_effect=_fake_suite),
        ]
        self.mocks = [item.start() for item in self.patches]

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        support.reset()
        self.folder.cleanup()

    def mark(self, **extra: object) -> None:
        payload = {"report_dir": str(self.session_dir), "watchdog_min": 120, "owner": "session"}
        payload.update(extra)
        write_linux_marker(payload)

    def go(self, remote: FakeRemote, estimate: int = 25, cases: str = "health") -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = insession.run_in_session({"cases": cases, "jobs": 1, "force_deploy": False}, estimate, remote)
        return code, out.getvalue()

    def reports(self) -> list[Path]:
        return sorted(Path(self.folder.name).glob("*-insession-*/report.json"))


class RefusalTests(_Base):
    def assert_refused(self, remote: FakeRemote, estimate: int = 25) -> str:
        code, text = self.go(remote, estimate)
        self.assertEqual(code, 2, text)
        self.assertIn("不能在会话内运行", text)
        self.assertEqual(self.reports(), [])
        self.mocks[0].assert_not_called()
        self.assertFalse(any(name in remote.calls for name in _FORBIDDEN), remote.calls)
        return text

    def test_no_session_refused_before_remote(self) -> None:
        remote = FakeRemote(_live())
        self.assert_refused(remote)
        self.assertEqual(remote.calls, [])

    def test_run_owner_refused(self) -> None:
        self.mark(owner="run")
        self.assert_refused(FakeRemote(_live(owner="run")))

    def test_keep_in_progress_refused(self) -> None:
        self.mark(keep="in-progress")
        self.assert_refused(FakeRemote(_live()))

    def test_server_marker_missing_refused(self) -> None:
        self.mark()
        self.assert_refused(FakeRemote(dict(_live(), marker=False)))

    def test_snapshot_missing_refused(self) -> None:
        self.mark()
        status = _live()
        status["snapshots"] = status["snapshots"][:1]
        self.assert_refused(FakeRemote(status))

    def test_done_refused(self) -> None:
        self.mark()
        self.assert_refused(FakeRemote(dict(_live(), done=True)))

    def test_watchdog_fired_or_missing_refused(self) -> None:
        self.mark()
        self.assert_refused(FakeRemote(dict(_live(), next_run="fired")))
        self.assert_refused(FakeRemote(dict(_live(), watchdog_state="none", next_run="none")))

    def test_not_enough_watchdog_time_refused(self) -> None:
        self.mark()
        # 剩 28 分钟，预计 25 分钟 + 5 分钟余量 = 30，拒绝；预计 20 分钟就可以。
        text = self.assert_refused(FakeRemote(_live(minutes=28)), estimate=25)
        self.assertIn("--estimate-min", text)
        code, _ = self.go(FakeRemote(_live(minutes=28)), estimate=20)
        self.assertEqual(code, 0)

    def test_server_gate_refused(self) -> None:
        self.mark()
        remote = FakeRemote(_live())
        remote.codes["session-gate.ps1"] = 1
        self.assert_refused(remote)

    def test_pure_refusal(self) -> None:
        now = datetime.now(ZoneInfo(settings.conf().timezone))
        local = {"owner": "session", "report_dir": "x"}
        self.assertEqual(insession.session_refusal(local, _live(60), 25, now), "")
        self.assertNotEqual(insession.session_refusal(local, _live(29), 25, now), "")
        self.assertNotEqual(insession.session_refusal(None, _live(60), 25, now), "")


class RunTests(_Base):
    def test_skips_prepare_snapshot_and_revert(self) -> None:
        self.mark()
        remote = FakeRemote(_live())
        code, text = self.go(remote)
        self.assertEqual(code, 0, text)
        self.assertFalse(any(name in remote.calls for name in _FORBIDDEN), remote.calls)
        # 不重传脚本、不写标记：会话照旧打开，标记原样。
        self.assertNotIn("upload", remote.calls)
        self.assertIn("session-gate.ps1", remote.calls)
        self.assertEqual(read_linux_marker()["report_dir"], str(self.session_dir))
        self.mocks[0].assert_called_once()
        self.assertIn(insession.REMINDER, text)

    def test_report_fields(self) -> None:
        self.mark()
        code, _ = self.go(FakeRemote(_live()))
        self.assertEqual(code, 0)
        [path] = self.reports()
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(report["mode"], "in-session")
        self.assertEqual(report["overall"], "PASS")
        self.assertIsNone(report["rollback_verified"])
        self.assertEqual(report["rollback_note"], "会话内运行，还原由 session close 负责")
        self.assertFalse(report["gate_evidence"])
        self.assertEqual(report["manual"], "")
        self.assertEqual(report["session_report_dir"], str(self.session_dir))
        self.assertEqual(report["watchdog_min"], 120)
        self.assertNotIn("prep", report["phases"])
        self.assertNotIn("revert", report["phases"])
        markdown = (path.parent / "report.md").read_text(encoding="utf-8")
        self.assertIn("rollback_verified：null", markdown)
        self.assertIn("模式：in-session", markdown)

    def test_failed_case_fails_report(self) -> None:
        self.mark()

        def failing(ctx, *args: object) -> None:
            ctx.add_test("health", "FAIL", "bad", {"case": "health"})

        self.mocks[3].side_effect = failing
        code, text = self.go(FakeRemote(_live()))
        self.assertEqual(code, 1)
        self.assertIn(insession.REMINDER, text)

    def test_watchdog_fired_during_run_fails(self) -> None:
        self.mark()
        remote = FakeRemote(_live())
        remote.later = dict(_live(), snapshots=[], done=True)
        code, _ = self.go(remote)
        self.assertEqual(code, 1)
        report = json.loads(self.reports()[0].read_text(encoding="utf-8"))
        self.assertEqual(report["overall"], "FAIL")
        self.assertTrue(any("不可信" in item for item in report["reasons"]))
        self.assertFalse(any(name in remote.calls for name in _FORBIDDEN), remote.calls)

    def test_deploy_failure_reports_without_revert(self) -> None:
        self.mark()
        self.mocks[0].side_effect = RuntimeError("build 失败")
        remote = FakeRemote(_live())
        code, _ = self.go(remote)
        self.assertEqual(code, 1)
        self.mocks[3].assert_not_called()
        self.assertFalse(any(name in remote.calls for name in _FORBIDDEN), remote.calls)


class LockTests(_Base):
    def test_close_and_reset_refused_during_run(self) -> None:
        self.mark()
        seen: dict[str, int] = {}

        def contend(ctx, *args: object) -> None:
            with contextlib.redirect_stdout(io.StringIO()):
                for cmd in ("close", "reset", "deploy", "call"):
                    argv = [cmd, "health"] if cmd == "call" else [cmd]
                    seen[cmd] = session.main(argv)
            _fake_suite(ctx, "", "", "")

        self.mocks[3].side_effect = contend
        remote = FakeRemote(_live())
        with mock.patch("co.e2e.session_ops.make_remote") as made, mock.patch("co.e2e.session_reset.make_remote") as made_reset:
            code, _ = self.go(remote)
        self.assertEqual(code, 0)
        self.assertEqual(seen, {"close": 1, "reset": 1, "deploy": 1, "call": 1})
        made.assert_not_called()
        made_reset.assert_not_called()
        self.assertFalse(any(name in remote.calls for name in _FORBIDDEN), remote.calls)

    def test_refused_while_close_or_reset_holds_lock(self) -> None:
        self.mark()
        for exclusive in (True, False):
            remote = FakeRemote(_live())
            # close、reset 持独占锁；call、sql 持共享锁。两种情况下会话内运行都不开始。
            with session_lock(exclusive):
                code, text = self.go(remote)
            self.assertEqual(code, 2)
            self.assertEqual(text.strip(), insession.BUSY)
            self.assertEqual(remote.calls, [])


class GateSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        support.install()

    def tearDown(self) -> None:
        support.reset()

    def _main(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run_e2e.main(argv)
        return code, out.getvalue()

    def test_option_conflicts(self) -> None:
        for argv in (
            ["--in-session", "--dry-run"],
            ["--in-session", "--watchdog-min", "30"],
            ["--estimate-min", "10"],
            ["--in-session", "--estimate-min", "0"],
        ):
            code, text = self._main(argv)
            self.assertEqual(code, 2, argv)
            self.assertTrue(text.strip(), argv)

    def test_dispatches_to_in_session(self) -> None:
        with mock.patch("co.e2e.insession.run_in_session", return_value=0) as called, mock.patch("co.e2e.run_e2e.run") as normal:
            code, _ = self._main(["--in-session", "--cases", "health", "--estimate-min", "10", run_e2e.daytime.FLAG])
        self.assertEqual(code, 0)
        normal.assert_not_called()
        opts, estimate = called.call_args.args
        self.assertEqual(opts["cases"], "health")
        self.assertEqual(estimate, 10)

    def test_multi_refuses_in_session(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = multi.main(["--in-session", "--lane", "main=/nonexistent/e2e.json"])
        self.assertEqual(code, 2)
        self.assertIn("--in-session", out.getvalue())
        self.assertNotIn("--in-session", multi.child_command(["health"], multi.Options(False, False, None, False)))

    def test_multi_report_rejects_in_session_report(self) -> None:
        report = {"overall": "PASS", "mode": "in-session", "rollback_verified": None, "tests": []}
        reasons = lane_reasons(LaneResult("main", "801", 0, report), False)
        self.assertTrue(any("in-session" in item for item in reasons))
        self.assertTrue(any("rollback_verified" in item for item in reasons))


if __name__ == "__main__":
    unittest.main()
