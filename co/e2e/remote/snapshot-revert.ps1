#Requires -Version 7.4
#Requires -PSEdition Core
# 看门狗可以 -AllowMissing。编排器不行：快照没了或 done 已在，必须失败。
# -KeepSnapshot 给 session reset：还原后不删快照，会话继续。它不接受缺失的快照，和 -AllowMissing 同给时按没给 -AllowMissing 处理。
param(
    [switch]$AllowMissing,
    [switch]$KeepSnapshot
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
. (Join-Path $PSScriptRoot "fingerlib.ps1")
. (Join-Path $PSScriptRoot "tasklib.ps1")

# SINGLE_USER 之后，别的主机（U8 应用服务器的连接池）可能抢先连上占住唯一的连接，RESTORE 和 SET MULTI_USER 都会失败。
# e2e 登录不是 sysadmin，不能 KILL。服务器装了 master.dbo.u8co_release_db（scripts/lane/install-release-proc.sql）时，
# 在 SINGLE_USER / RESTORE 之前、以及库还不是 MULTI_USER 时的 SET MULTI_USER 之前，同一批同一连接里调用它
# 结束占用该库的会话（含跨库持有库级锁的，如 U8 任务服务从 UFSystem 每分钟重连轮询），并在日志里记下是哪台主机、哪个程序；
# 没装时只能等对方断开
# （空闲的池连接会超时）后重试。1205：ALTER DATABASE 被选为死锁牺牲者，同样重试。
# 新版过程（有 @restore_snapshot 参数）另把「结束会话 → SINGLE_USER + RESTORE」「结束会话 → 重新 SINGLE_USER / MULTI_USER」
# 放在服务器端紧凑重试，两步之间没有客户端往返（U8 应用服务器的池连接可能在 1 秒内重连，客户端重试抢不过）；
# 过程用尽重试时报「仍被占用，最后错误 <错误号>」，一律当占用、整批重来（一条语句报多个错时 CATCH 只拿得到最后一个，
# 常是 3013 / 5069 这类笼统的号），最终报错里附上最后一次的原文。旧版过程或没装时走原来的批。
$script:BusyCodes = @(924, 1205, 1222, 3101, 3102, 5061, 5064, 5070)
$script:BusySeconds = 180

function Test-BusyError([string]$Text) {
    # sqlcmd 按系统语言打印 Msg 或 消息；u8co_release_db 用尽重试时的报错是 Msg 50000，错误号在「最后错误」后面。
    if ($Text -match 'u8co_release_db \S+：重试 \d+ 次仍被占用') { return $true }
    foreach ($m in [regex]::Matches($Text, '(?:Msg|消息|最后错误)\s*(\d+)')) {
        if ($script:BusyCodes -contains [int]$m.Groups[1].Value) { return $true }
    }
    return $false
}

function Get-HeldText([string]$Database, [string]$What, [int]$Tries) {
    $head = "数据库 " + $Database + " 被另一个连接以单用户模式占住（" + $What + " 重试 " + $Tries + " 次、约 " + $script:BusySeconds + " 秒仍未释放）。"
    $tail = "快照完好，没有删除。请 DBA 执行 KILL <占用该库的会话>，再 ALTER DATABASE [" + $Database + "] SET MULTI_USER，然后重新还原。"
    $seen = ""
    if ($script:Holders.ContainsKey($Database)) { $seen = "最近结束过的占用会话：" + $script:Holders[$Database] + "。" }
    return ($head + $seen + $tail + "以后请 sysadmin 跑一次 scripts/lane/install-release-proc.sql，还原就能自己结束占用的会话。")
}

# 释放语句：过程不在或没有 EXECUTE 权限（看不到元数据，OBJECT_ID 为 NULL）、库不归本登录所有、库名不是 UFDATA_ / UFMeta_
# 时都不调用，照旧靠重试。守卫和过程的拒绝条件一致，所以这句本身不会报错，放在 RESTORE 的 CATCH 里也不会盖掉原错误。
# 过程用返回值报告结束的会话数，新版过程另用 @who 带回被结束会话的主机、程序和登录（旧版过程没有这个参数，按 sys.parameters
# 判断后再决定带不带，服务器没重跑安装脚本也照常还原）。这里输出一行 U8REVERT_REL<US>库名<US>个数<US>会话说明<RS>，
# 由 Split-RevertRows / Write-ReleaseLog 摘出记日志，不进指纹；批失败时 sqlcmd 的输出拼在异常里，Invoke-Busy 也从那里摘。用到批里的 @rel、@who，批开头要有 Get-ReleaseDeclare。
# 库名已经过 Quote-Ident 的字符检查。
function Get-ReleaseDeclare {
    return "DECLARE @rel int, @who nvarchar(1000);"
}

# 过程在、本登录看得见且库通过过程的守卫。
function Get-ReleaseGuard([string]$Database) {
    [void](Quote-Ident $Database)
    $name = "N'" + $Database + "'"
    return ("OBJECT_ID(N'master.dbo.u8co_release_db') IS NOT NULL AND EXISTS (SELECT 1 FROM sys.databases WHERE name = " +
        $name + " AND owner_sid = SUSER_SID(ORIGINAL_LOGIN()) AND source_database_id IS NULL" +
        " AND (name LIKE N'UFDATA[_]%' OR name LIKE N'UFMeta[_]%'))")
}

function Get-ReleaseSql([string]$Database) {
    $name = "N'" + $Database + "'"
    return ("IF " + (Get-ReleaseGuard $Database) +
        " BEGIN SET @who = NULL;" +
        " IF EXISTS (SELECT 1 FROM master.sys.parameters WHERE object_id = OBJECT_ID(N'master.dbo.u8co_release_db') AND name = N'@who')" +
        " EXEC @rel = master.dbo.u8co_release_db " + $name + ", @who = @who OUTPUT;" +
        " ELSE EXEC @rel = master.dbo.u8co_release_db " + $name + ";" +
        " SELECT N'U8REVERT_REL' + CHAR(31) + " + $name + " + CHAR(31) + CONVERT(nvarchar(11), @rel) + CHAR(31) + ISNULL(@who, N'') + CHAR(30); END;")
}

$script:RelMark = "U8REVERT_REL$([char]31)"
# 库名 -> 最近一次结束的占用会话说明；最终仍占住时写进报错，告诉 DBA 是谁。
$script:Holders = @{}

# 逐行读到的行已 Trim（.NET 把 US、RS 也当空白），结尾的 RS 可能没了：说明一直取到 RS 或行尾。
# 新版过程的紧凑重试另带第五列：尝试次数。
$script:RelPattern = 'U8REVERT_REL\x1F([^\x1F]*)\x1F(-?\d+)(?:\x1F([^\x1E\x1F]*))?(?:\x1F(\d+))?'

function Get-ReleaseHead($Match) {
    $head = "u8co_release_db " + $Match.Groups[1].Value
    $count = [int]$Match.Groups[2].Value
    $tries = $Match.Groups[4].Value
    if ($count -lt 0) { return ($head + " 重试 " + $tries + " 次仍未成功") }
    $head = $head + " 结束会话 " + $count + " 个"
    if ($tries.Length -gt 0) { $head = $head + "，尝试 " + $tries + " 次" }
    return $head
}

function Write-ReleaseLog([string]$Text) {
    foreach ($m in [regex]::Matches($Text, $script:RelPattern)) {
        $db = $m.Groups[1].Value
        $who = $m.Groups[3].Value.Trim()
        $line = Get-ReleaseHead $m
        if ($who.Length -gt 0) {
            $script:Holders[$db] = $who
            $line = $line + "：" + $who
        }
        Write-Host $line
    }
}

function Invoke-Busy([scriptblock]$Action, [string]$Database, [string]$What) {
    $deadline = (Get-Date).AddSeconds($script:BusySeconds)
    $tries = 0
    while ($true) {
        try {
            return (& $Action)
        } catch {
            # 失败的这一批里结束过谁也要记下（占用者是谁，DBA 要看）。
            Write-ReleaseLog $_.Exception.Message
            if (-not (Test-BusyError $_.Exception.Message)) { throw }
            if ((Get-Date) -ge $deadline) {
                $last = [string]$_.Exception.Message
                if ($last.Length -gt 600) { $last = $last.Substring(0, 600) }
                throw ((Get-HeldText $Database $What $tries) + "最后一次报错：" + $last)
            }
            $tries++
            Write-Host ($What + " 遇到占用，第 " + $tries + " 次重试 " + $Database)
            Start-Sleep -Seconds ([Math]::Min(1 + $tries, 5))
        }
    }
}

# 已经是 MULTI_USER（还原成功之后、看门狗 -AllowMissing 的「已无快照」）就不释放：那时连上来的是 U8 和桥的新池连接，
# 杀掉会让它们下一次用时出传输错误。
function Set-MultiUser([string]$Database, [bool]$UseProc) {
    $db = Quote-Ident $Database
    $notMulti = "IF CONVERT(nvarchar(20), DATABASEPROPERTYEX(N'" + $Database + "', 'UserAccess')) <> N'MULTI_USER'"
    if ($UseProc) {
        # 新版过程：结束占用会话和 SET MULTI_USER 在服务器端紧凑重试；已是 MULTI_USER 就什么都不做。
        $sql = (Get-ProcDeclare) + "`r`n" + $notMulti + "`r`nBEGIN`r`n" + (Get-ProcCall $Database ", @user_access = N'MULTI_USER'") + "`r`nEND;"
    } else {
        $sql = (Get-ReleaseDeclare) + "`r`n" + $notMulti + "`r`nBEGIN`r`n" + (Get-ReleaseSql $Database) + "`r`nEND;`r`nALTER DATABASE " + $db + " SET MULTI_USER;"
    }
    Assert-MasterQuery $sql
    # 不用 GetNewClosure：闭包看不到脚本作用域里的函数。$sql 按动态作用域从本函数取。
    $rows = Invoke-Busy { Get-SqlText "master" $sql } $Database "SET MULTI_USER"
    foreach ($line in @($rows)) {
        $text = ([string]$line).Trim()
        if ($text.StartsWith($script:RelMark)) { Write-ReleaseLog $text }
    }
}

function Assert-FreshWindow([bool]$MissingOk) {
    if ($MissingOk) { return }
    $done = Assert-E2EPath (Get-E2EPath "done")
    if (-not [System.IO.File]::Exists($done)) { return }
    $text = [System.IO.File]::ReadAllText($done).Trim()
    if ($text.StartsWith("done ")) { $text = $text.Substring(5).Trim() }
    throw ("看门狗已在 " + $text + " 还原，之后的写入未还原")
}

function Save-SnapTaskCfg {
    $db = Get-SnapName (Get-DataDb)
    Assert-SnapPair (Get-DataDb) $db
    $rows = ConvertTo-TaskRows @(Get-SqlText $db (Get-TaskQuery))
    $now = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff")
    $doc = [ordered]@{ source = "snapshot"; server_time = $now; restore_started = ""; rows = $rows }
    Write-Utf8 (Get-E2EPath "out\taskcfg-snapshot.json") ($doc | ConvertTo-Json -Depth 5)
}

# RESTORE 会回到快照当时的 MULTI_USER。必须马上再 SINGLE_USER，先导出任务表再算指纹，最后才放开。
function Get-RevertBatch([string]$Database, [string]$Snapshot, [bool]$WithTask) {
    $db = Quote-Ident $Database
    $release = Get-ReleaseSql $Database
    $lines = New-Object System.Collections.Generic.List[string]
    [void]$lines.Add("SET NOCOUNT ON;")
    [void]$lines.Add((Get-ReleaseDeclare))
    [void]$lines.Add($release)
    [void]$lines.Add("ALTER DATABASE " + $db + " SET SINGLE_USER WITH ROLLBACK IMMEDIATE;")
    [void]$lines.Add("BEGIN TRY")
    [void]$lines.Add("  " + $release)
    [void]$lines.Add("  RESTORE DATABASE " + $db + " FROM DATABASE_SNAPSHOT = N'" + $Snapshot + "';")
    # RESTORE 回到多用户，紧接着重新 SINGLE_USER，中间不结束会话、不往返。
    [void]$lines.Add("  ALTER DATABASE " + $db + " SET SINGLE_USER WITH ROLLBACK IMMEDIATE;")
    [void]$lines.Add("END TRY")
    [void]$lines.Add("BEGIN CATCH")
    [void]$lines.Add("  " + $release)
    [void]$lines.Add("  ALTER DATABASE " + $db + " SET MULTI_USER;")
    [void]$lines.Add("  THROW;")
    [void]$lines.Add("END CATCH;")
    # 进不去说明别人抢先占了槽位、可能已经写入：不结束它再进，整批重来（连 RESTORE 一起）。
    [void]$lines.Add("USE " + $db + ";")
    [void]$lines.Add("IF DB_NAME() <> N'" + $Database + "' BEGIN RAISERROR(N'未能进入 %s（最后错误 924）', 16, 1, N'" + $Database + "'); RETURN; END;")
    if ($WithTask) { [void]$lines.Add((Get-TaskBatchSql)) }
    [void]$lines.Add($script:FingerSql)
    # 仍连在库里（占着唯一的连接）时放开多用户，避免 USE master 之后被别人抢走。
    [void]$lines.Add("ALTER DATABASE " + $db + " SET MULTI_USER;")
    [void]$lines.Add("USE [master];")
    return ($lines -join "`r`n")
}

# 新版过程（有 @restore_snapshot 参数）且库通过守卫时走服务器端紧凑重试；否则走 Get-RevertBatch。
function Test-ProcRevert([string]$Database) {
    $query = "SET NOCOUNT ON; SELECT CASE WHEN " + (Get-ReleaseGuard $Database) +
        " AND EXISTS (SELECT 1 FROM master.sys.parameters WHERE object_id = OBJECT_ID(N'master.dbo.u8co_release_db') AND name = N'@restore_snapshot')" +
        " THEN N'U8REVERT_PROC_NEW' ELSE N'U8REVERT_PROC_OLD' END;"
    foreach ($line in @(Get-SqlText "master" $query)) {
        if (([string]$line).Trim() -eq "U8REVERT_PROC_NEW") { return $true }
    }
    return $false
}

function Get-ProcDeclare {
    return "DECLARE @rel int, @who nvarchar(1000), @tries int, @inside bit = 0, @useTries int = 0;"
}

# 调用新版过程并输出一行 U8REVERT_REL（第五列是尝试次数）。过程用尽重试或拒绝时已 RAISERROR 并返回负数，整批到此为止
# （RETURN 结束批，sqlcmd -b 以非 0 退出，Invoke-Busy 按「最后错误」判断是否整批重来）。$Extra 只由本脚本拼出，库名、快照名已过检查。
function Get-ProcCall([string]$Database, [string]$Extra) {
    [void](Quote-Ident $Database)
    $name = "N'" + $Database + "'"
    return ("SET @who = NULL; SET @tries = NULL;" +
        " EXEC @rel = master.dbo.u8co_release_db " + $name + ", @who = @who OUTPUT, @tries = @tries OUTPUT" + $Extra + ";" +
        " SELECT N'U8REVERT_REL' + CHAR(31) + " + $name + " + CHAR(31) + CONVERT(nvarchar(11), ISNULL(@rel, -1)) + CHAR(31) + ISNULL(@who, N'')" +
        " + CHAR(31) + CONVERT(nvarchar(11), ISNULL(@tries, 0)) + CHAR(30);" +
        " IF ISNULL(@rel, -1) < 0 RETURN;")
}

# 过程里：结束占用会话 → SINGLE_USER + RESTORE + 再 SINGLE_USER（新版过程同一个动态语句；旧版过程还原后另起循环）紧凑重试。
# 回到批里马上 USE 占住唯一的连接；被抢先（924）说明别人进过库、可能已经写入，就让过程连 RESTORE 一起重做（不只是重新占槽位），
# 最多 20 轮，然后照旧导出任务表、算指纹、放开多用户。
function Get-ProcRevertBatch([string]$Database, [string]$Snapshot, [bool]$WithTask) {
    $db = Quote-Ident $Database
    [void](Quote-Ident $Snapshot)
    $lines = New-Object System.Collections.Generic.List[string]
    [void]$lines.Add("SET NOCOUNT ON;")
    [void]$lines.Add((Get-ProcDeclare))
    [void]$lines.Add((Get-ProcCall $Database (", @restore_snapshot = N'" + $Snapshot + "'")))
    [void]$lines.Add("WHILE @inside = 0")
    [void]$lines.Add("BEGIN")
    [void]$lines.Add("  BEGIN TRY")
    [void]$lines.Add("    USE " + $db + ";")
    [void]$lines.Add("    SET @inside = 1;")
    [void]$lines.Add("  END TRY")
    [void]$lines.Add("  BEGIN CATCH")
    [void]$lines.Add("    SET @useTries += 1;")
    [void]$lines.Add("    IF @useTries >= 20 THROW;")
    [void]$lines.Add("    " + (Get-ProcCall $Database (", @restore_snapshot = N'" + $Snapshot + "'")))
    [void]$lines.Add("  END CATCH;")
    [void]$lines.Add("END;")
    [void]$lines.Add("IF DB_NAME() <> N'" + $Database + "' BEGIN RAISERROR(N'未能进入 %s（最后错误 924）', 16, 1, N'" + $Database + "'); RETURN; END;")
    if ($WithTask) { [void]$lines.Add((Get-TaskBatchSql)) }
    [void]$lines.Add($script:FingerSql)
    # 仍连在库里（占着唯一的连接）时放开多用户，避免 USE master 之后被别人抢走。
    [void]$lines.Add("ALTER DATABASE " + $db + " SET MULTI_USER;")
    [void]$lines.Add("USE [master];")
    return ($lines -join "`r`n")
}

function Split-RevertRows($Lines) {
    $finger = New-Object System.Collections.Generic.List[string]
    $tasks = New-Object System.Collections.Generic.List[string]
    $started = ""
    foreach ($line in @($Lines)) {
        $text = ([string]$line).Trim()
        $timeMark = "U8REVERT_TIME$([char]31)"
        $taskMark = "U8REVERT_TASK$([char]31)"
        if ($text.StartsWith($timeMark)) {
            $started = $text.Substring($timeMark.Length)
            continue
        }
        if ($text.StartsWith($taskMark)) {
            $tasks.Add($text.Substring($taskMark.Length))
            continue
        }
        if ($text.StartsWith($script:RelMark)) {
            Write-ReleaseLog $text
            continue
        }
        if ($text.Length -gt 0) { $finger.Add($text) }
    }
    return @{ finger = $finger; tasks = $tasks; started = $started }
}

function Write-LiveTask($Split) {
    $rows = ConvertTo-TaskRows @($Split.tasks)
    $started = [string]$Split.started
    $doc = [ordered]@{
        source = "live"
        restore_started = $started
        server_time = $started
        rows = $rows
    }
    Write-Utf8 (Get-E2EPath "out\taskcfg-live.json") ($doc | ConvertTo-Json -Depth 5)
}

function Restore-Pair([string]$Database, [string]$Snapshot, [bool]$WithTask, [bool]$MissingOk, [bool]$Keep) {
    Assert-SnapPair $Database $Snapshot
    $useProc = Test-ProcRevert $Database
    if (-not (Test-DatabaseName $Snapshot)) {
        if (-not $MissingOk) { throw ("已无快照 " + $Snapshot) }
        Set-MultiUser $Database $useProc
        Write-Host ("已无快照 " + $Snapshot)
        return
    }
    Assert-SnapSource $Database $Snapshot
    $out = Get-E2EPath ("out\fp-inrevert-" + $Database + ".json")
    try {
        # 整批（SINGLE_USER + RESTORE + 同会话指纹）遇到占用就整批重来；再 RESTORE 一次结果相同。
        if ($useProc) {
            Write-Host ("还原 " + $Database + "：服务器端紧凑重试（u8co_release_db @restore_snapshot）")
            $batch = Get-ProcRevertBatch $Database $Snapshot $WithTask
        } else {
            Write-Host ("还原 " + $Database + "：服务器上没有新版 u8co_release_db，按原来的批重试")
            $batch = Get-RevertBatch $Database $Snapshot $WithTask
        }
        $rows = Invoke-Busy { Get-SqlText "master" $batch } $Database "RESTORE"
        $split = Split-RevertRows @($rows)
        $doc = ConvertTo-FingerDoc $Database @($split.finger)
        Write-Utf8 $out ($doc | ConvertTo-Json -Depth 5)
        if ($WithTask) { Write-LiveTask $split }
    } finally {
        Set-MultiUser $Database $useProc
    }
    if ($Keep) {
        # 快照在 RESTORE 之后仍指向同一时点，可以再次还原；session close 照常还原、删除并核对。
        Write-Host ("已还原 " + $Database + "，快照保留，同会话指纹 " + $doc.tables.Count + " 张表")
        return
    }
    Drop-Snap $Snapshot
    Write-Host ("已还原 " + $Database + "，同会话指纹 " + $doc.tables.Count + " 张表")
}

$missingOk = [bool]$AllowMissing -and -not [bool]$KeepSnapshot
& (Join-Path $PSScriptRoot "clear-inrevert.ps1")
Assert-FreshWindow $missingOk
$dataSnap = Get-SnapName (Get-DataDb)
$metaSnap = Get-SnapName (Get-MetaDb)
$dataMissing = -not (Test-DatabaseName $dataSnap)
$metaMissing = -not (Test-DatabaseName $metaSnap)
if ($dataMissing -and -not $missingOk) { throw ("已无快照 " + $dataSnap) }
if ($metaMissing -and -not $missingOk) { throw ("已无快照 " + $metaSnap) }
if (-not $dataMissing) { Save-SnapTaskCfg }
Restore-Pair (Get-DataDb) $dataSnap $true $missingOk ([bool]$KeepSnapshot)
Restore-Pair (Get-MetaDb) $metaSnap $false $missingOk ([bool]$KeepSnapshot)
if ($KeepSnapshot) {
    Write-Host "snapshot-revert ok keep-snapshot"
} else {
    Write-Host "snapshot-revert ok"
}
