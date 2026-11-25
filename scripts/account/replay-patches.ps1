# replay-patches.ps1 — 把 U8 补丁的账套库脚本（UFDBTMP 下 Config-*.Xml 里的 ufdata / ufmeta 部分）重放到一个账套。
#
# 用途：在打过补丁之后才新建或引入的账套，没有经过 U8 补丁的数据库升级（DBEnginSys.exe），
#       缺 UA_PatchList 和补丁加的列（例如 wfaudit.signatureid），账套体检 `patches` 失败，业务组件报列名无效。
#       优先用 U8 自带的升级工具：32 位运行 <U8 安装目录>\Admin\UFDBTMP\DBEnginSys.exe -all，
#       在「获取账套」里先全消、只勾这个账套。本脚本是它的替代，照它的做法执行，但只碰一个账套的两个库。
#
# 与 DBEnginSys 行为一致的地方（实测核对）：Config 按「编号-日期-模块」排序；先跑完全部 ufdata 脚本，再跑全部 ufmeta 脚本；
#   按单独一行的 GO 分批；文件按系统缺省代码页（中文 Windows 为 936）读，有 BOM 按 BOM；每个文件一个事务，
#   出错回滚并停下；文件名含 __new 的跳过；ufdata 脚本按语言后缀过滤（nl 总要，其余看 -Locales）；
#   每个库跑完后在这个库的 UA_PatchList 里记下每个补丁号。
# 不做的：ufsystem / u8workflow 等共享库的脚本、*dll 类名条目（只打印出来）。
#
# 护栏：
#   * -DataDb 必须是 UFDATA_<三位账套>_<年度>，-MetaDb 必须是同一账套的 UFMeta_<账套>；共享库和系统库一律拒绝。
#   * 只连这两个库（和 master 之外的任何库都不连）；批次里出现 USE 别的库就停。
#   * 执行之前扫一遍全部脚本，列出写三段名（库.架构.对象、库..对象）的批次（INSERT / UPDATE / DELETE / MERGE /
#     CREATE / ALTER / DROP / TRUNCATE / EXEC / SELECT…INTO）：
#       - 对 UFSystem 的 INSERT / UPDATE / MERGE 是补丁里已知的可重复执行的定义 upsert，标「提示」照常执行
#         （DBEnginSys 同样会执行）；
#       - 对 UFSystem 的其他写法（DELETE / DROP / TRUNCATE / CREATE / SELECT…INTO / ALTER / EXEC）标「拒绝」，
#         加 -Force 才改标「放行」照常执行；
#       - 写目标两个库、tempdb、UFSystem 之外其他库的一律标「拒绝」，-Force 也不放行。
#     有「拒绝」时正式执行一个文件都不跑就停，-WhatIf 只列出。识别只看写语句后紧跟的对象名，不是安全边界，
#     请先看 -WhatIf 的清单。
#   * -DeniedAccounts 里的账套拒绝（例如正式账套）。
#   * 账套有业务单据（总账凭证、生产订单、检验单、采购订单、销售订单、采购入库、销售出库、应收应付单、收付款单）时拒绝，除非 -Force。
#   * 不带 -WhatIf 时必须带 -BackupConfirmed：表示两个库都已做过 COPY_ONLY 完整备份。
#   * 连接串只从环境变量 U8_PATCH_CS（或 -ConnectionString）取，不要写进文件或命令历史。
#
# 还原：恢复两个库的备份。脚本没有逐条撤销的办法。
#
# 例子（在 U8 数据库服务器上，Windows PowerShell 5.1 或 pwsh 7 均可）：
#   $env:U8_PATCH_CS = 'Data Source=<SQL 实例>;Integrated Security=SSPI'
#   .\replay-patches.ps1 -DataDb UFDATA_801_2024 -MetaDb UFMeta_801 -ScriptDir 'C:\U8SOFT\Admin\UFDBTMP' -WhatIf
#   .\replay-patches.ps1 -DataDb UFDATA_801_2024 -MetaDb UFMeta_801 -ScriptDir 'C:\U8SOFT\Admin\UFDBTMP' `
#     -DeniedAccounts 998 -BackupConfirmed
# 跑完用 SELECT COUNT(*) FROM UA_PatchList 对比 UFSystem..UA_PatchList，再跑账套体检。

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $DataDb,

    [Parameter(Mandatory = $true)]
    [string] $MetaDb,

    # U8 安装目录下的 Admin\UFDBTMP（补丁把 Config-*.Xml 和 .sql 放在这里）。
    [Parameter(Mandatory = $true)]
    [string] $ScriptDir,

    # 优先用环境变量 U8_PATCH_CS。不要把口令写进文件。
    [string] $ConnectionString = $env:U8_PATCH_CS,

    # 只列出要执行的文件和要记的补丁号，不连数据库。
    [switch] $WhatIf,

    [switch] $BackupConfirmed,

    # 有业务单据也照样执行；对 UFSystem 的非 upsert 写法（DELETE / DROP 等）也照样执行。只用于确认过的测试账套。
    [switch] $Force,

    [string[]] $DeniedAccounts = @(),

    # ufdata 脚本的语言后缀：nl 总要；这里列出账套的语言（对应 U8LangDefine）。缺省简体中文。
    [string[]] $Locales = @('nl', 'cn'),

    [int] $CommandTimeout = 7200
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:SharedDbs = @('ufsystem', 'ufmom', 'u8workflow', 'ufreport', 'ufportal', 'utu', 'pdm',
    'ufmodel', 'ufsmartm', 'master', 'tempdb', 'model', 'msdb')

function Test-AllowedDbName {
    param([string] $Name)
    return ($Name -eq $DataDb) -or ($Name -eq $MetaDb)
}

function Assert-NotSharedDb {
    param([string] $Name)
    $lower = $Name.ToLowerInvariant()
    foreach ($banned in $script:SharedDbs) {
        if ($lower -eq $banned -or $lower.StartsWith($banned + '_')) {
            throw "拒绝库 '$Name'：共享库或系统库不在本脚本范围内。"
        }
    }
}

function Assert-AccountPair {
    if ($DataDb -notmatch '^(?i)UFDATA_(\d{3})_(\d{4})$') {
        throw "拒绝 -DataDb '$DataDb'：要求 UFDATA_<三位账套>_<年度>，例如 UFDATA_801_2024。"
    }
    $acc = $Matches[1]
    if ($MetaDb -notmatch '^(?i)UFMeta_(\d{3})$') {
        throw "拒绝 -MetaDb '$MetaDb'：要求 UFMeta_<账套>，例如 UFMeta_801。"
    }
    if ($Matches[1] -ne $acc) {
        throw "拒绝 -MetaDb '$MetaDb'：账套号 $($Matches[1]) 与 -DataDb 的 $acc 不同。"
    }
    if ($DeniedAccounts -contains $acc) {
        throw "拒绝账套 ${acc}：它在 -DeniedAccounts 里。"
    }
    Assert-NotSharedDb $DataDb
    Assert-NotSharedDb $MetaDb
    return $acc
}

function Get-BaseConnectionString {
    if ([string]::IsNullOrWhiteSpace($ConnectionString)) {
        throw '没有连接串。请设置环境变量 U8_PATCH_CS（或传 -ConnectionString），不要把凭据写进文件。'
    }
    $builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder $ConnectionString.Trim()
    $cat = [string] $builder['Initial Catalog']
    if ($cat -and -not (Test-AllowedDbName $cat) -and $cat -ne 'master') {
        throw "拒绝连接串里的 Initial Catalog '$cat'：只能是 master、$DataDb 或 $MetaDb。"
    }
    [void] $builder.Remove('Initial Catalog')
    $builder['Current Language'] = 'Simplified Chinese'
    return $builder
}

function Connect-U8Db {
    param([System.Data.SqlClient.SqlConnectionStringBuilder] $Builder, [string] $Catalog)
    if (-not (Test-AllowedDbName $Catalog)) { throw "拒绝连接 '$Catalog'。" }
    $b = New-Object System.Data.SqlClient.SqlConnectionStringBuilder ($Builder.ConnectionString)
    $b['Initial Catalog'] = $Catalog
    $conn = New-Object System.Data.SqlClient.SqlConnection ($b.ConnectionString)
    $conn.Open()
    return $conn
}

function Invoke-Scalar {
    param([System.Data.SqlClient.SqlConnection] $Conn, [string] $Sql)
    $cmd = $Conn.CreateCommand()
    try {
        $cmd.CommandTimeout = $CommandTimeout
        $cmd.CommandText = $Sql
        return $cmd.ExecuteScalar()
    } finally { $cmd.Dispose() }
}

function Assert-NoBusinessData {
    param([System.Data.SqlClient.SqlConnectionStringBuilder] $Builder)
    if ($Force) { Write-Warning '-Force：跳过业务数据检查。'; return }
    $conn = Connect-U8Db -Builder $Builder -Catalog $DataDb
    try {
        $tables = @('GL_accvouch', 'mom_order', 'QMCHECKVOUCHERS', 'PO_Pomain', 'SO_SOMain', 'RdRecord01', 'RdRecord32',
            'Ap_Vouch', 'Ap_CloseBill')
        $probe = ($tables | ForEach-Object { "EXISTS (SELECT 1 FROM dbo.$_)" }) -join ' OR '
        $sql = "SELECT CASE WHEN $probe THEN 1 ELSE 0 END"
        if ([int] (Invoke-Scalar -Conn $conn -Sql $sql) -ne 0) {
            throw "$DataDb 已有业务单据。正式账套请用 DBEnginSys.exe；确属测试账套才加 -Force。"
        }
    } finally { $conn.Close(); $conn.Dispose() }
}

function Get-ConfigSortKey {
    param([string] $FileName)
    # 与 DBEnginSys 的执行顺序一致：Config-{编号}-{yymmdd}-{模块}.Xml，先按编号，再按日期。
    if ($FileName -match '(?i)^Config-(\d+)-(\d+)-(.*)\.xml$') {
        return '{0:D10}-{1:D10}-{2}' -f [int64]$Matches[1], [int64]$Matches[2], $Matches[3].ToLowerInvariant()
    }
    return $FileName.ToLowerInvariant()
}

function Get-PatchNumber {
    param([string] $ConfigFileName)
    $n = $ConfigFileName
    if ($n.StartsWith('Config-', [StringComparison]::OrdinalIgnoreCase)) { $n = $n.Substring(7) }
    if ($n.EndsWith('.xml', [StringComparison]::OrdinalIgnoreCase)) { $n = $n.Substring(0, $n.Length - 4) }
    return $n.ToLowerInvariant()
}

function Get-LegacyEncoding {
    # 补丁脚本在中文 Windows 上是系统默认代码页 936。pwsh 7 需要注册代码页提供程序。
    try { [System.Text.Encoding]::RegisterProvider([System.Text.CodePagesEncodingProvider]::Instance) } catch { }
    return [System.Text.Encoding]::GetEncoding(936)
}

function Get-XmlTexts {
    param([xml] $Xml, [string] $XPath)
    return , @($Xml.SelectNodes($XPath) | ForEach-Object { $_.InnerText.Trim() } | Where-Object { $_ })
}

function Read-ConfigXml {
    param([string] $Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    if ($hasBom) { $text = [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3) }
    else { $text = (Get-LegacyEncoding).GetString($bytes) }
    $text = [regex]::Replace($text, 'encoding="gb2312"', 'encoding="utf-8"', 'IgnoreCase')
    [xml] $xml = $text
    if (-not $xml.SelectSingleNode('/body')) { throw "$Path 里没有 <body>。" }
    $name = [IO.Path]::GetFileName($Path)
    return [pscustomobject]@{
        File   = $name
        Patch  = Get-PatchNumber $name
        Ufdata = Get-XmlTexts -Xml $xml -XPath '/body/ufdata/sqlfile'
        Ufmeta = Get-XmlTexts -Xml $xml -XPath '/body/ufmeta/sqlfile'
        Dll    = Get-XmlTexts -Xml $xml -XPath '/body/ufdatadll/classname | /body/ufmetadll/classname'
    }
}

function Test-FileIsNeed {
    param([string] $FileName)
    return -not ([IO.Path]::GetFileNameWithoutExtension($FileName) -match '(?i)__new')
}

function Test-LanguageMatch {
    param([string] $FileName)
    # 只用于 ufdata：去掉 .sql 和 __up 后取最后一个 '_' 之后的后缀；不是语言后缀的照常执行。
    $stem = [IO.Path]::GetFileNameWithoutExtension($FileName) -replace '(?i)__up', ''
    $parts = $stem.Split('_')
    if ($parts.Length -lt 2) { return $true }
    $suf = $parts[-1].ToLowerInvariant()
    if (@('cn', 'en', 'tw', 'us') -notcontains $suf) { return $true }
    return ($Locales | ForEach-Object { $_.ToLowerInvariant() }) -contains $suf
}

function Get-WorkList {
    param([string] $Dir)
    $cfgs = Get-ChildItem -LiteralPath $Dir -File | Where-Object { $_.Name -like 'Config-*.xml' } |
        Sort-Object { Get-ConfigSortKey $_.Name }
    $data = New-Object System.Collections.Generic.List[object]
    $meta = New-Object System.Collections.Generic.List[object]
    $flags = New-Object System.Collections.Generic.List[string]
    foreach ($c in $cfgs) {
        $parsed = Read-ConfigXml $c.FullName
        if ($parsed.Dll.Count -gt 0) { Write-Host ("跳过 {0} 的 dll 类名：{1}" -f $parsed.File, ($parsed.Dll -join ', ')) }
        foreach ($sql in ($parsed.Ufdata | Where-Object { (Test-FileIsNeed $_) -and (Test-LanguageMatch $_) })) {
            $data.Add([pscustomobject]@{ Patch = $parsed.Patch; Db = $DataDb; Sql = $sql })
        }
        foreach ($sql in ($parsed.Ufmeta | Where-Object { Test-FileIsNeed $_ })) {
            $meta.Add([pscustomobject]@{ Patch = $parsed.Patch; Db = $MetaDb; Sql = $sql })
        }
        # DBEnginSys 即使某个 Config 没有脚本也记下补丁号。
        $flags.Add($parsed.Patch)
    }
    $ordered = New-Object System.Collections.Generic.List[object]
    $ordered.AddRange($data)
    foreach ($p in $flags) { $ordered.Add([pscustomobject]@{ Patch = $p; Db = $DataDb; Sql = $null }) }
    $ordered.AddRange($meta)
    foreach ($p in $flags) { $ordered.Add([pscustomobject]@{ Patch = $p; Db = $MetaDb; Sql = $null }) }
    return , $ordered
}

function Resolve-SqlPath {
    param([string] $Dir, [string] $Name)
    $direct = Join-Path $Dir $Name
    if (Test-Path -LiteralPath $direct) { return (Get-Item -LiteralPath $direct).FullName }
    $hit = @(Get-ChildItem -LiteralPath $Dir -File | Where-Object { $_.Name -ieq $Name })
    if ($hit.Count -gt 0) { return $hit[0].FullName }
    return $null
}

function Read-SqlText {
    param([string] $Path)
    $reader = New-Object System.IO.StreamReader($Path, (Get-LegacyEncoding), $true)
    try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
}

function Split-GoBatches {
    param([string] $Text)
    # 批次分隔行：转小写、去掉制表符、Trim 之后等于 go。
    $batches = New-Object System.Collections.Generic.List[string]
    $sb = New-Object System.Text.StringBuilder
    foreach ($line in ($Text -split "`r`n|`n|`r")) {
        if ($line.ToLowerInvariant().Replace("`t", '').Trim() -eq 'go') {
            $chunk = $sb.ToString().Trim()
            if ($chunk.Length -gt 0) { $batches.Add($chunk) }
            [void] $sb.Clear()
        } else { [void] $sb.AppendLine($line) }
    }
    $tail = $sb.ToString().Trim()
    if ($tail.Length -gt 0) { $batches.Add($tail) }
    return , $batches.ToArray()
}

function Assert-BatchStaysOnTarget {
    param([string] $Batch, [string] $TargetDb)
    if ($Batch -match '(?im)^\s*USE\s+(?:\[(?<n>[^\]]+)\]|(?<n>\S+))') {
        $n = $Matches['n'].Trim().TrimEnd(';')
        if ($n -ne $TargetDb) { throw "拒绝：发往 $TargetDb 的脚本里有 USE $n。" }
    }
}

# 写语句（INSERT/UPDATE/DELETE/MERGE/CREATE/ALTER/DROP/TRUNCATE/EXEC、SELECT…INTO 的 INTO）后面紧跟的三段名
# 「库.架构.对象」或「库..对象」；verb 组是写语句的第一个词（SELECT…INTO 记为 INTO）。
# 只看语句开头的对象名，写成 DELETE t FROM 库..表 之类的会漏，所以这只是清单，不是安全边界：先用 -WhatIf 看。
$script:CrossWritePattern = '(?i)\b(?:(?<verb>INSERT)\s+(?:INTO\s+)?|(?<verb>UPDATE)\s+|(?<verb>DELETE)\s+(?:FROM\s+)?' +
    '|(?<verb>MERGE)\s+(?:INTO\s+)?|(?<verb>INTO)\s+' +
    '|(?<verb>CREATE)\s+(?:OR\s+ALTER\s+)?(?:TABLE|VIEW|PROC|PROCEDURE|FUNCTION|TRIGGER)\s+' +
    '|(?<verb>ALTER)\s+(?:TABLE|VIEW|PROC|PROCEDURE|FUNCTION|TRIGGER)\s+' +
    '|(?<verb>DROP)\s+(?:TABLE|VIEW|PROC|PROCEDURE|FUNCTION|TRIGGER)\s+(?:IF\s+EXISTS\s+)?|(?<verb>TRUNCATE)\s+TABLE\s+' +
    '|(?<verb>EXEC)(?:UTE)?\s+(?:@\w+\s*=\s*)?)(?:\[(?<db>[^\]]+)\]|(?<db>[A-Za-z_][\w@$#]*))\s*\.\s*(?:\[[^\]]*\]|[\w@$#]*)\s*\.\s*(?:\[[^\]]+\]|[\w@$#]+)'

$script:UpsertVerbs = @('INSERT', 'UPDATE', 'MERGE')

function Get-CrossWriteKind {
    param([string] $Db, [string] $Verb)
    # 目标两个库和 tempdb 不算跨库。UFSystem 的 INSERT/UPDATE/MERGE 是已知的定义 upsert，只提示；
    # UFSystem 的其他写法 -Force 才放行；其余库一律拒绝。
    if ((Test-AllowedDbName $Db) -or $Db -ieq 'tempdb') { return $null }
    if ($Db -ine 'ufsystem') { return '拒绝' }
    if ($script:UpsertVerbs -contains $Verb.ToUpperInvariant()) { return '提示' }
    if ($Force) { return '放行' }
    return '拒绝'
}

function Find-CrossDbWrites {
    param([System.Collections.Generic.List[object]] $Jobs)
    $found = New-Object System.Collections.Generic.List[object]
    foreach ($job in $Jobs) {
        if ($null -eq $job.Sql) { continue }
        $batches = Split-GoBatches (Read-SqlText (Resolve-SqlPath -Dir $ScriptDir -Name $job.Sql))
        for ($i = 0; $i -lt $batches.Count; $i++) {
            foreach ($m in [regex]::Matches($batches[$i], $script:CrossWritePattern)) {
                $kind = Get-CrossWriteKind $m.Groups['db'].Value $m.Groups['verb'].Value
                if ($null -eq $kind) { continue }
                $found.Add([pscustomobject]@{ Kind = $kind; Patch = $job.Patch; Sql = $job.Sql; Batch = $i + 1; Text = $m.Value })
            }
        }
    }
    return , $found
}

function Show-CrossDbWrites {
    param([System.Collections.Generic.List[object]] $Found)
    if ($Found.Count -eq 0) { return }
    Write-Warning ("有 {0} 处写目标两个库之外的三段名（「提示」= UFSystem 的 INSERT/UPDATE/MERGE，照常执行；「放行」= UFSystem 的其他写法，因 -Force 照常执行；「拒绝」= 正式执行前就停）：" -f $Found.Count)
    foreach ($f in $Found) {
        Write-Host ("  [{0}] {1} {2} 第 {3} 批：{4}" -f $f.Kind, $f.Patch, $f.Sql, $f.Batch, $f.Text)
    }
    $refused = @($Found | Where-Object { $_.Kind -eq '拒绝' })
    if ($refused.Count -gt 0 -and -not $WhatIf) {
        throw "有 $($refused.Count) 处写别的库或对 UFSystem 做非 upsert 的写（清单见上），什么都没执行。这些补丁请用 DBEnginSys.exe 打；只涉及 UFSystem 且确属测试环境才加 -Force。"
    }
}

function Invoke-SqlFile {
    param([System.Data.SqlClient.SqlConnection] $Conn, [string] $TargetDb, [string] $Path)
    if ($Conn.Database -ne $TargetDb) { throw "连接的库 '$($Conn.Database)' 不是目标 '$TargetDb'。" }
    $batches = Split-GoBatches (Read-SqlText $Path)
    $tx = $Conn.BeginTransaction()
    $batch = $null
    try {
        foreach ($batch in $batches) {
            Assert-BatchStaysOnTarget -Batch $batch -TargetDb $TargetDb
            $cmd = $Conn.CreateCommand()
            $cmd.Transaction = $tx
            $cmd.CommandTimeout = $CommandTimeout
            $cmd.CommandText = $batch
            try { [void] $cmd.ExecuteNonQuery() } finally { $cmd.Dispose() }
        }
        $tx.Commit()
    } catch {
        try { $tx.Rollback() } catch { }
        throw ("运行脚本 {0} 时出错，出错语句：{1}，错误信息：{2}" -f $Path, $batch, $_.Exception.Message)
    } finally { $tx.Dispose() }
}

function Save-PatchFlag {
    param([System.Data.SqlClient.SqlConnection] $Conn, [string] $PatchNumber)
    if (-not (Test-AllowedDbName $Conn.Database)) { throw "拒绝在 '$($Conn.Database)' 写 UA_PatchList。" }
    $ddl = "IF NOT EXISTS (SELECT 1 FROM sysobjects WHERE name = 'UA_PatchList' AND xtype = 'U') " +
        "CREATE TABLE UA_PatchList (iIndex int IDENTITY(1,1), patchNumber nvarchar(200) PRIMARY KEY, " +
        "firstExecutionDate datetime, lastExecutionDate datetime)"
    [void] (Invoke-Scalar -Conn $Conn -Sql $ddl)
    $n = $PatchNumber.Replace("'", "''")
    $upsert = "IF EXISTS (SELECT 1 FROM UA_PatchList WHERE patchNumber = N'$n') " +
        "UPDATE UA_PatchList SET lastExecutionDate = GETDATE() WHERE patchNumber = N'$n' " +
        "ELSE INSERT INTO UA_PatchList (patchNumber, firstExecutionDate, lastExecutionDate) VALUES (N'$n', GETDATE(), GETDATE())"
    [void] (Invoke-Scalar -Conn $Conn -Sql $upsert)
}

function Invoke-Job {
    param([object] $Job, [hashtable] $Open, [System.Data.SqlClient.SqlConnectionStringBuilder] $Builder)
    if (-not (Test-AllowedDbName $Job.Db)) { throw "拒绝目标库 '$($Job.Db)'。" }
    if (-not $Open.ContainsKey($Job.Db)) {
        $Open[$Job.Db] = Connect-U8Db -Builder $Builder -Catalog $Job.Db
        Write-Host ("--开始升级数据库 {0}" -f $Job.Db)
    }
    if ($null -eq $Job.Sql) { Save-PatchFlag -Conn $Open[$Job.Db] -PatchNumber $Job.Patch; return }
    $path = Resolve-SqlPath -Dir $ScriptDir -Name $Job.Sql
    Invoke-SqlFile -Conn $Open[$Job.Db] -TargetDb $Job.Db -Path $path
}

function Assert-FilesPresent {
    param([System.Collections.Generic.List[object]] $Jobs)
    $missing = @($Jobs | Where-Object { $null -ne $_.Sql -and -not (Resolve-SqlPath -Dir $ScriptDir -Name $_.Sql) })
    if ($missing.Count -gt 0) {
        $names = ($missing | ForEach-Object { "$($_.Patch): $($_.Sql)" }) -join '; '
        throw "$ScriptDir 下缺少脚本，什么都没执行：$names"
    }
}

function Invoke-WorkList {
    param([System.Collections.Generic.List[object]] $Jobs, [System.Data.SqlClient.SqlConnectionStringBuilder] $Builder)
    $open = @{}
    $i = 0
    try {
        foreach ($job in $Jobs) {
            $i++
            $what = if ($null -eq $job.Sql) { 'UA_PatchList' } else { $job.Sql }
            Write-Host ("[{0}/{1}] {2} {3} -> {4}" -f $i, $Jobs.Count, $job.Patch, $what, $job.Db)
            if (-not $WhatIf) { Invoke-Job -Job $job -Open $open -Builder $Builder }
        }
    } finally {
        foreach ($k in @($open.Keys)) {
            Write-Host ("--结束升级数据库 {0}" -f $k)
            try { $open[$k].Close(); $open[$k].Dispose() } catch { }
        }
    }
}

# ---- main ----
[void] (Assert-AccountPair)
if (-not (Test-Path -LiteralPath $ScriptDir)) { throw "找不到 -ScriptDir：$ScriptDir" }
if (-not $WhatIf -and -not $BackupConfirmed) {
    throw '先请 DBA 对两个库各做一份 COPY_ONLY 完整备份，再加 -BackupConfirmed 重跑；或者先用 -WhatIf 看清单。'
}

$jobs = Get-WorkList -Dir $ScriptDir
Assert-FilesPresent -Jobs $jobs
Show-CrossDbWrites -Found (Find-CrossDbWrites -Jobs $jobs)
Write-Host ("已读入 Config，共 {0} 项（含 UA_PatchList 记录）。" -f $jobs.Count)

$builder = $null
if (-not $WhatIf) {
    $builder = Get-BaseConnectionString
    Assert-NoBusinessData -Builder $builder
}
try {
    Invoke-WorkList -Jobs $jobs -Builder $builder
    if (-not $WhatIf) { Write-Host '--升级成功' }
} catch {
    Write-Error ("数据库升级出现错误！{0}`n数据库升级失败。已提交的文件不会回滚，请恢复备份或修正后重跑。" -f $_.Exception.Message)
    throw
}
