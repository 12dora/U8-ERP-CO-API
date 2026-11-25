"""session close --keep：可造数通道保留造数结果。不连接服务器。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from co.e2e import settings
from co.e2e.markers import keep_refusal, keep_resumable, markers_cleared, read_linux_marker, write_linux_marker
from co.e2e.remote import RemoteResult
from co.e2e.report import assemble, render_markdown
from co.e2e.session_ops import close_session
from co.e2e.tests import support

_MUTATING = ("service-stop.ps1", "watchdog-disarm.ps1", "snapshot-drop.ps1", "snapshot-revert.ps1", "session-clear.ps1")


class KeepRemote:
    def __init__(self, status: dict) -> None:
        self.calls: list[str] = []
        self.codes: dict[str, int] = {}
        self.status = status

    def run_script(self, name: str, named: dict | None, timeout: int) -> RemoteResult:
        del timeout
        if named and named.get("Mode") == "check":
            name += "?check"
        self.calls.append(name)
        out = name
        if name == "session-status.ps1" and named and named.get("Tag"):
            out += "\ntag " + named["Tag"]
        return RemoteResult(self.codes.get(name, 0), out)

    def download(self, remote_win: str, local: Path) -> None:
        self.calls.append("download")
        doc: dict = {}
        if remote_win.endswith("session-status.json"):
            doc = {"config": support.status_identity()}
            doc.update(self.status)
        for database in settings.conf().databases:
            if remote_win.endswith("\\fp-" + database + ".json"):
                row = {"schema": "dbo", "name": "T", "rows": 3, "checksum": 7, "error": ""}
                doc = {"database": database, "tables": [row]}
        local.write_text(json.dumps(doc), encoding="utf-8")

    def upload(self, local: Path, remote_win: str) -> None:
        del local, remote_win
        self.calls.append("upload")

    def ensure_dirs(self) -> RemoteResult:
        self.calls.append("ensure-dirs")
        return RemoteResult(0, "ok")


def _live(owner: str = "session") -> dict:
    snaps = list(settings.conf().snapshots)
    later = datetime.now(ZoneInfo(settings.conf().timezone)) + timedelta(minutes=30)
    return {
        "snapshots": snaps,
        "done": False,
        "marker": True,
        "watchdog_state": "Ready",
        "next_run": later.isoformat(),
        "owner": owner,
    }


def _assert_nothing_changed(case: unittest.TestCase, remote: KeepRemote) -> None:
    case.assertFalse(any(name in remote.calls for name in _MUTATING), remote.calls)


def _seedable(folder: str) -> settings.E2EConfig:
    data = support.lane_dict("802")
    data["account"]["seedable"] = True
    data["report"] = {"dir": str(Path(folder) / "runs-802")}
    return settings.from_dict(data)


def _report() -> tuple[dict, str]:
    found = sorted(Path(settings.conf().report_dir).glob("*/report.json"))
    folder = found[-1].parent
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    return report, (folder / "report.md").read_text(encoding="utf-8")


class KeepRuleTests(unittest.TestCase):
    def test_refusals(self) -> None:
        self.assertIn("seedable", keep_refusal("revert", "session", False))
        self.assertIn("session", keep_refusal("revert", "run", True))
        self.assertIn("session", keep_refusal("revert", "", True))
        for kind in ("verify-only", "already-closed", "foreign"):
            self.assertTrue(keep_refusal(kind, "session", True), kind)
        self.assertEqual(keep_refusal("revert", "session", True), "")

    def test_markers_cleared_for_keep(self) -> None:
        self.assertTrue(markers_cleared("keep", True, True, True))
        self.assertFalse(markers_cleared("keep", False, True, True))
        self.assertFalse(markers_cleared("keep", True, False, True))
        self.assertFalse(markers_cleared("keep", True, True, False))

    def test_resumable_only_after_noted_keep(self) -> None:
        local = {"owner": "session", "keep": "in-progress"}
        idle = {"snapshots": [], "done": True, "watchdog_state": "none"}
        self.assertTrue(keep_resumable(local, idle, "verify-only", False))
        self.assertFalse(keep_resumable(local, idle, "verify-only", True))
        self.assertFalse(keep_resumable({"owner": "session"}, idle, "verify-only", False))
        self.assertFalse(keep_resumable(None, idle, "verify-only", False))
        self.assertFalse(keep_resumable(local, dict(idle, watchdog_state="Ready"), "verify-only", False))
        self.assertFalse(keep_resumable(local, idle, "revert", False))

    def test_report_never_claims_rollback(self) -> None:
        support.install()
        try:
            doc = {"databases": {name: {"totals": {"tables": 1, "rows": 3}} for name in settings.conf().databases}}
            state = {"keep": True, "keep_fingerprint": doc, "snapshots_made": True, "before": {}, "after": {}}
            report = assemble(state)
            self.assertEqual(report["overall"], "KEPT")
            self.assertFalse(report["rollback_verified"])
            self.assertEqual(report["manual"], "")
            text = render_markdown(report)
            self.assertIn("结论：已保留（未还原）", text)
            self.assertNotIn("指纹差异", text)
            failed = assemble(dict(state, keep_fingerprint=None))
            self.assertEqual(failed["overall"], "FAIL")
            self.assertTrue(failed["manual"])
        finally:
            support.reset()


class DropScriptTests(unittest.TestCase):
    # 不跑 pwsh：核对 snapshot-drop.ps1 的结构。check 在取消看门狗之前跑，要求看门狗还在；drop 才要求已取消。
    def test_check_mode_requires_armed_watchdog(self) -> None:
        path = Path(__file__).resolve().parents[1] / "remote" / "snapshot-drop.ps1"
        text = path.read_text(encoding="utf-8-sig")
        main = text[text.index("Assert-Seedable\n") :]
        check = main[main.index('if ($Mode -eq "check")') : main.index("exit 0")]
        self.assertIn("Assert-Armed", check)
        self.assertNotIn("Assert-Disarmed", check)
        self.assertLess(main.index("exit 0"), main.index("Assert-Disarmed"))
        self.assertLess(main.index("Assert-Disarmed"), main.index("Drop-Snap"))
        self.assertIn("Mandatory = $true", text)
        armed = text[text.index("function Assert-Armed") : text.index("function Assert-Disarmed")]
        self.assertIn("Assert-DoneAbsent", armed)
        self.assertIn("-eq $task", armed)

    def test_revert_retries_single_user_holder(self) -> None:
        path = Path(__file__).resolve().parents[1] / "remote" / "snapshot-revert.ps1"
        text = path.read_text(encoding="utf-8-sig")
        for code in ("5064", "5070", "3101", "3102", "5061"):
            self.assertIn(code, text)
        self.assertIn('Invoke-Busy { Get-SqlText "master" $batch }', text)
        self.assertIn('Invoke-Busy { Get-SqlText "master" $sql }', text)
        self.assertIn("<> N'MULTI_USER'", text)


class KeepCloseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        support.install(_seedable(self.folder.name))
        Path(settings.conf().report_dir).mkdir(parents=True)

    def tearDown(self) -> None:
        support.reset()
        self.folder.cleanup()

    def test_refused_without_seedable_before_remote(self) -> None:
        support.install(support.lane_config())
        remote = KeepRemote(_live())
        self.assertEqual(close_session(remote, keep=True), 1)
        self.assertEqual(remote.calls, [])

    def test_refused_for_run_owner(self) -> None:
        remote = KeepRemote(_live("run"))
        self.assertEqual(close_session(remote, keep=True), 1)
        _assert_nothing_changed(self, remote)

    def test_refused_when_verify_only(self) -> None:
        status = {"snapshots": [], "done": True, "marker": True, "watchdog_state": "none", "owner": "session"}
        remote = KeepRemote(status)
        self.assertEqual(close_session(remote, keep=True), 1)
        _assert_nothing_changed(self, remote)

    def test_refused_when_one_snapshot_missing(self) -> None:
        status = _live()
        status["snapshots"] = status["snapshots"][:1]
        remote = KeepRemote(status)
        self.assertEqual(close_session(remote, keep=True), 1)
        _assert_nothing_changed(self, remote)

    def test_refused_when_watchdog_fired(self) -> None:
        remote = KeepRemote(dict(_live(), next_run="fired"))
        self.assertEqual(close_session(remote, keep=True), 1)
        _assert_nothing_changed(self, remote)

    def test_refused_when_done_present(self) -> None:
        remote = KeepRemote(dict(_live(), done=True))
        self.assertEqual(close_session(remote, keep=True), 1)
        _assert_nothing_changed(self, remote)

    def test_missing_drop_script_fails_before_disarm(self) -> None:
        remote = KeepRemote(_live())
        remote.codes["snapshot-drop.ps1?check"] = 1
        self.assertEqual(close_session(remote, keep=True), 1)
        self.assertIn("snapshot-drop.ps1?check", remote.calls)
        self.assertNotIn("watchdog-disarm.ps1", remote.calls)
        self.assertNotIn("snapshot-drop.ps1", remote.calls)
        self.assertNotIn("session-clear.ps1", remote.calls)

    def test_gate_failure_fails_before_disarm(self) -> None:
        remote = KeepRemote(_live())
        remote.codes["session-gate.ps1"] = 1
        self.assertEqual(close_session(remote, keep=True), 1)
        self.assertNotIn("watchdog-disarm.ps1", remote.calls)
        self.assertNotIn("snapshot-drop.ps1", remote.calls)

    def test_keep_disarms_first_and_never_reverts(self) -> None:
        remote = KeepRemote(_live())
        self.assertEqual(close_session(remote, keep=True), 0)
        calls = remote.calls
        self.assertNotIn("snapshot-revert.ps1", calls)
        self.assertLess(calls.index("service-stop.ps1"), calls.index("session-gate.ps1"))
        self.assertLess(calls.index("session-gate.ps1"), calls.index("watchdog-disarm.ps1"))
        self.assertLess(calls.index("snapshot-drop.ps1?check"), calls.index("watchdog-disarm.ps1"))
        self.assertLess(calls.index("watchdog-disarm.ps1"), calls.index("snapshot-drop.ps1"))
        self.assertLess(calls.index("snapshot-drop.ps1"), calls.index("service-start.ps1"))
        self.assertIn("session-clear.ps1", calls)
        report, text = _report()
        self.assertEqual(report["overall"], "KEPT")
        self.assertFalse(report["rollback_verified"])
        self.assertEqual(set(report["keep_fingerprint"]), set(settings.conf().databases))
        self.assertIn("已保留（未还原）", text)

    def test_drop_failure_keeps_markers(self) -> None:
        remote = KeepRemote(_live())
        remote.codes["snapshot-drop.ps1"] = 1
        self.assertEqual(close_session(remote, keep=True), 1)
        self.assertIn("watchdog-disarm.ps1", remote.calls)
        self.assertNotIn("session-clear.ps1", remote.calls)
        self.assertNotIn("snapshot-revert.ps1", remote.calls)
        self.assertEqual((read_linux_marker() or {}).get("keep"), "in-progress")
        report, text = _report()
        self.assertEqual(report["overall"], "FAIL")
        self.assertIn("保留没有完成", text)
        self.assertIn("-AllowMissing", text)

    def test_resume_only_takes_fingerprint(self) -> None:
        folder = Path(settings.conf().report_dir) / "session-1"
        folder.mkdir()
        write_linux_marker({"report_dir": str(folder), "owner": "session", "keep": "in-progress"})
        status = {"snapshots": [], "done": True, "marker": True, "watchdog_state": "none", "owner": "session"}
        remote = KeepRemote(status)
        self.assertEqual(close_session(remote, keep=True), 0)
        self.assertIn("fingerprint.ps1", remote.calls)
        self.assertIn("session-clear.ps1", remote.calls)
        for name in ("service-stop.ps1", "watchdog-disarm.ps1", "snapshot-drop.ps1", "snapshot-revert.ps1"):
            self.assertNotIn(name, remote.calls)
        self.assertTrue((folder / "fingerprint-keep.json").is_file())
        self.assertIsNone(read_linux_marker())

    def test_stop_failure_keeps_snapshots_and_watchdog(self) -> None:
        remote = KeepRemote(_live())
        remote.codes["service-stop.ps1"] = 1
        self.assertEqual(close_session(remote, keep=True), 1)
        self.assertNotIn("watchdog-disarm.ps1", remote.calls)
        self.assertNotIn("snapshot-drop.ps1", remote.calls)
        self.assertNotIn("session-clear.ps1", remote.calls)


if __name__ == "__main__":
    unittest.main()
