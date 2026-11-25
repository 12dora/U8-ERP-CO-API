"""远端命令行长度和 stdin 传正文。Win32-OpenSSH 缺省经 cmd.exe 执行，命令行超过 8191 字符会失败。不连接服务器。"""

from __future__ import annotations

import base64
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from co.e2e import remote, sqlssh
from co.e2e.tests import support
from co.probe import comprobe_remote

_LIMIT = 8000


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _decoded(payload: bytes) -> str:
    return base64.b64decode(payload.strip()).decode("utf-8")


def _fake_remote() -> SimpleNamespace:
    return SimpleNamespace(key="/nonexistent/key", known_hosts="/nonexistent/known_hosts", mux_args=lambda: [])


class StdinLaunchTests(unittest.TestCase):
    def test_long_bodies_go_over_stdin_with_a_fixed_command(self) -> None:
        bodies = (remote.ensure_body(), remote.idle_body(), comprobe_remote.ensure_body())
        commands = set()
        for body in bodies:
            command, payload = remote.stdin_launch(body)
            self.assertLess(len(command), _LIMIT)
            self.assertTrue(_decoded(payload).startswith(body))
            commands.add(command)
        self.assertEqual(len(commands), 1)

    def test_payload_keeps_failure_exit_code_and_is_ascii(self) -> None:
        _, payload = remote.stdin_launch("& 'x.ps1'")
        payload.decode("ascii")
        self.assertTrue(_decoded(payload).endswith("\nif (-not $?) { exit 1 }\n# u8co-body-end"))
        stub = base64.b64decode(remote.stdin_launch("")[0].split()[-1]).decode("utf-16-le")
        self.assertIn("[Console]::OpenStandardInput()", stub)
        self.assertIn(". ([scriptblock]::Create($u8coBody))", stub)

    def test_stub_refuses_body_without_end_marker(self) -> None:
        # 空的或截断的 stdin 不能当成功执行。
        stub = base64.b64decode(remote.stdin_launch("")[0].split()[-1]).decode("utf-16-le")
        self.assertIn("EndsWith([string][char]10 + '# u8co-body-end', [System.StringComparison]::Ordinal)", stub)
        self.assertLess(stub.index("EndsWith("), stub.index(". ([scriptblock]::Create("))

    def test_inline_launch_refuses_long_bodies(self) -> None:
        with self.assertRaises(ValueError):
            remote.remote_launch(remote.ensure_body())

    def test_every_inline_command_stays_short(self) -> None:
        bodies = [remote.fixed_call(name) for name in ("build", "selftest", "check")]
        bodies.append(remote.script_call("fingerprint.ps1", {"Database": "UFDATA_801_2024", "OutFile": "C:\\x\\out\\fp.json"}))
        bodies.append(comprobe_remote.launch_body())
        bodies.append(comprobe_remote.build_body())
        for body in bodies:
            self.assertLess(len(remote.remote_launch(body)), _LIMIT)

    def test_run_body_sends_body_on_stdin(self) -> None:
        box = remote.Remote.__new__(remote.Remote)
        box.key, box.known_hosts = "/nonexistent/key", "/nonexistent/known_hosts"
        box.mux_args = lambda: []
        with mock.patch.object(remote, "_run", return_value=remote.RemoteResult(0, "")) as run:
            box.ensure_dirs()
        argv, timeout, payload = run.call_args.args
        self.assertEqual(timeout, 60)
        self.assertLess(len(argv[-1]), _LIMIT)
        self.assertTrue(_decoded(payload).startswith(remote.ensure_body()))

    def test_sql_worker_command_stays_short(self) -> None:
        with mock.patch.object(sqlssh.subprocess, "Popen") as popen:
            sqlssh._spawn(_fake_remote())
        self.assertLess(len(popen.call_args.args[0][-1]), _LIMIT)

    def test_comprobe_ssh_uses_stdin_only_without_its_own_payload(self) -> None:
        done = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        with (
            mock.patch("co.e2e.flow.make_remote", return_value=_fake_remote()),
            mock.patch.object(comprobe_remote.subprocess, "run", return_value=done) as run,
        ):
            comprobe_remote.remote_ssh(comprobe_remote.ensure_body(), None, 60)
            argv = run.call_args.args[0]
            self.assertLess(len(argv[-1]), _LIMIT)
            self.assertTrue(_decoded(run.call_args.kwargs["input"]).startswith(comprobe_remote.ensure_body()))
            comprobe_remote.remote_ssh(comprobe_remote.launch_body(), b"secret-payload", 60)
            argv = run.call_args.args[0]
            self.assertEqual(argv[-1], remote.remote_launch(comprobe_remote.launch_body()))
            self.assertEqual(run.call_args.kwargs["input"], b"secret-payload")


class EnsureLockTests(unittest.TestCase):
    def test_contents_checked_before_and_after_lock(self) -> None:
        body = remote.ensure_body()
        lock = body.index("Lock-Dir $root ")
        self.assertLess(body.index("Assert-Contents $root"), lock)
        self.assertIn("Assert-Contents $root", body[lock:])
        self.assertNotIn("if ($existed) { Assert-Contents", body)

    def test_extra_grants_removed_and_verified(self) -> None:
        body = remote.ensure_body()
        self.assertIn("/remove:g", body)
        self.assertIn("Get-ExtraSids $Dir $Allowed", body)
        self.assertIn("Lock-Dir $root @('*' + $principal + ':(OI)(CI)M') ($admins + @($principal))", body)
        self.assertIn("Lock-Dir $dir @() $admins", body)

    def test_owner_checked_before_lock_and_ssh_account_trusted(self) -> None:
        body = remote.ensure_body()
        lock = body.index("function Lock-Dir")
        self.assertLess(body.index("可能被别人抢先建出", lock), body.index("/inheritance:r", lock))
        self.assertIn("$owners = $admins + @('S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464', $me)", body)

    def test_server_scripts_check_root_grantees_after_config(self) -> None:
        common = (Path(__file__).resolve().parents[1] / "remote" / "common.ps1").read_text(encoding="utf-8-sig")
        head = common[: common.index("$script:Cfg = Read-E2EConfig")]
        self.assertIn("function Assert-RootGrantees", head)
        self.assertLess(head.index("Read-CfgPaths $doc"), head.index("Assert-RootGrantees $extra $doc"))


class RedeployTests(unittest.TestCase):
    def test_redeploy_checks_service_root_before_stopping(self) -> None:
        # 旧版本登记的服务 binPath 没有 --root，换上新版桥会找不到配置；停服务之前就拒绝。
        text = (Path(__file__).resolve().parents[1] / "remote" / "service-redeploy.ps1").read_text(encoding="utf-8-sig")
        body = text[text.index("function Update-InstalledBridge") :]
        self.assertLess(body.index("Assert-ServiceRoot"), body.index("try {"))
        # 检查本身在 common.ps1，预检、停、启都用它：程序路径和 --root 都要对上本通道的运行目录。
        common = (Path(__file__).resolve().parents[1] / "remote" / "common.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("'--root \"' + $root + '\"'", common)
        self.assertIn("install.ps1 -Root", common)
        self.assertIn("Get-BinPathExe $path", common)
        for name in ("service-stop.ps1", "service-start.ps1"):
            script = (Path(__file__).resolve().parents[1] / "remote" / name).read_text(encoding="utf-8-sig")
            self.assertIn("Assert-ServiceRoot", script, name)
        preflight = (Path(__file__).resolve().parents[1] / "remote" / "preflight.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Get-ServiceRootProblem", preflight)


if __name__ == "__main__":
    unittest.main()
