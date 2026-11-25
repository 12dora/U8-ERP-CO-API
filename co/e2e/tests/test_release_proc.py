"""还原时结束占住单用户槽位的会话：master.dbo.u8co_release_db 安装脚本和 snapshot-revert.ps1 的调用。不连接服务器。

脚本文本检查总是跑；本机有 pwsh（PATH 或 scripts/quality/pwsh.sh 的缓存）时，另把 snapshot-revert.ps1 里的日志函数取出来实跑。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_PROC = _ROOT / "scripts" / "lane" / "install-release-proc.sql"
_REVERT = _ROOT / "co" / "e2e" / "remote" / "snapshot-revert.ps1"
_COMMON = _ROOT / "co" / "e2e" / "remote" / "common.ps1"
_TASKLIB = _ROOT / "co" / "e2e" / "remote" / "tasklib.ps1"


def _pwsh() -> str | None:
    found = shutil.which("pwsh")
    if found:
        return found
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "u8co"
    for path in sorted(base.glob("pwsh-*/pwsh"), reverse=True):
        if os.access(path, os.X_OK):
            return str(path)
    return None


class ReleaseProcTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _PROC.read_text(encoding="utf-8")

    def test_victims_include_cross_database_lock_holders(self) -> None:
        # U8 任务服务从 UFSystem 跨库轮询测试账套库：当前库不是它，但持有库级锁，同样占着单用户槽位。
        self.assertIn("s.database_id = @id", self.sql)
        self.assertIn("sys.dm_tran_locks", self.sql)
        self.assertIn("l.resource_type = N'DATABASE'", self.sql)
        self.assertIn("l.resource_database_id = @id", self.sql)
        self.assertIn("s.session_id <> @me AND s.is_user_process = 1", self.sql)

    def test_who_output_is_optional_and_reports_host_program_login(self) -> None:
        self.assertIn("@who nvarchar(1000) = NULL OUTPUT", self.sql)
        for word in ("s.host_name", "s.program_name", "s.login_name"):
            self.assertIn(word, self.sql)
        # 拒绝条件和签名做法不变。
        self.assertIn("owner_sid = SUSER_SID(ORIGINAL_LOGIN())", self.sql)
        self.assertIn("REMOVE PRIVATE KEY", self.sql)
        self.assertIn("VIEW SERVER STATE", self.sql)

    def test_restore_mode_runs_kill_alter_restore_tightly(self) -> None:
        # 连着 U8 应用服务器的测试账套上，应用服务器的池连接约 1 秒内重连：结束会话之后马上执行同一个动态语句（SINGLE_USER + RESTORE）。
        for text in (
            "@restore_snapshot sysname = NULL",
            "@user_access nvarchar(20) = NULL",
            "@tries int = NULL OUTPUT",
            "SET DEADLOCK_PRIORITY HIGH;",
            "SET @step = @again + N' RESTORE DATABASE ' + QUOTENAME(@db) + N' FROM DATABASE_SNAPSHOT = '",
            "QUOTENAME(@restore_snapshot, N'''')",
            "IF @tries >= @limit OR SYSDATETIME() >= @deadline",
            "@limit int = 60",
            "SET @limit = @tries + 5;",
            "最后错误 %d",
            "RETURN -2;",
        ):
            self.assertIn(text, self.sql)
        loop = self.sql.index("WHILE 1 = 1")
        kill = self.sql.index("EXEC (@kill);", loop)
        step = self.sql.index("EXEC (@step);", loop)
        self.assertLess(kill, step)
        # 结束会话和动态语句之间没有 WAITFOR；停顿只在失败之后。
        self.assertNotIn("WAITFOR", self.sql[kill:step])
        self.assertLess(step, self.sql.index("WAITFOR DELAY", loop))
        # RESTORE 之后在同一个动态语句里重新占回 SINGLE_USER；失败时整句（连 RESTORE）重做，不再单独只占槽位。
        self.assertIn("+ QUOTENAME(@restore_snapshot, N'''') + N'; ' + @again;", self.sql)
        self.assertNotIn("SET @phase = 1;", self.sql)

    def test_restore_mode_keeps_guards(self) -> None:
        # 快照必须是该库自己的；ALTER / RESTORE 不靠证书，签名登录的权限不变。
        self.assertIn("WHERE name = @restore_snapshot AND source_database_id = @id", self.sql)
        self.assertIn("@user_access NOT IN (N'SINGLE_USER', N'MULTI_USER')", self.sql)
        self.assertIn("GRANT ALTER ANY CONNECTION TO [u8co_release_signer];", self.sql)
        self.assertIn("GRANT VIEW SERVER STATE TO [u8co_release_signer];", self.sql)
        self.assertNotIn("GRANT ALTER ANY DATABASE", self.sql)
        guard = self.sql.index("owner_sid = SUSER_SID(ORIGINAL_LOGIN())")
        self.assertLess(guard, self.sql.index("WHILE 1 = 1"))


class RevertCallTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ps = _REVERT.read_text(encoding="utf-8")

    def test_call_passes_who_only_when_proc_has_it(self) -> None:
        self.assertIn('return "DECLARE @rel int, @who nvarchar(1000);"', self.ps)
        self.assertIn("FROM master.sys.parameters WHERE object_id = OBJECT_ID(N'master.dbo.u8co_release_db') AND name = N'@who'", self.ps)
        self.assertIn('", @who = @who OUTPUT;"', self.ps)
        self.assertIn('" ELSE EXEC @rel = master.dbo.u8co_release_db "', self.ps)

    def test_busy_retry_logs_holders_and_reports_them(self) -> None:
        catch = self.ps.index("Write-ReleaseLog $_.Exception.Message")
        self.assertLess(catch, self.ps.index("if (-not (Test-BusyError $_.Exception.Message)) { throw }"))
        self.assertIn("最近结束过的占用会话", self.ps)
        # 还原批照旧：每个 SINGLE_USER 都 ROLLBACK IMMEDIATE，释放语句在它前面。
        self.assertGreaterEqual(self.ps.count("SET SINGLE_USER WITH ROLLBACK IMMEDIATE"), 2)
        self.assertNotIn("-Recurse", self.ps)

    def test_new_proc_is_detected_and_old_batch_kept(self) -> None:
        # 服务器上还是旧版过程（没有 @restore_snapshot）或没装时，照旧走 Get-RevertBatch。
        self.assertIn("AND name = N'@restore_snapshot')", self.ps)
        self.assertIn("$useProc = Test-ProcRevert $Database", self.ps)
        self.assertIn("$batch = Get-ProcRevertBatch $Database $Snapshot $WithTask", self.ps)
        self.assertIn("$batch = Get-RevertBatch $Database $Snapshot $WithTask", self.ps)
        self.assertEqual(self.ps.count("Set-MultiUser $Database $useProc"), 2)
        self.assertIn(", @user_access = N'MULTI_USER'", self.ps)
        self.assertIn("1222", self.ps)

    def test_old_batch_reenters_single_user_right_after_restore(self) -> None:
        # 旧批：RESTORE 之后同一个 TRY 里紧接着 SINGLE_USER；USE 进不去就报 924 整批重来，不结束占用者后再进。
        body = self.ps[self.ps.index("function Get-RevertBatch") : self.ps.index("function Test-ProcRevert")]
        restore = body.index("RESTORE DATABASE")
        again = body.index("SET SINGLE_USER WITH ROLLBACK IMMEDIATE", restore)
        self.assertLess(again, body.index("END TRY"))
        use = body.index('"USE " + $db')
        self.assertLess(body.index("END CATCH;"), use)
        self.assertIn("最后错误 924", body[use:])
        self.assertNotIn("$release", body[body.index("END CATCH;") : use])


_DRIVER = r"""
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$null, [ref]$null)
$want = @("Write-ReleaseLog", "Get-HeldText", "Get-ReleaseHead")
foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
    if ($want -contains $f.Name) { Invoke-Expression $f.Extent.Text }
}
foreach ($s in $ast.EndBlock.Statements) {
    $text = $s.Extent.Text
    if ($text.StartsWith('$script:RelPattern') -or $text.StartsWith('$script:Holders') -or $text.StartsWith('$script:BusySeconds')) {
        Invoke-Expression $text
    }
}
$us = [char]31
$rs = [char]30
Write-ReleaseLog (("U8REVERT_REL" + $us + "UFDATA_999_2026" + $us + "0" + $us + $rs).Trim())
Write-ReleaseLog ("sqlcmd 1 U8REVERT_REL" + $us + "UFDATA_999_2026" + $us + "1" + $us + "会话 57 主机 APP2 程序 TaskSvc 登录 sa" + $rs + " Msg 5064")
Write-ReleaseLog ("U8REVERT_REL" + $us + "UFMeta_999" + $us + "3")
Get-HeldText "UFDATA_999_2026" "RESTORE" 2
Write-ReleaseLog ("U8REVERT_REL" + $us + "UFDATA_999_2026" + $us + "2" + $us + "会话 61 主机 APP1 程序 Pool 登录 sa" + $us + "3" + $rs)
Write-ReleaseLog ("sqlcmd 1 U8REVERT_REL" + $us + "UFMeta_999" + $us + "-2" + $us + "会话 70 主机 APP2 程序 Pool 登录 sa" + $us + "60" + $rs)
Write-ReleaseLog ("U8REVERT_REL" + $us + "UFMeta_999" + $us + "0" + $us + $us + "1")
"""

_BATCH_DRIVER = r"""
$ErrorActionPreference = "Stop"
$want = @("Get-ProcRevertBatch", "Get-ProcDeclare", "Get-ProcCall", "Test-BusyError", "Quote-Ident", "Get-TaskBatchSql", "Get-TaskCols")
foreach ($path in $args) {
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$null)
    foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
        if ($want -contains $f.Name) { Invoke-Expression $f.Extent.Text }
    }
    foreach ($s in $ast.EndBlock.Statements) {
        if ($s.Extent.Text.StartsWith('$script:BusyCodes')) { Invoke-Expression $s.Extent.Text }
    }
}
$script:FingerSql = "-- FINGER"
Get-ProcRevertBatch "UFDATA_999_2026" "UFDATA_999_2026_u8co_snap" $true
Write-Host "BUSY:"
Test-BusyError "Msg 50000, Level 16 u8co_release_db UFDATA_999_2026：重试 60 次仍被占用，最后错误 5064：..."
Test-BusyError "消息 924，级别 14"
Test-BusyError "Msg 50000, Level 16 u8co_release_db 拒绝 UFDATA_999_2026"
Test-BusyError "未能进入 UFDATA_999_2026（最后错误 924）"
Test-BusyError "Msg 50000 u8co_release_db UFMeta_999：重试 60 次仍被占用，最后错误 3013：RESTORE DATABASE is terminating abnormally."
Test-BusyError "Msg 3013, Level 16 RESTORE DATABASE is terminating abnormally."
"""


@unittest.skipIf(_pwsh() is None, "本机没有 pwsh")
class RevertLogRunTest(unittest.TestCase):
    def test_log_lines_and_held_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            driver = Path(tmp) / "driver.ps1"
            driver.write_text(_DRIVER, encoding="utf-8")
            done = subprocess.run(
                [str(_pwsh()), "-NoProfile", "-NonInteractive", "-File", str(driver), str(_REVERT)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
                check=False,
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], "u8co_release_db UFDATA_999_2026 结束会话 0 个")
        self.assertEqual(lines[1], "u8co_release_db UFDATA_999_2026 结束会话 1 个：会话 57 主机 APP2 程序 TaskSvc 登录 sa")
        # 旧版过程（没有 @who）的行。
        self.assertEqual(lines[2], "u8co_release_db UFMeta_999 结束会话 3 个")
        self.assertIn("最近结束过的占用会话：会话 57 主机 APP2 程序 TaskSvc 登录 sa。", lines[3])
        # 新版过程的行：第五列是尝试次数；-2 是用尽重试。
        self.assertEqual(lines[4], "u8co_release_db UFDATA_999_2026 结束会话 2 个，尝试 3 次：会话 61 主机 APP1 程序 Pool 登录 sa")
        self.assertEqual(lines[5], "u8co_release_db UFMeta_999 重试 60 次仍未成功：会话 70 主机 APP2 程序 Pool 登录 sa")
        self.assertEqual(lines[6], "u8co_release_db UFMeta_999 结束会话 0 个，尝试 1 次")

    def test_proc_batch_order_and_busy_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            driver = Path(tmp) / "batch.ps1"
            driver.write_text(_BATCH_DRIVER, encoding="utf-8")
            done = subprocess.run(
                [str(_pwsh()), "-NoProfile", "-NonInteractive", "-File", str(driver), str(_REVERT), str(_COMMON), str(_TASKLIB)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
                check=False,
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        out = done.stdout
        head, _, busy = out.partition("BUSY:")
        # 过程用尽重试一律当占用（CATCH 只拿得到最后一个错误号，常是 3013）；单独的 3013 不算。
        self.assertEqual(busy.split(), ["True", "True", "False", "True", "True", "False"])
        batch = head
        restore = batch.index("@restore_snapshot = N'UFDATA_999_2026_u8co_snap'")
        use = batch.index("    USE [UFDATA_999_2026];")
        # 进库被抢先时让过程连 RESTORE 一起重做（别人进过库，可能已经写入），不只是重新占槽位。
        again = batch.index("@restore_snapshot = N'UFDATA_999_2026_u8co_snap'", use)
        self.assertNotIn("@user_access = N'SINGLE_USER'", batch)
        task = batch.index("U8REVERT_TASK")
        finger = batch.index("-- FINGER")
        multi = batch.index("ALTER DATABASE [UFDATA_999_2026] SET MULTI_USER;")
        self.assertLess(restore, use)
        self.assertLess(use, again)
        self.assertLess(again, task)
        self.assertLess(task, finger)
        self.assertLess(finger, multi)
        self.assertLess(multi, batch.index("USE [master];"))
        self.assertIn("IF @useTries >= 20 THROW;", batch)
        self.assertIn("IF DB_NAME() <> N'UFDATA_999_2026'", batch)
        self.assertEqual(batch.count("IF ISNULL(@rel, -1) < 0 RETURN;"), 2)
        # 客户端不再自己 SINGLE_USER / RESTORE：都在过程里。
        self.assertNotIn("SET SINGLE_USER", batch)
        self.assertNotIn("RESTORE DATABASE", batch)


if __name__ == "__main__":
    unittest.main()
