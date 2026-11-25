"""session extend：延长调试会话的看门狗，会话从打开起最长 240 分钟。不连接服务器。"""

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
from co.e2e.lanelock import EXCLUSIVE
from co.e2e.markers import read_linux_marker, write_linux_marker
from co.e2e.plan import session_watchdog_default
from co.e2e.remote import RemoteResult
from co.e2e.session import _parser
from co.e2e.session_extend import extend_session, plan_deadline
from co.e2e.tests import support

_HERE = Path(__file__).resolve().parents[1]


class ExtendRemote:
    def __init__(self, status: dict) -> None:
        self.calls: list[str] = []
        self.named: dict[str, dict] = {}
        self.codes: dict[str, int] = {}
        self.outs: dict[str, str] = {}
        self.status = status
        self.uploaded: list[dict] = []

    def run_script(self, name: str, named: dict | None, timeout: int) -> RemoteResult:
        del timeout
        self.calls.append(name)
        self.named[name] = dict(named or {})
        out = self.outs.get(name, name)
        if name == "session-status.ps1" and named and named.get("Tag"):
            out += "\ntag " + named["Tag"]
        return RemoteResult(self.codes.get(name, 0), out)

    def download(self, remote_win: str, local: Path) -> None:
        self.calls.append("download")
        doc = {"config": support.status_identity()}
        doc.update(self.status)
        local.write_text(json.dumps(doc), encoding="utf-8")

    def upload(self, local: Path, remote_win: str) -> None:
        self.calls.append("upload")
        if remote_win.endswith("session-body.json"):
            self.uploaded.append(json.loads(local.read_text(encoding="utf-8")))

    def ensure_dirs(self) -> RemoteResult:
        self.calls.append("ensure-dirs")
        return RemoteResult(0, "ok")


def _zone() -> ZoneInfo:
    return ZoneInfo(settings.conf().timezone)


def _utc_text(value: datetime) -> str:
    return value.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def _naive(value: datetime) -> str:
    # 服务器 NextRunTime.ToString("o") 在 Kind 未指定时不带偏移。
    return value.replace(tzinfo=None).isoformat()


class ExtendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data = support.sample_dict()
        data["report"] = {"dir": str(Path(self.tmp.name) / "runs")}
        support.install(settings.from_dict(data))
        self.now = datetime.now(_zone()).replace(microsecond=0)
        self.report = Path(self.tmp.name) / "runs" / "sess"
        self.report.mkdir(parents=True)

    def tearDown(self) -> None:
        support.reset()
        self.tmp.cleanup()

    def _open(self, opened_ago: int = 30, owner: str = "session") -> dict:
        payload = {
            "report_dir": str(self.report),
            "watchdog_min": 120,
            "opened_at": _utc_text(self.now - timedelta(minutes=opened_ago)),
            "secret_path": "/nonexistent/secret",
            "owner": owner,
        }
        write_linux_marker(payload)
        return payload

    def _status(self, left: float = 90, **changes: object) -> dict:
        doc = {
            "marker": True,
            "owner": "session",
            "snapshots": list(settings.conf().snapshots),
            "done": False,
            "watchdog_state": "Ready",
            "next_run": _naive(self.now + timedelta(minutes=left)),
        }
        doc.update(changes)
        return doc

    def _run(self, remote: ExtendRemote | None, minutes: int = 60) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = extend_session(minutes, remote)
        return code, out.getvalue()

    def _refused(self, remote: ExtendRemote, needle: str, minutes: int = 60) -> None:
        code, text = self._run(remote, minutes)
        self.assertEqual(code, 1, text)
        self.assertIn(needle, text)
        self.assertNotIn("watchdog-extend.ps1", remote.calls)
        self.assertNotIn("session-mark.ps1", remote.calls)

    def test_no_session_refused_without_remote(self) -> None:
        remote = ExtendRemote(self._status())
        self._refused(remote, "没有打开的会话")
        self.assertEqual(remote.calls, [])

    def test_minutes_out_of_range(self) -> None:
        self._open()
        for minutes in (9, 121):
            remote = ExtendRemote(self._status())
            self._refused(remote, "10 到 120", minutes)
            self.assertEqual(remote.calls, [])

    def test_foreign_owner_refused(self) -> None:
        self._open(owner="run")
        self._refused(ExtendRemote(self._status(owner="run")), "当前所有者：run")

    def test_watchdog_fired_or_done_refused(self) -> None:
        self._open()
        self._refused(ExtendRemote(self._status(next_run="fired")), "已触发")
        self._refused(ExtendRemote(self._status(watchdog_state="Running")), "已触发")
        self._refused(ExtendRemote(self._status(done=True)), "已还原")
        self._refused(ExtendRemote(self._status(watchdog_state="none", next_run="none")), "看门狗不在")
        self._refused(ExtendRemote(self._status(snapshots=[])), "快照不在")

    def test_less_than_one_minute_refused(self) -> None:
        self._open()
        self._refused(ExtendRemote(self._status(left=0.5)), "不足 1 分钟")

    def test_cap_reached_refused(self) -> None:
        # 打开 200 分钟，看门狗还剩 38 分钟：到点已在打开后 238 分钟，只能再加 2 分钟。
        self._open(opened_ago=200)
        self._refused(ExtendRemote(self._status(left=38)), "240 分钟上限")

    def test_happy_path(self) -> None:
        self._open(opened_ago=30)
        remote = ExtendRemote(self._status(left=90))
        code, text = self._run(remote, 60)
        self.assertEqual(code, 0, text)
        target = self.now + timedelta(minutes=150)
        until = remote.named["watchdog-extend.ps1"]["Until"]
        self.assertEqual(until, target.strftime("%Y-%m-%dT%H:%M:%S"))
        self.assertIn("extend ok, 看门狗到点 " + target.strftime("%H:%M"), text)
        self.assertIn("最多 240", text)
        self.assertNotIn("watchdog-arm.ps1", remote.calls)
        self.assertNotIn("watchdog-disarm.ps1", remote.calls)
        marker = read_linux_marker() or {}
        self.assertEqual(marker["deadline"], target.isoformat())
        self.assertEqual(marker["watchdog_min"], 180)
        self.assertEqual(marker["extends"], 1)
        self.assertEqual(marker["opened_at"], _utc_text(self.now - timedelta(minutes=30)))
        self.assertEqual(remote.uploaded[-1]["deadline"], target.isoformat())
        self.assertLess(remote.calls.index("watchdog-extend.ps1"), remote.calls.index("session-mark.ps1"))
        row = json.loads((self.report / "extend-1" / "extend.json").read_text(encoding="utf-8"))
        self.assertTrue(row["ok"])
        self.assertTrue((self.report / "extend-1" / "watchdog-extend.log").is_file())

    def test_capped_at_240(self) -> None:
        self._open(opened_ago=150)
        remote = ExtendRemote(self._status(left=60))
        code, text = self._run(remote, 120)
        self.assertEqual(code, 0, text)
        cap = self.now + timedelta(minutes=90)
        self.assertEqual(remote.named["watchdog-extend.ps1"]["Until"], cap.strftime("%Y-%m-%dT%H:%M:%S"))
        self.assertEqual((read_linux_marker() or {})["watchdog_min"], 240)

    def test_second_extend_numbered(self) -> None:
        self._open()
        self.assertEqual(self._run(ExtendRemote(self._status()), 30)[0], 0)
        self.assertEqual(self._run(ExtendRemote(self._status()), 30)[0], 0)
        self.assertTrue((self.report / "extend-2" / "extend.json").is_file())
        self.assertEqual((read_linux_marker() or {})["extends"], 2)

    def test_server_refusal_keeps_marker(self) -> None:
        before = self._open()
        remote = ExtendRemote(self._status())
        remote.codes["watchdog-extend.ps1"] = 1
        remote.outs["watchdog-extend.ps1"] = "会话已到 240 分钟上限，请 session close"
        code, text = self._run(remote)
        self.assertEqual(code, 1)
        self.assertIn("240 分钟上限", text)
        self.assertNotIn("session-mark.ps1", remote.calls)
        self.assertEqual(read_linux_marker(), before)

    def test_marker_failure_is_warning(self) -> None:
        self._open()
        remote = ExtendRemote(self._status())
        remote.codes["session-mark.ps1"] = 1
        code, text = self._run(remote)
        self.assertEqual(code, 0)
        self.assertIn("extend ok", text)
        self.assertIn("警告", text)


class ExtendRuleTests(unittest.TestCase):
    def test_plan_deadline(self) -> None:
        zone = ZoneInfo("Asia/Shanghai")
        opened = datetime(2026, 10, 2, 9, 0, tzinfo=zone)
        current = opened + timedelta(minutes=120)
        self.assertEqual(plan_deadline(current, opened, 60), (current + timedelta(minutes=60), ""))
        self.assertEqual(plan_deadline(current, opened, 120), (opened + timedelta(minutes=240), ""))
        late = opened + timedelta(minutes=236)
        self.assertEqual(plan_deadline(late, opened, 60)[0], None)
        self.assertIn("240", plan_deadline(late, opened, 60)[1])

    def test_extend_is_exclusive(self) -> None:
        self.assertIn("extend", EXCLUSIVE)

    def test_parser_defaults(self) -> None:
        self.assertEqual(_parser().parse_args(["extend"]).minutes, 60)
        self.assertEqual(_parser().parse_args(["extend", "--minutes", "30"]).minutes, 30)

    def test_session_default_is_120(self) -> None:
        support.install()
        try:
            self.assertEqual(settings.conf().session_minutes, 120)
            self.assertEqual(session_watchdog_default(), 120)
            data = support.sample_dict()
            data.pop("watchdog")
            self.assertEqual(settings.from_dict(data).session_minutes, 120)
        finally:
            support.reset()
        for name in ("e2e.example.json", "lane.example.json"):
            doc = json.loads((_HERE / name).read_text(encoding="utf-8"))
            self.assertEqual(doc["watchdog"]["session_minutes"], 120, name)
        self.assertEqual(support.lane_config().session_minutes, 120)


if __name__ == "__main__":
    unittest.main()
