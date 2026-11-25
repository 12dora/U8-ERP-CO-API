"""session reset：还原到会话快照，会话保持打开。不连接服务器。"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from co.e2e import settings
from co.e2e.fingerprint import merge_parts, normalize
from co.e2e.markers import read_linux_marker, write_linux_marker
from co.e2e.remote import RemoteResult
from co.e2e.session_reset import RESETS_FILE, reset_note, reset_refusal, reset_session
from co.e2e.tests import support

_NEVER = ("watchdog-disarm.ps1", "snapshot-drop.ps1", "session-clear.ps1", "msgcenter-hash.ps1", "read-audit.ps1")
_NEVER += ("login-count.ps1", "fingerprint.ps1")
_REVERT = "snapshot-revert.ps1 -KeepSnapshot"


def _part(database: str, rows: int) -> dict:
    return {"database": database, "tables": [{"schema": "dbo", "name": "T", "rows": rows, "checksum": 7, "error": ""}]}


def _reference(rows: int = 3) -> dict:
    return normalize(merge_parts([_part(name, rows) for name in settings.conf().databases]))


class ResetRemote:
    def __init__(self, status: dict) -> None:
        self.calls: list[str] = []
        self.codes: dict[str, int] = {}
        self.status = status
        # 还原之后的会话状态；None 表示不变。
        self.after_status: dict | None = None
        self.rows = 3
        self.revert_out = "已还原\nsnapshot-revert ok keep-snapshot"
        self.script_text = "param(\n    [switch]$AllowMissing,\n    [switch]$KeepSnapshot\n)\n"

    def run_script(self, name: str, named: dict | None, timeout: int) -> RemoteResult:
        del timeout
        self.calls.append(name)
        out = name
        if name == "session-status.ps1" and named and named.get("Tag"):
            out += "\ntag " + named["Tag"]
        if name in self.codes and self.codes[name] != 0:
            out = name + " 失败\n第二行"
        return RemoteResult(self.codes.get(name, 0), out)

    def run_body(self, body: str, timeout: int) -> RemoteResult:
        del timeout
        name = _REVERT if body.endswith("snapshot-revert.ps1' -KeepSnapshot") else "body"
        self.calls.append(name)
        if self.after_status is not None:
            self.status = self.after_status
        return RemoteResult(self.codes.get(name, 0), self.revert_out)

    def download(self, remote_win: str, local: Path) -> None:
        self.calls.append("download")
        if remote_win.endswith("\\snapshot-revert.ps1"):
            local.write_text(self.script_text, encoding="utf-8")
            return
        doc: dict = {}
        if remote_win.endswith("session-status.json"):
            doc = {"config": support.status_identity()}
            doc.update(self.status)
        for database in settings.conf().databases:
            if remote_win.endswith("\\fp-inrevert-" + database + ".json"):
                doc = _part(database, self.rows)
        local.write_text(json.dumps(doc), encoding="utf-8")

    def upload(self, local: Path, remote_win: str) -> None:
        del local, remote_win
        self.calls.append("upload")

    def ensure_dirs(self) -> RemoteResult:
        self.calls.append("ensure-dirs")
        return RemoteResult(0, "ok")


def _live(owner: str = "session") -> dict:
    later = datetime.now(ZoneInfo(settings.conf().timezone)) + timedelta(minutes=30)
    return {
        "snapshots": list(settings.conf().snapshots),
        "done": False,
        "marker": True,
        "watchdog_state": "Ready",
        "next_run": later.isoformat(),
        "owner": owner,
    }


def _quiet(remote: ResetRemote) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = reset_session(remote)
    return code, buffer.getvalue()


class RefusalRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        support.install()

    def tearDown(self) -> None:
        support.reset()

    def test_rules(self) -> None:
        now = datetime.now(ZoneInfo(settings.conf().timezone))
        self.assertEqual(reset_refusal({"owner": "session"}, _live(), now), "")
        self.assertIn("另一个运行", reset_refusal({"owner": "run"}, _live("run"), now))
        self.assertIn("session open", reset_refusal({}, dict(_live(), owner=""), now))
        self.assertIn("快照不在", reset_refusal({"owner": "session"}, dict(_live(), snapshots=[]), now))
        self.assertIn("已还原", reset_refusal({"owner": "session"}, dict(_live(), done=True), now))
        soon = (now + timedelta(minutes=2)).isoformat()
        self.assertIn("5 分钟", reset_refusal({"owner": "session"}, dict(_live(), next_run=soon), now))

    def test_refuses_under_fifteen_minutes(self) -> None:
        now = datetime.now(ZoneInfo(settings.conf().timezone))
        near = (now + timedelta(minutes=12)).isoformat()
        reason = reset_refusal({"owner": "session"}, dict(_live(), next_run=near), now)
        self.assertEqual(reason, "看门狗剩余不足 15 分钟，请直接 session close")
        later = (now + timedelta(minutes=16)).isoformat()
        self.assertEqual(reset_refusal({"owner": "session"}, dict(_live(), next_run=later), now), "")


class ResetScriptTests(unittest.TestCase):
    # 不跑 pwsh：核对 snapshot-revert.ps1 的结构。
    def test_keep_switch_skips_drop_only(self) -> None:
        path = Path(__file__).resolve().parents[1] / "remote" / "snapshot-revert.ps1"
        text = path.read_text(encoding="utf-8-sig")
        self.assertIn("[switch]$KeepSnapshot", text)
        self.assertIn("$missingOk = [bool]$AllowMissing -and -not [bool]$KeepSnapshot", text)
        self.assertIn("Assert-FreshWindow $missingOk", text)
        pair = text[text.index("function Restore-Pair") :]
        self.assertLess(pair.index("if ($Keep)"), pair.index("Drop-Snap $Snapshot"))
        self.assertLess(pair.index("Set-MultiUser $Database"), pair.index("if ($Keep)"))
        self.assertIn('throw ("已无快照 " + $Snapshot)', pair)
        self.assertIn("snapshot-revert ok keep-snapshot", text)


class ResetSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        data = support.lane_dict("802")
        data["report"] = {"dir": str(Path(self.folder.name) / "runs-802")}
        support.install(settings.from_dict(data))
        self.session = Path(settings.conf().report_dir) / "session-1"
        self.session.mkdir(parents=True)
        (self.session / "fingerprint-snapshot.json").write_text(json.dumps(_reference()), encoding="utf-8")
        write_linux_marker({"report_dir": str(self.session), "owner": "session", "watchdog_min": 60})

    def tearDown(self) -> None:
        support.reset()
        self.folder.cleanup()

    def _resets(self) -> list:
        return json.loads((self.session / RESETS_FILE).read_text(encoding="utf-8"))

    def test_refused_without_session(self) -> None:
        Path(settings.conf().report_dir, "session.json").unlink()
        remote = ResetRemote(_live())
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("没有打开的会话", out)
        self.assertEqual(remote.calls, [])

    def test_refused_for_foreign_owner(self) -> None:
        write_linux_marker({"report_dir": str(self.session), "owner": "run"})
        remote = ResetRemote(_live("run"))
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("另一个运行", out)
        for name in ("service-stop.ps1", _REVERT, "service-start.ps1"):
            self.assertNotIn(name, remote.calls)
        self.assertFalse((self.session / RESETS_FILE).exists())

    def test_happy_path_keeps_session(self) -> None:
        remote = ResetRemote(_live())
        code, out = _quiet(remote)
        self.assertEqual(code, 0, out)
        self.assertIn("reset ok", out)
        calls = remote.calls
        for name in _NEVER:
            self.assertNotIn(name, calls)
        self.assertNotIn("snapshot-revert.ps1", calls)
        self.assertLess(calls.index("session-gate.ps1"), calls.index("service-stop.ps1"))
        self.assertLess(calls.index("service-stop.ps1"), calls.index(_REVERT))
        self.assertLess(calls.index(_REVERT), calls.index("service-start.ps1"))
        self.assertLess(calls.index("service-start.ps1"), calls.index("clear-inrevert.ps1"))
        self.assertEqual((read_linux_marker() or {}).get("owner"), "session")
        rows = self._resets()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(rows[0]["diff_count"], 0)
        self.assertTrue((self.session / "reset-1" / "fingerprint-inrevert.json").is_file())
        self.assertFalse((self.session / "reset-1" / "snapshot-revert.ps1").exists())
        self.assertIn("1 次", reset_note(self.session))

    def test_diff_fails_and_starts_service(self) -> None:
        remote = ResetRemote(_live())
        remote.rows = 4
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("行数 3 -> 4", out)
        self.assertIn("service-start.ps1", remote.calls)
        self.assertNotIn("clear-inrevert.ps1", remote.calls)
        self.assertNotIn("watchdog-disarm.ps1", remote.calls)
        rows = self._resets()
        self.assertFalse(rows[0]["ok"])
        self.assertGreater(rows[0]["diff_count"], 0)
        self.assertIn("失败", reset_note(self.session))

    def test_revert_failure_still_starts_service(self) -> None:
        remote = ResetRemote(_live())
        remote.codes[_REVERT] = 1
        remote.revert_out = "数据库 X 被另一个连接以单用户模式占住"
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("单用户模式占住", out)
        self.assertIn("两个库可能只还原了一个", out)
        self.assertLess(remote.calls.index(_REVERT), remote.calls.index("service-start.ps1"))
        for name in _NEVER:
            self.assertNotIn(name, remote.calls)

    def test_missing_keep_confirmation_is_failure(self) -> None:
        remote = ResetRemote(_live())
        remote.revert_out = "snapshot-revert ok"
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("保留快照", out)
        self.assertIn("service-start.ps1", remote.calls)

    def test_old_script_refused_before_stop(self) -> None:
        remote = ResetRemote(_live())
        remote.script_text = "param(\n    [switch]$AllowMissing\n)\n"
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("旧版", out)
        for name in ("service-stop.ps1", _REVERT, "service-start.ps1"):
            self.assertNotIn(name, remote.calls)

    def test_stop_failure_aborts_before_restore(self) -> None:
        remote = ResetRemote(_live())
        remote.codes["service-stop.ps1"] = 1
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("服务没停下，不还原", out)
        self.assertNotIn(_REVERT, remote.calls)
        self.assertIn("service-start.ps1", remote.calls)
        self.assertNotIn("clear-inrevert.ps1", remote.calls)

    def test_gate_failure_touches_nothing(self) -> None:
        remote = ResetRemote(_live())
        remote.codes["session-gate.ps1"] = 1
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("会话窗口核对没通过 session-gate.ps1 失败", out)
        for name in ("service-stop.ps1", _REVERT, "service-start.ps1"):
            self.assertNotIn(name, remote.calls)

    def test_unreadable_script_is_distinct(self) -> None:
        remote = ResetRemote(_live())
        remote.script_text = ""
        original = remote.download

        def failing(remote_win: str, local: Path) -> None:
            if remote_win.endswith("\\snapshot-revert.ps1"):
                raise OSError("gone")
            original(remote_win, local)

        remote.download = failing  # type: ignore[method-assign]
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("读不到服务器上的 snapshot-revert.ps1", out)
        self.assertNotIn("service-stop.ps1", remote.calls)

    def test_watchdog_fired_during_restore(self) -> None:
        remote = ResetRemote(_live())
        remote.after_status = dict(_live(), snapshots=[], done=True)
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("看门狗已经还原", out)
        self.assertIn("session close", out)
        self.assertNotIn("clear-inrevert.ps1", remote.calls)
        self.assertFalse(self._resets()[0]["ok"])

    def test_clear_inrevert_failure_fails(self) -> None:
        remote = ResetRemote(_live())
        remote.codes["clear-inrevert.ps1"] = 1
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("close --keep 会拒绝", out)
        self.assertFalse(self._resets()[0]["ok"])

    def test_diff_counted_on_soft_failure(self) -> None:
        remote = ResetRemote(_live())
        remote.rows = 4
        remote.after_status = dict(_live(), done=True)
        code, out = _quiet(remote)
        self.assertEqual(code, 1)
        self.assertIn("看门狗已经还原", out)
        self.assertIn("行数 3 -> 4", out)
        self.assertGreater(self._resets()[0]["diff_count"], 0)
        self.assertNotIn("clear-inrevert.ps1", remote.calls)

    def test_second_reset_logs_separately(self) -> None:
        self.assertEqual(_quiet(ResetRemote(_live()))[0], 0)
        first = sorted(item.name for item in (self.session / "reset-1").glob("*.log"))
        self.assertEqual(_quiet(ResetRemote(_live()))[0], 0)
        second = sorted(item.name for item in (self.session / "reset-2").glob("*.log"))
        self.assertEqual(first, second)
        self.assertIn("00-session-gate.log", first)
        self.assertTrue(any(name.endswith("-revert.log") for name in second))
        self.assertEqual(len(self._resets()), 2)
        self.assertIn("2 次", reset_note(self.session))


if __name__ == "__main__":
    unittest.main()
