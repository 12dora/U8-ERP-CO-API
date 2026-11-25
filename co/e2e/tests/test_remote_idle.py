"""上传前的空闲检查（assert-idle）：本通道上次中断留下的 S4U 探测任务自动清掉，其余计划任务仍拒绝。不连接服务器。

脚本文本检查总是跑；本机有 pwsh（PATH 或 scripts/quality/pwsh.sh 的缓存）时，另用假的计划任务命令实跑一遍。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from co.e2e import remote
from co.e2e.tests import support

_PROBE = "u8co-e2e-s4u-probe"
_WATCHDOG = "u8co-e2e-revert"

# 假的计划任务命令：函数优先于同名 cmdlet。$global:fake 是 {任务名: @(状态, 路径)}。
_FAKES = r"""
function Get-ScheduledTask { param([string]$TaskName, $ErrorAction)
  foreach ($key in @($global:fake.Keys)) {
    if ($key -ceq $TaskName) { foreach ($item in $global:fake[$key]) { [pscustomobject]@{ TaskName = $key; State = $item[0]; TaskPath = $item[1] } } }
  }
}
function Get-ScheduledTaskInfo { param([string]$TaskName, [string]$TaskPath, $ErrorAction)
  [pscustomobject]@{ LastRunTime = [datetime]'2026-10-02 16:30:00' }
}
function Unregister-ScheduledTask { param([string]$TaskName, [string]$TaskPath, [switch]$Confirm)
  if ($TaskPath -ne '\') { throw 'bad path' }
  $global:fake.Remove($TaskName)
  Write-Output ('UNREGISTERED ' + $TaskName)
}
"""


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _pwsh() -> str | None:
    found = shutil.which("pwsh")
    if found:
        return found
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "u8co"
    for path in sorted(base.glob("pwsh-*/pwsh"), reverse=True):
        if os.access(path, os.X_OK):
            return str(path)
    return None


class IdleTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.body = remote.idle_body()

    def test_only_own_probe_task_not_running_is_removed(self) -> None:
        self.assertIn("$probe = '" + _PROBE + "'", self.body)
        self.assertIn("$own = $task -ceq $probe -and $found.Count -eq 1 -and [string]$found[0].TaskPath -eq '\\'", self.body)
        self.assertIn("if (-not $own -or @('Running', 'Queued') -contains [string]$found[0].State) { throw", self.body)
        self.assertIn("还在（看门狗或探测任务），不覆盖脚本和配置", self.body)
        self.assertIn("Unregister-ScheduledTask -TaskName $task -TaskPath '\\' -Confirm:$false", self.body)
        self.assertIn("清掉上次中断留下的探测任务 ' + $task + '（状态 ' + $state + '，上次运行 ' + $last + '）'", self.body)
        # 先判断再删，删后再查一遍；不按通配或计算出来的名字删。
        self.assertLess(self.body.index("$own ="), self.body.index("Unregister-ScheduledTask"))
        self.assertIn("清不掉，请人工处理", self.body)
        self.assertNotIn("*", self.body[self.body.index("$probe ="): self.body.index("if ($snaps.Count")])


@unittest.skipUnless(_pwsh(), "本机没有 pwsh")
class IdleRunTest(unittest.TestCase):
    def _run(self, fake: str) -> subprocess.CompletedProcess:
        script = "$ErrorActionPreference = 'Stop'\n" + _FAKES + "$global:fake = @{" + fake + "}\n" + remote.idle_body()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "idle.ps1"
            path.write_text(script, encoding="utf-8")
            return subprocess.run(
                [str(_pwsh()), "-NoProfile", "-NonInteractive", "-File", str(path)],
                capture_output=True, text=True, timeout=120, check=False,
            )

    def test_idle_server_passes_silently(self) -> None:
        got = self._run("")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(got.stdout.strip(), "")

    def test_stale_own_probe_is_removed_and_logged(self) -> None:
        got = self._run("'" + _PROBE + "' = @(,@('Ready', '\\'))")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("UNREGISTERED " + _PROBE, got.stdout)
        self.assertIn("清掉上次中断留下的探测任务 " + _PROBE + "（状态 Ready，上次运行 2026-10-02 16:30:00）", got.stdout)

    def test_running_probe_watchdog_or_odd_path_still_refused(self) -> None:
        cases = {
            "running": "'" + _PROBE + "' = @(,@('Running', '\\'))",
            "queued": "'" + _PROBE + "' = @(,@('Queued', '\\'))",
            "watchdog": "'" + _WATCHDOG + "' = @(,@('Ready', '\\'))",
            "subfolder": "'" + _PROBE + "' = @(,@('Ready', '\\Other\\'))",
            "two": "'" + _PROBE + "' = @(@('Ready', '\\'), @('Ready', '\\Other\\'))",
        }
        for name, fake in cases.items():
            with self.subTest(name=name):
                got = self._run(fake)
                self.assertNotEqual(got.returncode, 0)
                self.assertIn("还在（看门狗或探测任务），不覆盖脚本和配置", got.stdout + got.stderr)
                self.assertNotIn("UNREGISTERED", got.stdout)


if __name__ == "__main__":
    unittest.main()
