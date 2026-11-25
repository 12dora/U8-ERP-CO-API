"""会话标记、看门狗触发后的 close、SQL 守卫。不连接服务器。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from co.e2e.callshape import merge_call_fields, normalize_call_path
from co.e2e.cases import Operator
from co.e2e.context import Run
from co.e2e.flow import arm_snapshot, run_close_remote, stop_and_revert
from co.e2e.markers import close_plan, markers_cleared, refusal_reason
from co.e2e.remote import RemoteResult
from co.e2e.safety import minutes_left, unique_report_dir, window_block_reason
from co.e2e.session_ops import fail_open
from co.e2e.sqlguard import sql_block_reason
from co.e2e.taskcfg import classify
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


_ZONE = ZoneInfo("Asia/Shanghai")
_SNAPS = ["UFDATA_801_2024_u8co_e2e_snap", "UFMeta_801_u8co_e2e_snap"]


class FakeRemote:
    def __init__(self, revert_code: int = 0) -> None:
        self.calls: list[str] = []
        self.revert_code = revert_code
        self.codes: dict[str, int] = {}
        self.revert_out = ""
        self.status: dict = {}

    def run_script(self, name: str, named: dict | None, timeout: int) -> RemoteResult:
        del timeout
        self.calls.append(name)
        if name in self.codes:
            code = self.codes[name]
        elif name == "snapshot-revert.ps1":
            code = self.revert_code
        else:
            code = 0
        out = self.revert_out if name == "snapshot-revert.ps1" and self.revert_out else name
        if name == "session-status.ps1" and named and named.get("Tag"):
            # 新版 session-status.ps1 收到 -Tag 时回报「tag <标记>」（runfiles.status_tagged）。
            out += "\ntag " + named["Tag"]
        return RemoteResult(code, out)

    def download(self, remote_win: str, local: Path) -> None:
        self.calls.append("download")
        doc = {"config": support.status_identity()} if remote_win.endswith("session-status.json") else {}
        doc.update(self.status if remote_win.endswith("session-status.json") else {})
        local.write_text(json.dumps(doc), encoding="utf-8")

    def upload(self, local: Path, remote_win: str) -> None:
        del local, remote_win
        self.calls.append("upload")

    def ensure_dirs(self) -> RemoteResult:
        return RemoteResult(0, "ok")


def _run(remote: FakeRemote) -> Run:
    return Run(False, Path(tempfile.mkdtemp()), remote)


class MarkerTests(unittest.TestCase):
    def test_open_refused_when_either_marker_exists(self) -> None:
        self.assertEqual(refusal_reason(False, False), "")
        self.assertIn("close", refusal_reason(True, False))
        self.assertIn("close", refusal_reason(False, True))

    def test_close_plan(self) -> None:
        idle = {"snapshots": [], "done": False, "marker": False, "watchdog_state": "none"}
        self.assertEqual(close_plan(None, idle, "session"), "already-closed")
        watched = {"snapshots": [], "done": True, "marker": False, "watchdog_state": "Ready"}
        self.assertEqual(close_plan({"report_dir": "/tmp/x", "owner": "session"}, watched, "session"), "verify-only")
        revert = {"snapshots": _SNAPS, "done": False, "marker": True, "watchdog_state": "Ready"}
        self.assertEqual(close_plan(None, revert, "session"), "revert")
        armed = {"snapshots": [], "done": False, "marker": False, "watchdog_state": "Ready"}
        self.assertNotEqual(close_plan(None, armed, "session"), "already-closed")
        foreign = {"snapshots": _SNAPS, "done": False, "watchdog_state": "Ready", "owner": "run"}
        self.assertEqual(close_plan({"owner": "run"}, foreign, "session"), "foreign")
        quiet = {"snapshots": [], "done": True, "watchdog_state": "none", "owner": "run"}
        self.assertEqual(close_plan({"owner": "run"}, quiet, "session"), "foreign")
        self.assertEqual(close_plan({"owner": "session"}, foreign, "run"), "foreign")

    def test_markers_cleared_only_after_success(self) -> None:
        self.assertTrue(markers_cleared("verify-only", False, True, False))
        self.assertFalse(markers_cleared("verify-only", True, False, True))
        self.assertFalse(markers_cleared("revert", False, True, True))
        self.assertFalse(markers_cleared("revert", True, True, False))
        self.assertTrue(markers_cleared("revert", True, True, True))
        self.assertFalse(markers_cleared("already-closed", True, True, True))


class CloseRemoteTests(unittest.TestCase):
    def test_already_closed_does_not_call_remote(self) -> None:
        remote = FakeRemote()
        run_close_remote(_run(remote), "already-closed")
        self.assertEqual(remote.calls, [])

    def test_watchdog_fired_skips_restore(self) -> None:
        remote = FakeRemote()
        run_close_remote(_run(remote), "verify-only")
        self.assertNotIn("snapshot-revert.ps1", remote.calls)
        self.assertIn("service-start.ps1", remote.calls)
        self.assertIn("watchdog-disarm.ps1", remote.calls)

    def test_revert_failure_still_starts_service(self) -> None:
        remote = FakeRemote(revert_code=1)
        ctx = _run(remote)
        ctx.snapshots_made = True
        ctx.watchdog = True
        stop_and_revert(ctx)
        self.assertLess(remote.calls.index("service-stop.ps1"), remote.calls.index("snapshot-revert.ps1"))
        self.assertGreater(remote.calls.index("service-start.ps1"), remote.calls.index("snapshot-revert.ps1"))
        self.assertNotIn("watchdog-disarm.ps1", remote.calls)
        self.assertNotIn("session-clear.ps1", remote.calls)
        self.assertFalse(ctx.reverted)

    def test_revert_success_starts_after_disarm(self) -> None:
        remote = FakeRemote(0)
        ctx = _run(remote)
        ctx.snapshots_made = True
        ctx.watchdog = True
        stop_and_revert(ctx)
        self.assertLess(remote.calls.index("snapshot-revert.ps1"), remote.calls.index("watchdog-disarm.ps1"))
        self.assertLess(remote.calls.index("watchdog-disarm.ps1"), remote.calls.index("service-start.ps1"))
        self.assertTrue(ctx.reverted)
        self.assertTrue(ctx.cleanup_closed.is_set())

    def test_no_snapshot_leaves_cleanup_open(self) -> None:
        remote = FakeRemote()
        ctx = _run(remote)
        stop_and_revert(ctx)
        self.assertEqual(remote.calls, [])
        self.assertFalse(ctx.cleanup_closed.is_set())

    def test_revert_closes_cleanup_before_service_stop(self) -> None:
        holder = []
        closed = []

        class Tracking(FakeRemote):
            def run_script(self, name, named, timeout):
                if name == "service-stop.ps1" and holder:
                    closed.append(holder[0].cleanup_closed.is_set())
                return super().run_script(name, named, timeout)

        remote = Tracking(0)
        ctx = _run(remote)
        holder.append(ctx)
        ctx.snapshots_made = True
        ctx.watchdog = True
        stop_and_revert(ctx)
        self.assertEqual(closed, [True])
        self.assertIn("service-stop.ps1", remote.calls)

    def test_missing_snapshot_is_failure(self) -> None:
        remote = FakeRemote(0)
        remote.revert_out = "已无快照 UFDATA_801_2024_u8co_e2e_snap"
        ctx = _run(remote)
        ctx.snapshots_made = True
        ctx.watchdog = True
        stop_and_revert(ctx)
        self.assertFalse(ctx.reverted)
        self.assertTrue(any("已无快照" in item for item in ctx.reasons))

    def test_snapshot_flag_before_create(self) -> None:
        remote = FakeRemote()
        remote.codes["snapshot-create.ps1"] = 1
        ctx = _run(remote)
        with self.assertRaises(RuntimeError):
            arm_snapshot(ctx)
        self.assertTrue(ctx.snapshots_made)
        self.assertFalse(ctx.watchdog)

    def test_watchdog_flag_before_arm(self) -> None:
        remote = FakeRemote()
        remote.codes["watchdog-arm.ps1"] = 1
        ctx = _run(remote)
        with self.assertRaises(RuntimeError):
            arm_snapshot(ctx)
        self.assertTrue(ctx.snapshots_made)
        self.assertTrue(ctx.watchdog)

    def test_foreign_config_blocks_restart(self) -> None:
        remote = FakeRemote(0)
        remote.status = {"config": dict(support.status_identity(), account="802")}
        ctx = _run(remote)
        ctx.snapshots_made = True
        ctx.watchdog = True
        stop_and_revert(ctx)
        self.assertNotIn("snapshot-revert.ps1", remote.calls)
        self.assertNotIn("service-start.ps1", remote.calls)
        self.assertTrue(any("不一致" in item for item in ctx.reasons))

    def test_open_failure_reverts_without_marker(self) -> None:
        remote = FakeRemote()
        ctx = _run(remote)
        ctx.snapshots_made = True
        fail_open(ctx)
        self.assertIn("snapshot-revert.ps1", remote.calls)
        self.assertNotIn("session-mark.ps1", remote.calls)


class SqlGuardTests(unittest.TestCase):
    def test_rejects_other_databases(self) -> None:
        self.assertTrue(sql_block_reason("SELECT * FROM " + support.foreign_data_db(0) + ".dbo.SO_SOMain"))
        self.assertTrue(sql_block_reason("SELECT * FROM " + support.foreign_data_db(1) + ".dbo.t"))
        self.assertTrue(sql_block_reason("UPDATE " + support.foreign_data_db(2) + ".dbo.t SET a=1"))
        self.assertTrue(sql_block_reason("SELECT * FROM " + support.foreign_meta_db(0) + ".dbo.t"))
        self.assertTrue(sql_block_reason("SELECT * FROM UFSystem.dbo.UA_User"))
        self.assertTrue(sql_block_reason("SELECT name FROM master.sys.databases"))
        self.assertTrue(sql_block_reason("SELECT * FROM UFDATA_801_2024..SO_SOMain"))
        self.assertTrue(sql_block_reason("SELECT * FROM D:\\U8SOFT\\x"))
        self.assertEqual(sql_block_reason("SELECT TOP 1 ID FROM SO_SOMain"), "")
        self.assertEqual(sql_block_reason("UPDATE dbo.SO_SOMain SET cVerifier=N'' WHERE ID=1"), "")
        self.assertTrue(sql_block_reason("select * from " + support.foreign_data_db(0).lower() + ".dbo.t"))
        self.assertTrue(sql_block_reason("SELECT * FROM d:/anything"))
        self.assertTrue(sql_block_reason("SELECT N'D:\\U8SOFT'"))

    def test_protected_paths_follow_config(self) -> None:
        support.install(support.make_config(server__u8_home="C:\\U8SOFT"))
        try:
            self.assertTrue(sql_block_reason("SELECT N'c:\\u8soft\\x'"))
            self.assertTrue(sql_block_reason("SELECT N'C:/U8SOFT'"))
            self.assertEqual(sql_block_reason("SELECT N'C:\\U8SOFTX'"), "")
            self.assertEqual(sql_block_reason("SELECT N'D:\\other'"), "")
        finally:
            support.install()

    def test_rejects_sqlcmd_and_dynamic_sql(self) -> None:
        payloads = [
            ":setvar x 1\nSELECT 1",
            "!! calc.exe",
            "ED",
            "EXIT",
            "QUIT",
            "RESET",
            "SELECT $(var)",
            "EXEC sp_help",
            "execute sp_help",
            "EXEC sp_executesql N'SELECT 1'",
            "EXEC sp_MSforeachdb N'SELECT 1'",
            "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x', 'SELECT 1')",
            "SELECT * FROM OPENQUERY(linked, 'SELECT 1')",
            "SELECT * FROM OPENDATASOURCE('SQLNCLI', 'Data Source=x')",
            "BACKUP DATABASE UFDATA_801_2024 TO DISK = N'C:\\x.bak'",
            "RESTORE DATABASE UFDATA_801_2024 FROM DATABASE_SNAPSHOT = N'x'",
            "USE master",
            "ALTER DATABASE UFDATA_801_2024 SET MULTI_USER",
            "DROP DATABASE [UFDATA_801_2024_u8co_e2e_snap]",
            "CREATE DATABASE hack",
            "CREATE SYNONYM s FOR dbo.t",
            "BULK INSERT dbo.t FROM 'C:\\x'",
            "DBCC CHECKDB",
            "EXEC xp_cmdshell 'dir'",
            "SHUTDOWN",
            "KILL 55",
            "GRANT SELECT ON dbo.t TO public",
            "REVOKE SELECT ON dbo.t FROM public",
            "DENY SELECT ON dbo.t TO public",
            "CREATE LOGIN u WITH PASSWORD = 'x'",
            "ALTER LOGIN u DISABLE",
            "CREATE USER u",
            "ALTER USER u WITH LOGIN = u",
            "SELECT * FROM [a].[b].[c]",
            "SELECT * FROM a.b.c",
            "SELECT * FROM a.b.c.d",
        ]
        for text in payloads:
            self.assertTrue(sql_block_reason(text), text)

    def test_call_shape_drops_password(self) -> None:
        operator = Operator("801", "2024", "op001", "hidden-pass")
        body = merge_call_fields({"password": "nope", "id": 3}, operator, "2026-09-27")
        self.assertNotIn("password", body)
        self.assertNotIn("hidden-pass", str(body))
        self.assertEqual(body["acc"], "801")
        self.assertEqual(body["date"], "2026-09-27")
        self.assertEqual(body["id"], 3)
        given = merge_call_fields({"date": "2026-01-01"}, operator, "2026-09-27")
        self.assertEqual(given["date"], "2026-01-01")
        self.assertEqual(normalize_call_path("health"), "health")
        self.assertEqual(normalize_call_path("/v1/login-check"), "v1/login-check")
        self.assertEqual(normalize_call_path("/u8co/v1/sale-orders/verify"), "v1/sale-orders/verify")
        self.assertEqual(normalize_call_path("u8co/v1/login-check"), "v1/login-check")
        self.assertEqual(normalize_call_path("v1/../admin"), "")
        with self.assertRaises(RuntimeError):
            merge_call_fields({"acc": "001"}, operator, "2026-09-27")


class WindowTests(unittest.TestCase):
    def test_null_next_run_labels(self) -> None:
        self.assertEqual(minutes_left("fired"), "已触发")
        self.assertEqual(minutes_left("none"), "无")
        self.assertEqual(minutes_left(""), "无")

    def test_refuses_after_watchdog_fired(self) -> None:
        now = datetime(2026, 9, 27, 3, 0, tzinfo=_ZONE)
        done = {"snapshots": _SNAPS, "done": True, "next_run": "2026-09-27T04:00:00+08:00"}
        self.assertIn("还原", window_block_reason(done, now))
        fired = {"snapshots": _SNAPS, "done": False, "watchdog_state": "Ready", "next_run": "fired"}
        self.assertIn("触发", window_block_reason(fired, now))
        absent = {"snapshots": _SNAPS, "done": False, "watchdog_state": "none", "next_run": "none"}
        self.assertIn("不在", window_block_reason(absent, now))
        short = {
            "snapshots": _SNAPS,
            "done": False,
            "watchdog_state": "Ready",
            "next_run": "2026-09-27T03:04:00+08:00",
        }
        self.assertIn("5", window_block_reason(short, now))
        open_window = {
            "snapshots": _SNAPS,
            "done": False,
            "watchdog_state": "Ready",
            "next_run": "2026-09-27T03:30:00+08:00",
        }
        self.assertEqual(window_block_reason(open_window, now), "")
        missing = {"snapshots": [_SNAPS[0]], "done": False, "next_run": "2026-09-27T03:30:00+08:00"}
        self.assertIn("快照", window_block_reason(missing, now))

    def test_report_dirs_are_unique(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = unique_report_dir(root, "20260927")
            second = unique_report_dir(root, "20260927")
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir() and second.is_dir())


def _task_row(name: str, pre: str, nxt: str, rest: str = "9") -> dict:
    return {"name": name, "pre": pre, "next": nxt, "ts": "1", "app": "a", "rest": rest}


class TaskCfgTests(unittest.TestCase):
    def test_live_scheduler_row_passes(self) -> None:
        old = _task_row("job", "2026-09-27 02:28:00.000", "2026-09-27 02:29:00.000")
        new = _task_row("job", "2026-09-27 02:46:00.000", "2026-09-27 02:47:00.000")
        new["ts"] = "2"
        new["app"] = "b"
        ok, notes = classify({"rows": [old]}, {"rows": [new]}, "2026-09-27 02:46:34.000")
        self.assertTrue(ok)
        self.assertTrue(notes)

    def test_rejects_rest_or_early_pre(self) -> None:
        old = _task_row("job", "2026-09-27 02:28:00", "2026-09-27 02:29:00")
        changed = _task_row("job", "2026-09-27 02:46:00", "2026-09-27 02:47:00", rest="8")
        ok, _notes = classify({"rows": [old]}, {"rows": [changed]}, "2026-09-27 02:46:34")
        self.assertFalse(ok)
        early = _task_row("job", "2026-09-27 02:40:00", "2026-09-27 02:47:00")
        ok, _notes = classify({"rows": [old]}, {"rows": [early]}, "2026-09-27 02:46:34")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
