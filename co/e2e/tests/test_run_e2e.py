"""计划、指纹差异和报告。不连接 U8。"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from co.e2e.cases import (
    Operator,
    blocked_without_com,
    cleared_ok,
    count_logins,
    load_operator,
    rows_since,
    should_retry_year,
    verified_ok,
    year_plan,
)
from co.e2e.fingerprint import diff_fingerprints, normalize, totals_of
from co.e2e.plan import (
    MIN_FREE_BYTES,
    build_plan,
    clamp_minutes,
    preflight_problems,
    preflight_warnings,
    secret_text_problem,
    snapshot_name,
)
from co.e2e.remote import encode_pwsh, fixed_call, script_call, win_to_scp
from co.e2e.report import assemble, exit_code, judge, render_markdown
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


_REMOTE = Path(__file__).resolve().parents[1] / "remote"
_SECRET = "super-secret-value"
_DATABASES = ("UFDATA_801_2024", "UFMeta_801")
_OWNER = r"U8SERVER\u8co-ops"


def _db(name: str) -> dict:
    return {"name": name, "state": "ONLINE", "access": "MULTI_USER", "owner": _OWNER}


def _healthy() -> dict:
    return {
        "expected_owner": _OWNER.lower(),
        "databases": [_db(name) for name in _DATABASES],
        "snapshots": [],
        "is_sysadmin": False,
        "create_any_database": True,
        "free_bytes": MIN_FREE_BYTES,
        "volume": "C:",
        "bridge_config_exists": True,
        "bridge_secret_exists": True,
        "service_exists": True,
        "service_running": True,
        "port_busy": True,
        "audit_log_readable": True,
        "session_marker": False,
        "allowed_accounts": ["801"],
        "foreign_db_access": [],
        "service_root_problem": "",
        "listen_port": 18089,
        "release_proc": True,
    }


def _table(schema: str, name: str, rows: int, checksum: int, error: str = "") -> dict:
    return {"schema": schema, "name": name, "rows": rows, "checksum": checksum, "error": error}


def _finger() -> dict:
    return {
        "databases": {
            "UFDATA_801_2024": {"tables": [_table("dbo", "SO_SOMain", 3, 9)]},
            "UFMeta_801": {"tables": [_table("dbo", "UA_Account", 1, 4)]},
        }
    }


class PlanTests(unittest.TestCase):
    def test_dry_run_is_read_only(self) -> None:
        effects = [step["effect"] for step in build_plan(True)]
        self.assertEqual(effects, ["read", "read"])

    def test_full_order(self) -> None:
        names = [step["name"] for step in build_plan(False)]
        self.assertLess(names.index("s4u_preflight"), names.index("snapshot"))
        self.assertLess(names.index("fingerprint_before"), names.index("snapshot"))
        self.assertLess(names.index("snapshot"), names.index("http_tests"))
        self.assertLess(names.index("http_tests"), names.index("revert"))
        self.assertLess(names.index("revert"), names.index("fingerprint_after"))

    def test_snapshot_name_rejects_other_accounts(self) -> None:
        self.assertEqual(snapshot_name("UFDATA_801_2024"), "UFDATA_801_2024_u8co_e2e_snap")
        with self.assertRaises(ValueError):
            snapshot_name(support.foreign_data_db())


class PreflightTests(unittest.TestCase):
    def test_healthy(self) -> None:
        self.assertEqual(preflight_problems(_healthy()), [])

    def test_leftover_snapshot_aborts(self) -> None:
        info = _healthy()
        info["snapshots"] = ["UFDATA_801_2024_u8co_e2e_snap"]
        text = " ".join(preflight_problems(info))
        self.assertIn("已有快照", text)

    def test_missing_bridge_config_aborts(self) -> None:
        info = _healthy()
        info["bridge_config_exists"] = False
        text = " ".join(preflight_problems(info))
        self.assertIn("config.json", text)

    def test_service_missing_aborts(self) -> None:
        info = _healthy()
        info["service_exists"] = False
        info["service_running"] = False
        self.assertIn("未注册服务", " ".join(preflight_problems(info)))

    def test_service_stopped_aborts(self) -> None:
        info = _healthy()
        info["service_running"] = False
        self.assertIn("未在运行", " ".join(preflight_problems(info)))

    def test_port_must_listen(self) -> None:
        info = _healthy()
        info["port_busy"] = False
        self.assertIn("18089", " ".join(preflight_problems(info)))

    def test_session_marker_aborts(self) -> None:
        info = _healthy()
        info["session_marker"] = True
        self.assertIn("会话", " ".join(preflight_problems(info)))

    def test_service_binding_checked(self) -> None:
        # 服务 binPath 不是本通道的桥、监听端口不是 bridge.port、或旧版预检没报这两项，都拒绝。
        info = _healthy()
        info["service_root_problem"] = "服务 u8co 的程序不是 X"
        self.assertIn("程序不是", " ".join(preflight_problems(info)))
        info = _healthy()
        info["listen_port"] = 18090
        self.assertIn("listenPrefix", " ".join(preflight_problems(info)))
        info = _healthy()
        del info["listen_port"]
        self.assertIn("旧版本", " ".join(preflight_problems(info)))

    def test_audit_unreadable_aborts(self) -> None:
        info = _healthy()
        info["audit_log_readable"] = False
        self.assertTrue(preflight_problems(info))

    def test_watchdog_minutes_clamp(self) -> None:
        self.assertEqual(clamp_minutes(25), 25)
        self.assertEqual(clamp_minutes(10), 10)
        self.assertEqual(clamp_minutes(120), 120)
        self.assertEqual(clamp_minutes(9), 10)
        self.assertEqual(clamp_minutes(121), 120)

    def test_secret_text_rules(self) -> None:
        sample = "ab" * 32
        self.assertIn("找不到", secret_text_problem(0o600, "", False))
        self.assertIn("权限", secret_text_problem(0o640, sample, True))
        self.assertEqual(secret_text_problem(0o600, sample, True), "")
        self.assertIn("格式", secret_text_problem(0o600, "GG", True))

    def test_allowed_accounts_must_be_the_lane_account(self) -> None:
        info = _healthy()
        info["allowed_accounts"] = ["801", "001"]
        self.assertTrue(preflight_problems(info))
        info["allowed_accounts"] = "001"
        self.assertTrue(preflight_problems(info))
        info["allowed_accounts"] = "801"
        self.assertEqual(preflight_problems(info), [])
        info.pop("allowed_accounts")
        self.assertTrue(preflight_problems(info))

    def test_extra_accounts_must_be_read_only(self) -> None:
        info = _healthy()
        info["allowed_accounts"] = ["801", "802", "803"]
        info["read_only_accounts"] = ["802"]
        self.assertTrue(preflight_problems(info))
        info["read_only_accounts"] = ["802", "803"]
        self.assertEqual(preflight_problems(info), [])
        info["read_only_accounts"] = ["801", "802", "803"]
        self.assertTrue(preflight_problems(info))

    def test_foreign_db_access_fails_closed(self) -> None:
        info = _healthy()
        info["foreign_db_access"] = [support.foreign_data_db()]
        self.assertTrue(preflight_problems(info))
        info.pop("foreign_db_access")
        self.assertTrue(preflight_problems(info))

    def test_owner_must_match(self) -> None:
        info = _healthy()
        info["expected_owner"] = ""
        self.assertTrue(any("所有者" in item for item in preflight_problems(info)))
        info = _healthy()
        info["databases"][0]["owner"] = r"U8SERVER\someone"
        self.assertTrue(any("所有者" in item for item in preflight_problems(info)))

    def test_snapshot_volume_follows_remote_root(self) -> None:
        info = _healthy()
        info["volume"] = "D:"
        self.assertTrue(any("快照目录" in item for item in preflight_problems(info)))

    def test_sysadmin_unknown_fails_closed(self) -> None:
        info = _healthy()
        info["is_sysadmin"] = None
        self.assertTrue(any("sysadmin" in item for item in preflight_problems(info)))

    def test_low_space(self) -> None:
        info = _healthy()
        info["free_bytes"] = MIN_FREE_BYTES - 1
        self.assertTrue(preflight_problems(info))

    def test_release_proc_only_warns(self) -> None:
        info = _healthy()
        self.assertEqual(preflight_warnings(info), [])
        info["release_proc"] = False
        self.assertEqual(preflight_problems(info), [])
        self.assertTrue(any("install-release-proc.sql" in item for item in preflight_warnings(info)))
        del info["release_proc"]
        self.assertTrue(any("旧版本" in item for item in preflight_warnings(info)))


class FingerprintTests(unittest.TestCase):
    def test_equal(self) -> None:
        self.assertEqual(diff_fingerprints(_finger(), _finger()), [])

    def test_row_change(self) -> None:
        after = _finger()
        after["databases"]["UFDATA_801_2024"]["tables"][0]["rows"] = 4
        text = " ".join(diff_fingerprints(_finger(), after))
        self.assertIn("SO_SOMain", text)
        self.assertIn("3 -> 4", text)

    def test_extra_table(self) -> None:
        after = _finger()
        after["databases"]["UFMeta_801"]["tables"].append(_table("dbo", "Extra", 1, 1))
        self.assertTrue(diff_fingerprints(_finger(), after))

    def test_rejects_other_database(self) -> None:
        doc = _finger()
        doc["databases"][support.foreign_data_db()] = {"tables": []}
        self.assertTrue(diff_fingerprints(doc, doc))

    def test_totals_ignore_order(self) -> None:
        left = [_table("dbo", "A", 1, -1), _table("dbo", "B", 2, 5)]
        right = [_table("dbo", "B", 2, 5), _table("dbo", "A", 1, -1)]
        self.assertEqual(totals_of(left), totals_of(right))
        doc = _finger()
        doc["databases"]["UFDATA_801_2024"]["tables"] = left
        other = _finger()
        other["databases"]["UFDATA_801_2024"]["tables"] = right
        self.assertEqual(normalize(doc), normalize(other))


class CaseTests(unittest.TestCase):
    def test_year_retry(self) -> None:
        self.assertEqual(year_plan("2024", "2026-09-27"), ["2024", "2026"])
        self.assertEqual(year_plan("2026", "2026-09-27"), ["2026"])
        self.assertTrue(should_retry_year("登录日期不在会计期间内"))
        self.assertFalse(should_retry_year("口令错误"))

    def test_rows_since_ignores_earlier_rows(self) -> None:
        rows = [
            {"ts": "2026-09-27T00:00:00Z", "acc": "001", "outcome": "login_failed", "operator": "op001", "year": "2024"},
            {"ts": "2026-09-27T02:00:00Z", "acc": "001", "outcome": "account_not_allowed", "operator": "", "year": ""},
        ]
        kept = rows_since(rows, "2026-09-27T01:00:00Z")
        ok, _detail = blocked_without_com(kept)
        self.assertEqual(len(kept), 1)
        self.assertTrue(ok)

    def test_blocked_without_com(self) -> None:
        ok, _detail = blocked_without_com([{"acc": "001", "outcome": "account_not_allowed", "operator": "", "year": ""}])
        self.assertTrue(ok)
        bad, _detail = blocked_without_com([{"acc": "001", "outcome": "login_failed", "operator": "op001", "year": "2024"}])
        self.assertFalse(bad)

    def test_count_logins_skips_rejected(self) -> None:
        rows = [
            {"path": "/u8co/v1/login-check", "outcome": "account_not_allowed"},
            {"path": "/u8co/v1/login-check", "outcome": "unauthorized"},
            {"path": "/u8co/v1/login-check", "outcome": "ok"},
            {"path": "/u8co/v1/health", "outcome": "ok"},
            {"path": "/u8co/v1/sale-orders/verify", "outcome": "ok"},
        ]
        self.assertEqual(count_logins(rows), 2)

    def test_verify_state(self) -> None:
        self.assertTrue(verified_ok({"cVerifier": "张三", "dverifydate": "2026-09-27 00:00:00"}, "张三"))
        self.assertFalse(verified_ok({"cVerifier": "", "dverifydate": ""}, "张三"))
        self.assertTrue(cleared_ok({"cVerifier": "  ", "dverifydate": None}))

    def test_repr_hides_password(self) -> None:
        self.assertNotIn(_SECRET, repr(Operator("801", "2024", "op001", _SECRET)))

    def test_load_operator_rejects_other_account(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cred.json"
            payload = {"acc": "001", "year": "2024", "operator": "op001", "password": _SECRET}
            path.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaises(RuntimeError):
                load_operator(path)


class ReportTests(unittest.TestCase):
    def test_hides_secret_and_marks_rollback(self) -> None:
        state = {
            "dry_run": False,
            "failed": False,
            "snapshots_made": True,
            "reverted": True,
            "fingerprint_diff": [],
            "tests": [{"name": "健康检查", "status": "PASS", "detail": "ok " + _SECRET}],
            "needles": [_SECRET],
            "before": {},
            "after": {},
            "password": _SECRET,
            "u8_logins": 1,
        }
        report = assemble(state)
        text = json.dumps(report, ensure_ascii=False) + render_markdown(report)
        self.assertNotIn(_SECRET, text)
        self.assertNotIn("password", report)
        self.assertTrue(report["rollback_verified"])
        self.assertEqual(report["overall"], "PASS")
        self.assertEqual(exit_code(report["overall"]), 0)

    def test_skip_is_not_pass(self) -> None:
        verdict = judge(False, False, True, [{"status": "SKIP"}])
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(exit_code("DRY-RUN"), 0)
        self.assertEqual(judge(True, False, False, []), "DRY-RUN")

    def test_manual_when_revert_fails(self) -> None:
        report = assemble(
            {
                "dry_run": False,
                "failed": True,
                "snapshots_made": True,
                "reverted": False,
                "fingerprint_diff": ["UFDATA_801_2024 有差异"],
                "before": {},
                "after": {},
                "tests": [],
            }
        )
        self.assertIn("u8co-e2e-revert", report["manual"])
        self.assertFalse(report["rollback_verified"])
        self.assertIn("不要手工删除快照", render_markdown(report))


class RemoteContractTests(unittest.TestCase):
    def test_encode_and_args(self) -> None:
        self.assertEqual(base64.b64decode(encode_pwsh("ok")).decode("utf-16-le"), "ok")
        call = script_call(
            "fingerprint.ps1",
            {"Database": "UFDATA_801_2024", "OutFile": r"C:\ProgramData\U8Co\u8co-e2e\out\fp.json"},
        )
        self.assertIn(r"C:\ProgramData\U8Co\u8co-e2e\fingerprint.ps1", call)
        with self.assertRaises(ValueError):
            script_call("fingerprint.ps1", {"Database": "UFDATA_801_2024'; DROP"})
        with self.assertRaises(ValueError):
            fixed_call("uninstall")
        with self.assertRaises(ValueError):
            fixed_call("console")
        with self.assertRaises(ValueError):
            win_to_scp(r"D:\U8SOFT\data.mdf")
        with self.assertRaises(ValueError):
            win_to_scp(r"C:\ProgramData\U8Co\u8co-e2e\..\u8co\secret.hex")
        self.assertEqual(win_to_scp(r"C:\ProgramData\U8Co\u8co-e2e\out\a.json"), "/C:/ProgramData/U8Co/u8co-e2e/out/a.json")
        self.assertIn(r"kit\bridge\build.ps1", fixed_call("build"))
        self.assertNotIn("--root", fixed_call("check"))

    def test_scripts_carry_no_site_values(self) -> None:
        # 账套、库名、路径、服务名都从 e2e.json 读；脚本里不能再写死。
        banned = ("UFDATA_0", "UFMeta_0", "801", "802", "ProgramData", "U8SOFT", "1433", "18089", "u8co-ops")
        for path in _REMOTE.glob("*.ps1"):
            text = path.read_text(encoding="utf-8")
            self.assertIn("#Requires -Version 7.4", text)
            self.assertNotIn("-Recurse", text)
            for name in banned:
                self.assertNotIn(name, text, path.name)
        common = (_REMOTE / "common.ps1").read_text(encoding="utf-8")
        self.assertIn("DeniedDbs", common)
        self.assertIn('Join-Path $PSScriptRoot "e2e.json"', common)
        self.assertIn("RemoteRoot 不是脚本所在目录", common)
        revert = (_REMOTE / "snapshot-revert.ps1").read_text(encoding="utf-8")
        self.assertIn("SET SINGLE_USER WITH ROLLBACK IMMEDIATE", revert)
        self.assertIn("RESTORE DATABASE", revert)
        self.assertIn("SET MULTI_USER", revert)
        self.assertIn("Get-SnapName (Get-DataDb)", revert)
        self.assertIn("Get-SnapName (Get-MetaDb)", revert)
        arm = (_REMOTE / "watchdog-arm.ps1").read_text(encoding="utf-8")
        self.assertIn("S4U", arm)
        self.assertIn("[Parameter(Mandatory = $true)][int]$Minutes", arm)
        self.assertIn("-lt 10", arm)
        self.assertIn("-gt 120", arm)
        self.assertIn("AddMinutes($Minutes)", arm)
        self.assertNotIn("AddMinutes(25)", arm)
        fire = (_REMOTE / "watchdog-fire.ps1").read_text(encoding="utf-8")
        self.assertLess(fire.index("service-stop.ps1"), fire.index("snapshot-revert.ps1"))
        self.assertLess(fire.index("snapshot-revert.ps1"), fire.index("service-start.ps1"))
        redeploy = (_REMOTE / "service-redeploy.ps1").read_text(encoding="utf-8")
        self.assertIn("Test-Protected $Full", redeploy)
        self.assertNotIn("secret.hex", redeploy)
        self.assertNotIn("urlacl", redeploy)
        self.assertIn("CHECKSUM_AGG(BINARY_CHECKSUM(*))", (_REMOTE / "fingerlib.ps1").read_text(encoding="utf-8"))

    def test_restore_and_guard_contracts(self) -> None:
        revert = (_REMOTE / "snapshot-revert.ps1").read_text(encoding="utf-8")
        self.assertIn("AllowMissing", revert)
        self.assertIn("U8REVERT_TIME", revert)
        self.assertGreater(revert.count("SET SINGLE_USER WITH ROLLBACK IMMEDIATE"), 1)
        fire = (_REMOTE / "watchdog-fire.ps1").read_text(encoding="utf-8")
        self.assertIn("-AllowMissing", fire)
        flow = (_REMOTE.parent / "flow.py").read_text(encoding="utf-8")
        self.assertNotIn("AllowMissing", flow)
        sql = (_REMOTE / "session-sql.ps1").read_text(encoding="utf-8")
        self.assertIn("Assert-LiveWindow", sql)
        self.assertIn("-x -X1", sql)
        status = (_REMOTE / "session-status.ps1").read_text(encoding="utf-8")
        self.assertLess(status.index("NextRunTime"), status.index("ToString"))
        self.assertIn('"fired"', status)
        arm = (_REMOTE / "watchdog-arm.ps1").read_text(encoding="utf-8")
        self.assertIn("RunLevel Highest", arm)
        stop = (_REMOTE / "service-stop.ps1").read_text(encoding="utf-8")
        self.assertIn("-NoWait", stop)
        # SCM 报 Stopped 之后仍等本通道的桥进程退出，不退就按路径和进程号结束（bridgelib.ps1）。
        self.assertIn("bridgelib.ps1", stop)
        self.assertLess(stop.index("Save-ServicePid"), stop.index("Request-BridgeStop\n"))
        self.assertIn("Stop-BridgeLeftover 15", stop)
        lib = (_REMOTE / "bridgelib.ps1").read_text(encoding="utf-8")
        self.assertIn('Get-BridgePath ("bin\\" + $exe)', lib)
        self.assertIn("throw", lib[lib.index("function Stop-BridgeLeftover") :])
        redeploy = (_REMOTE / "service-redeploy.ps1").read_text(encoding="utf-8")
        self.assertIn('$script:Exe + ".prev"', redeploy)
        self.assertIn("Assert-NoReparse", redeploy)
        session = (_REMOTE.parent / "session.py").read_text(encoding="utf-8")
        self.assertIn("SIGTERM", session)
        guard = (_REMOTE.parent / "guard.py").read_text(encoding="utf-8")
        self.assertIn("session-gate.ps1", guard)
        call = (_REMOTE.parent / "session_call.py").read_text(encoding="utf-8")
        self.assertIn("assert_window", call)

    def test_service_mode_does_not_touch_installed_files(self) -> None:
        root = _REMOTE.parent
        for name in ("engine.py", "flow.py", "bridge_job.py", "session_ops.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("bridge-cleanup", text)
            self.assertNotIn("bridge-prepare", text)
            self.assertNotIn("popen_console", text)


if __name__ == "__main__":
    unittest.main()
