"""多条调试通道：配置文件的选择、防撞名的启发式检查、并行调用的临时文件名。不连接服务器。"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from co.e2e import lane, settings
from co.e2e.guard import fetch_status
from co.e2e.lanelock import EXCLUSIVE, SHARED, session_lock
from co.e2e.mux import prepare_control_path
from co.e2e.remote import RemoteResult
from co.e2e.runfiles import run_tag, tagged
from co.e2e.session_io import _read_audit_day, sql_session
from co.e2e.settings import ConfigError, from_dict
from co.e2e.tests import support

_HERE = Path(__file__).resolve().parents[1]
_REMOTE = _HERE / "remote"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _default_with(**changes: object) -> dict:
    data = copy.deepcopy(support.sample_dict())
    for dotted, value in changes.items():
        section, key = dotted.split("__", 1)
        data.setdefault(section, {})[key] = value
    return data


class ConfigSelectionTests(unittest.TestCase):
    def tearDown(self) -> None:
        support.install()

    def test_env_var_selects_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "802.json"
            path.write_text(json.dumps(support.lane_dict("802")), encoding="utf-8")
            with mock.patch.dict(os.environ, {settings.ENV_PATH: str(path)}):
                self.assertEqual(settings.config_path(), path)
                settings.use(None)
                cfg = settings.conf()
        self.assertEqual(cfg.account, "802")
        self.assertEqual(cfg.lane, "l802")
        self.assertEqual(cfg.revert_task, "u8co-e2e-802-revert")

    def test_default_path_without_env(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != settings.ENV_PATH}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(settings.config_path(), Path("~/.config/u8co/e2e.json").expanduser())

    def test_missing_file_names_env_var(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            settings.load(Path("/nonexistent/u8co-lane.json"))
        self.assertIn(settings.ENV_PATH, str(caught.exception))

    def test_only_settings_knows_the_config_path(self) -> None:
        # run_e2e、session、comprobe 都经 settings.conf()；别的模块不能自己去读 e2e.json。
        roots = [_HERE, _HERE.parent / "probe"]
        for root in roots:
            for path in root.rglob("*.py"):
                if "tests" in path.parts or path.name == "settings.py":
                    continue
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(".config/u8co/e2e.json", text, path.name)
                self.assertNotIn(settings.ENV_PATH, text, path.name)
                self.assertIsNone(re.search(r"settings\.load\(|from co\.e2e\.settings import[^\n]*\bload\b", text), path.name)


class LaneHeuristicTests(unittest.TestCase):
    def test_default_lane_is_not_checked(self) -> None:
        cfg = from_dict(support.sample_dict())
        self.assertEqual(cfg.lane, "")
        self.assertEqual(lane.lane_problems(cfg, "/nonexistent"), [])

    def test_lane_example_loads(self) -> None:
        cfg = settings.load(_HERE / "lane.example.json")
        self.assertEqual((cfg.account, cfg.lane), ("802", "l802"))
        self.assertIn("801", cfg.denied)

    def test_other_account_without_lane_is_not_checked(self) -> None:
        data = _default_with(account__code="802", account__denied=[])
        cfg = from_dict(data)
        self.assertEqual(cfg.account, "802")
        self.assertEqual(cfg.lane, "")
        self.assertEqual(lane.lane_problems(cfg, "/nonexistent"), [])

    def test_other_account_with_lane_and_default_names_rejected(self) -> None:
        data = _default_with(account__code="802", account__denied=[])
        data["lane"] = "l802"
        with self.assertRaises(ConfigError) as caught:
            from_dict(data)
        text = str(caught.exception)
        for label in ("server.remote_root", "snapshot.suffix", "snapshot.task_prefix", "bridge.root"):
            self.assertIn(label, text)
        for label in ("bridge.service_name", "bridge.port", "report.dir", "server.ssh_user"):
            self.assertIn(label, text)

    def test_lane_tag_on_default_account_is_checked(self) -> None:
        data = support.sample_dict()
        data["lane"] = "dev2"
        with self.assertRaises(ConfigError):
            from_dict(data)

    def test_names_must_carry_account_or_tag(self) -> None:
        data = support.lane_dict("802")
        data["snapshot"]["task_prefix"] = "u8co-other"
        with self.assertRaises(ConfigError) as caught:
            from_dict(data)
        self.assertIn("snapshot.task_prefix", str(caught.exception))
        self.assertNotIn("snapshot.suffix", str(caught.exception))
        data["snapshot"]["task_prefix"] = "u8co-l802"
        self.assertEqual(from_dict(data).revert_task, "u8co-l802-revert")

    def test_parent_dir_does_not_count(self) -> None:
        # 只看目录的最后一段：上级目录里碰巧含账套号不算。
        data = support.lane_dict("802")
        data["bridge"]["root"] = "C:\\U8Co802\\bridge"
        with self.assertRaises(ConfigError) as caught:
            from_dict(data)
        self.assertIn("bridge.root", str(caught.exception))

    def test_bad_lane_tag(self) -> None:
        for bad in ("L2", "a", "lane-2", 7, "x" * 13):
            data = support.lane_dict("802")
            data["lane"] = bad
            with self.assertRaises(ConfigError, msg=repr(bad)):
                from_dict(data)

    def test_fingerprints_of_two_lanes_differ_everywhere(self) -> None:
        main = lane.fingerprint(from_dict(support.sample_dict()))
        other = lane.fingerprint(support.lane_config("802"))
        self.assertEqual(set(main), set(other))
        for key in main:
            self.assertNotEqual(main[key], other[key], key)
        lines = lane.fingerprint_lines(support.lane_config("802"))
        self.assertIn("lane_account 802", lines)
        self.assertIn("lane_bridge_port 18090", lines)


class _FakeRemote:
    def __init__(self, old: bool = False) -> None:
        # old=True 模拟升级客户端前 open 的会话：服务器上是不认 -Tag 的旧脚本。
        self.old = old
        self.scripts: list[tuple[str, dict]] = []
        self.uploads: list[str] = []
        self.downloads: list[str] = []

    def run_script(self, name: str, named: dict | None, timeout: int) -> RemoteResult:
        del timeout
        args = dict(named or {})
        self.scripts.append((name, args))
        if self.old and name == "read-audit.ps1" and "Tag" in args:
            return RemoteResult(1, "ParameterBindingException ... NamedParameterNotFound,read-audit.ps1")
        if not self.old and name == "session-status.ps1" and "Tag" in args:
            return RemoteResult(0, name + "\ntag " + args["Tag"])
        return RemoteResult(0, name)

    def upload(self, local: Path, remote_win: str) -> None:
        self.assertExists(local)
        self.uploads.append(remote_win)

    def download(self, remote_win: str, local: Path) -> None:
        self.downloads.append(remote_win)
        if remote_win.endswith(".log"):
            local.write_text("", encoding="utf-8")
            return
        local.write_text(json.dumps({"config": support.status_identity()}), encoding="utf-8")

    def ensure_dirs(self) -> RemoteResult:
        raise AssertionError("call 和 status 不重锁目录")

    @staticmethod
    def assertExists(path: Path) -> None:
        if not path.is_file():
            raise AssertionError("本机文件不在")


class ConcurrentFilesTests(unittest.TestCase):
    def tearDown(self) -> None:
        support.install()

    def test_run_tags_are_unique_and_valid(self) -> None:
        tags = {run_tag() for _ in range(50)}
        self.assertEqual(len(tags), 50)
        for tag in tags:
            self.assertTrue(tag.startswith("p" + str(os.getpid()) + "-"))
            self.assertTrue(tagged(tag, "x.json").endswith("-x.json"))
        with self.assertRaises(ValueError):
            tagged("..\\evil", "x.json")

    def test_concurrent_sql_calls_use_distinct_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            support.install(
                support.make_config(report__dir=tmp, account__operator_file="/nonexistent/op.json", bridge__secret_file="/nonexistent/s.hex")
            )
            (report / "session.json").write_text(json.dumps({"report_dir": tmp, "owner": "session"}), encoding="utf-8")
            remote = _FakeRemote()
            with mock.patch("builtins.print"):
                self.assertEqual(sql_session("UFDATA_801_2024", "SELECT 1", remote), 0)
                self.assertEqual(sql_session("UFDATA_801_2024", "SELECT 2", remote), 0)
            locals_ = sorted(path.name for path in report.glob("*session-query.sql"))
            outs = sorted(path.name for path in report.glob("*session-query.out.txt"))
        self.assertEqual(len(remote.uploads), 2)
        self.assertNotEqual(remote.uploads[0], remote.uploads[1])
        files = [named["File"] for name, named in remote.scripts if name == "session-sql.ps1"]
        self.assertEqual(files, remote.uploads)
        dropped = [named["Path"] for name, named in remote.scripts if name == "remove-file.ps1"]
        self.assertEqual(dropped, remote.uploads)
        self.assertEqual(len(locals_), 2)
        self.assertEqual(len(outs), 2)

    def test_concurrent_status_reads_use_distinct_files(self) -> None:
        remote = _FakeRemote()
        with tempfile.TemporaryDirectory() as tmp:
            fetch_status(remote, Path(tmp), ensure=False)
            fetch_status(remote, Path(tmp), ensure=False)
            self.assertEqual(list(Path(tmp).iterdir()), [])
        tags = [named["Tag"] for name, named in remote.scripts if name == "session-status.ps1"]
        self.assertEqual(len(set(tags)), 2)
        self.assertEqual(len(set(remote.downloads)), 2)
        for tag, path in zip(tags, remote.downloads):
            self.assertTrue(path.endswith("\\out\\" + tag + "-session-status.json"))

    def test_old_status_script_falls_back_to_untagged_file(self) -> None:
        remote = _FakeRemote(old=True)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("sys.stderr"):
            data = fetch_status(remote, Path(tmp), ensure=False)
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual(data["config"], support.status_identity())
        self.assertEqual(len(remote.downloads), 1)
        self.assertTrue(remote.downloads[0].endswith("\\out\\session-status.json"))
        # 旧文件不删：可能还有别的旧客户端在用。
        self.assertNotIn("remove-file.ps1", [name for name, _ in remote.scripts])

    def test_old_audit_script_falls_back_without_tag(self) -> None:
        remote = _FakeRemote(old=True)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("sys.stderr"):
            _read_audit_day(remote, Path(tmp), "20260101", [])
        calls = [named for name, named in remote.scripts if name == "read-audit.ps1"]
        self.assertEqual(calls, [{"Day": "20260101", "Tag": calls[0]["Tag"]}, {"Day": "20260101"}])
        self.assertTrue(remote.downloads[0].endswith("\\out\\audit-20260101.log"))

    def test_new_audit_script_uses_tagged_file(self) -> None:
        remote = _FakeRemote()
        with tempfile.TemporaryDirectory() as tmp:
            _read_audit_day(remote, Path(tmp), "20260101", [])
        tag = remote.scripts[0][1]["Tag"]
        self.assertTrue(remote.downloads[0].endswith("\\out\\" + tag + "-audit-20260101.log"))
        self.assertEqual(remote.scripts[-1][0], "remove-file.ps1")

        # 控制套接字路径有 104 字节上限：测试放在短路径下，避免运行环境的 TMPDIR 太长。
    def test_mux_socket_is_per_process(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            path = prepare_control_path(Path(tmp))
        self.assertTrue(path.endswith("cm-" + str(os.getpid()) + "-%C"))

    def test_server_scripts_use_per_call_names(self) -> None:
        worker = (_REMOTE / "sql-worker.ps1").read_text(encoding="utf-8")
        self.assertIn("$PID", worker)
        self.assertIn("Remove-QueryFile", worker)
        self.assertNotIn('"out\\worker-query.sql"', worker)
        rule = "'^p\\d{1,10}-[0-9a-f]{8}$'"
        for name in ("session-status.ps1", "read-audit.ps1"):
            text = (_REMOTE / name).read_text(encoding="utf-8")
            self.assertIn("[string]$Tag", text, name)
            self.assertIn(rule, text, name)


class LaneLockTests(unittest.TestCase):
    def tearDown(self) -> None:
        support.install()

    def test_close_excludes_parallel_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            support.install(support.make_config(report__dir=tmp))
            with session_lock(False), session_lock(False):
                # 多条 call / sql 可以并行，但这时 close 拒绝。
                with self.assertRaises(RuntimeError), session_lock(True):
                    pass
            with session_lock(True):
                with self.assertRaises(RuntimeError), session_lock(False):
                    pass
            with session_lock(True):
                pass

    def test_command_modes(self) -> None:
        self.assertEqual(EXCLUSIVE, {"open", "deploy", "close", "reset", "extend"})
        self.assertEqual(SHARED, {"call", "sql", "audit"})
        self.assertNotIn("status", EXCLUSIVE | SHARED)
        main = (_HERE / "session.py").read_text(encoding="utf-8")
        self.assertIn("with command_lock(args.cmd)", main)


if __name__ == "__main__":
    unittest.main()
