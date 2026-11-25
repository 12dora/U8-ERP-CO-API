#Requires -Version 7.4
#Requires -PSEdition Core
# 测试账套端到端脚本的公共断言。删除和覆盖只允许 e2e 目录里的文件。
# 站点值只从同目录的 e2e.json 读（由 Linux 端按配置生成后上传），这里不写死任何账套、路径或服务名。
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
$ProgressPreference = "SilentlyContinue"
$PSNativeCommandUseErrorActionPreference = $false

function Assert-CfgText($Value, [string]$Name, [string]$Pattern) {
    $text = [string]$Value
    if ($text -cnotmatch $Pattern) { throw ("e2e.json 的 " + $Name + " 不合法") }
    return $text
}

function Assert-CfgPath($Value, [string]$Name) {
    $text = Assert-CfgText $Value $Name '^[A-Za-z]:(\\[A-Za-z0-9_. ()-]+)+$'
    foreach ($part in $text.Split("\")) {
        if (($part -eq ".") -or ($part -eq "..")) { throw ("e2e.json 的 " + $Name + " 含 . 或 ..") }
    }
    return $text
}

function Read-CfgNames($Doc) {
    $acc = Assert-CfgText $Doc.Account "Account" '^\d{3}$'
    [void](Assert-CfgText $Doc.DataDb "DataDb" ('^UFDATA_' + $acc + '_\d{4}$'))
    [void](Assert-CfgText $Doc.MetaDb "MetaDb" ('^UFMeta_' + $acc + '$'))
    [void](Assert-CfgText $Doc.SnapSuffix "SnapSuffix" '^_[A-Za-z0-9_]{3,40}$')
    [void](Assert-CfgText $Doc.RevertTask "RevertTask" '^[A-Za-z0-9-]{3,60}$')
    [void](Assert-CfgText $Doc.ProbeTask "ProbeTask" '^[A-Za-z0-9-]{3,60}$')
    [void](Assert-CfgText $Doc.ServiceName "ServiceName" '^[A-Za-z0-9_.-]{1,80}$')
    [void](Assert-CfgText $Doc.BridgeExe "BridgeExe" '^[A-Za-z0-9_.-]{1,80}\.exe$')
    [void](Assert-CfgText $Doc.BridgePath "BridgePath" '^/[A-Za-z0-9/_-]*$')
    [void](Assert-CfgText $Doc.SqlServer "SqlServer" '^[A-Za-z0-9._\\$-]{1,160}(,\d{1,5})?$')
    [void](Assert-CfgText $Doc.SqlService "SqlService" '^[A-Za-z0-9_$]{1,80}$')
    if (-not [string]::IsNullOrEmpty([string]$Doc.Principal)) {
        [void](Assert-CfgText $Doc.Principal "Principal" '^[A-Za-z0-9._-]{1,64}\\[A-Za-z0-9._-]{1,64}$')
    }
    foreach ($name in @($Doc.DeniedDbs)) {
        [void](Assert-CfgText $name "DeniedDbs" '^UF(DATA|Meta)_\d{3}$')
        if ($name -match ('_' + $acc + '$')) { throw "e2e.json 把测试账套列进了 DeniedDbs" }
    }
}

function Read-CfgPaths($Doc) {
    $root = Assert-CfgPath $Doc.RemoteRoot "RemoteRoot"
    $here = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd("\")
    # 脚本只在自己所在的目录里工作：e2e.json 写的根目录必须就是脚本目录。
    if (-not $here.Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) { throw "e2e.json 的 RemoteRoot 不是脚本所在目录" }
    foreach ($key in @("BridgeRoot", "U8Home", "Pwsh", "SqlCmd")) { [void](Assert-CfgPath $Doc.$key $key) }
    $items = @($Doc.Protected)
    if ($items.Count -lt 1) { throw "e2e.json 缺少 Protected" }
    foreach ($item in $items) {
        [void](Assert-CfgText $item "Protected" '^[A-Za-z]:\\([A-Za-z0-9_. ()-]+(\\[A-Za-z0-9_. ()-]+)*)?$')
    }
    $port = [int]$Doc.BridgePort
    if (($port -lt 1) -or ($port -gt 65535)) { throw "e2e.json 的 BridgePort 不合法" }
    if ([int64]$Doc.MinFreeBytes -lt 2GB) { throw "e2e.json 的 MinFreeBytes 低于 2 GiB" }
}

function Assert-RootLocked([string]$Root) {
    # e2e.json 和这些脚本只有在目录锁好之后才可信（Linux 端 ensure 阶段锁：断开继承，所有者 Administrators）。
    # 计划任务以最高权限运行这里的脚本，目录还继承上级的宽权限时别人可能改过它们。
    $bit = [System.IO.FileAttributes]::ReparsePoint
    if (([System.IO.File]::GetAttributes($Root) -band $bit) -eq $bit) { throw ("拒绝继续：e2e 目录是重解析点 " + $Root) }
    $acl = Get-Acl -LiteralPath $Root
    $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    if (@("S-1-5-18", "S-1-5-32-544") -notcontains $owner) { throw "e2e 目录的所有者不是 SYSTEM 或 Administrators，不信任 e2e.json" }
    if (-not $acl.AreAccessRulesProtected) { throw "e2e 目录还在继承上级目录的权限，不信任 e2e.json（先由 Linux 端重跑一次，锁好目录）" }
    # 返回 SYSTEM、Administrators 以外被授权的账户；读完 e2e.json 后由 Assert-RootGrantees 核对只剩计划任务账户。
    $extra = @()
    foreach ($rule in $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -ne "Allow") { continue }
        $sid = [string]$rule.IdentityReference.Value
        if (@("S-1-5-18", "S-1-5-32-544") -notcontains $sid) { $extra += $sid }
    }
    return , $extra
}

# 换过计划任务账户后，旧账户留下的授权还能改这里的脚本。Linux 端 ensure 会删掉它们。
function Assert-RootGrantees([string[]]$Extra, $Doc) {
    $name = [string]$Doc.Principal
    if ([string]::IsNullOrEmpty($name)) { $want = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
    else { $want = (New-Object System.Security.Principal.NTAccount($name)).Translate([System.Security.Principal.SecurityIdentifier]).Value }
    foreach ($sid in $Extra) {
        if ($sid -ne $want) { throw ("e2e 目录还授权给了 " + $sid + "，不信任 e2e.json（先由 Linux 端重跑一次，锁好目录）") }
    }
}

function Read-E2EConfig {
    $extra = Assert-RootLocked ([System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd("\"))
    $path = Join-Path $PSScriptRoot "e2e.json"
    if (-not [System.IO.File]::Exists($path)) { throw "找不到 e2e.json，请先由 Linux 端上传" }
    $doc = [System.IO.File]::ReadAllText($path) | ConvertFrom-Json
    if ([int]$doc.Version -ne 1) { throw "e2e.json 版本不对" }
    Read-CfgNames $doc
    Read-CfgPaths $doc
    Assert-RootGrantees $extra $doc
    return $doc
}

$script:Cfg = Read-E2EConfig
$script:E2ERoot = [string]$script:Cfg.RemoteRoot
$script:SqlExe = [string]$script:Cfg.SqlCmd

function Get-E2EPath([string]$Rel) {
    return (Join-Path $script:E2ERoot $Rel)
}

function Get-BridgePath([string]$Rel) {
    return (Join-Path ([string]$script:Cfg.BridgeRoot) $Rel)
}

function Get-BinPathExe([string]$Path) {
    # binPath 开头的程序路径：带引号时取引号里的，否则取到第一个空格。
    $text = $Path.Trim()
    if ($text.StartsWith('"')) {
        $end = $text.IndexOf('"', 1)
        if ($end -lt 1) { return "" }
        return $text.Substring(1, $end - 1)
    }
    $space = $text.IndexOf(' ')
    if ($space -lt 0) { return $text }
    return $text.Substring(0, $space)
}

# 服务名只是 e2e.json 里的一个字符串。预检、停、启、换程序之前先确认它真是本通道的桥：binPath 的程序正好是
# <BridgeRoot>\bin\<BridgeExe>，并带 --root "<BridgeRoot>"。否则可能停掉别的调试通道或生产的桥。返回问题说明，没问题返回空串。
function Get-ServiceRootProblem {
    $name = [string]$script:Cfg.ServiceName
    $root = [string]$script:Cfg.BridgeRoot
    $svc = Get-CimInstance -ClassName Win32_Service -Filter ("Name='" + $name + "'")
    if ($null -eq $svc) { return ("找不到服务 " + $name) }
    $path = [string]$svc.PathName
    $exe = [System.IO.Path]::GetFullPath((Get-BridgePath ("bin\" + [string]$script:Cfg.BridgeExe)))
    if (-not (Get-BinPathExe $path).Equals($exe, [System.StringComparison]::OrdinalIgnoreCase)) {
        return ("服务 " + $name + " 的程序不是 " + $exe + "，可能是别的调试通道或生产的桥，拒绝碰它。当前 binPath：" + $path)
    }
    $want = '--root "' + $root + '"'
    if ($path.IndexOf($want, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { return "" }
    return ("服务 " + $name + " 的 binPath 没有 " + $want + "（旧版本登记的服务），换上新版桥后会找不到配置。" +
        "先在服务器上用新版套件重跑 install.ps1 -Root '" + $root + "' -ServiceName " + $name + "（会改写 binPath）。当前 binPath：" + $path)
}

function Assert-ServiceRoot {
    $problem = Get-ServiceRootProblem
    if ($problem -ne "") { throw $problem }
}

function Get-Principal {
    # 计划任务以运行 e2e 的 Windows 账户身份跑。配置没写时就是当前 SSH 登录的本机账户。
    $name = [string]$script:Cfg.Principal
    if (-not [string]::IsNullOrEmpty($name)) { return $name }
    return ($env:COMPUTERNAME + "\" + $env:USERNAME)
}

function Test-Protected([string]$Full) {
    foreach ($item in @($script:Cfg.Protected)) {
        $top = ([string]$item).TrimEnd("\")
        if ($Full.Equals($top, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
        if ($Full.StartsWith($top + "\", [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Assert-E2EPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw "路径为空" }
    $full = [System.IO.Path]::GetFullPath($Path)
    if (Test-Protected $full) { throw "拒绝 U8 安装目录所在路径" }
    $root = [System.IO.Path]::GetFullPath($script:E2ERoot)
    if ($full.Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) { return $full }
    $prefix = $root.TrimEnd("\") + "\"
    if ($full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { return $full }
    throw ("路径不在 " + $root + "\ 之下")
}

function Assert-NoReparse([string]$Path) {
    $attr = [System.IO.File]::GetAttributes($Path)
    $bit = [System.IO.FileAttributes]::ReparsePoint
    if (($attr -band $bit) -eq $bit) { throw ("拒绝继续：重解析点 " + $Path) }
}

function Get-DataDb { return [string]$script:Cfg.DataDb }
function Get-MetaDb { return [string]$script:Cfg.MetaDb }

function Get-SnapName([string]$Database) {
    Assert-AllowedDb $Database
    return ($Database + [string]$script:Cfg.SnapSuffix)
}

function Assert-AllowedDb([string]$Name) {
    if ($Name -ceq (Get-DataDb)) { return }
    if ($Name -ceq (Get-MetaDb)) { return }
    throw "拒绝访问名单以外的数据库"
}

function Assert-SnapPair([string]$Database, [string]$Snapshot) {
    Assert-AllowedDb $Database
    $expect = $Database + [string]$script:Cfg.SnapSuffix
    if ($Snapshot -cne $expect) { throw "快照名不在本次允许列表" }
}

# snapshot-revert 和 snapshot-drop 共用。
function Assert-SnapSource([string]$Database, [string]$Snapshot) {
    $query = "SET NOCOUNT ON; SELECT ISNULL(DB_NAME(source_database_id), N'') FROM sys.databases WHERE name = N'" + $Snapshot + "';"
    $got = ""
    foreach ($line in @(Get-SqlText "master" $query)) {
        $text = ([string]$line).Trim()
        if ($text.Length -gt 0) { $got = $text }
    }
    if ($got -ne $Database) { throw "快照的源库不是预期的测试账套库" }
}

function Drop-Snap([string]$Snapshot) {
    Invoke-Master ("DROP DATABASE " + (Quote-Ident $Snapshot) + ";")
}

function Test-HasText([string]$Query, [string]$Needle) {
    if ([string]::IsNullOrEmpty($Needle)) { return $false }
    return $Query.IndexOf($Needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-PathText([string]$Query, [string]$Path) {
    # 反斜杠和正斜杠两种写法都算。
    if (Test-HasText $Query $Path) { return $true }
    return (Test-HasText $Query ($Path.Replace("\", "/")))
}

function Assert-NoProd([string]$Query) {
    # 禁止碰的账套来自配置 account.denied；名单外的其他 UFDATA_ 库另由预检的 HAS_DBACCESS 卡住。
    foreach ($name in @($script:Cfg.DeniedDbs)) {
        if (Test-HasText $Query ([string]$name)) { throw "SQL 碰到了禁止的账套（account.denied）" }
    }
    if (Test-PathText $Query ([string]$script:Cfg.U8Home)) { throw "禁止 U8 安装目录" }
}

function Assert-MasterQuery([string]$Query) {
    Assert-NoProd $Query
    if (Test-HasText $Query ("DROP DATABASE [" + (Get-DataDb) + "]")) { throw "禁止删除业务库" }
    if (Test-HasText $Query ("DROP DATABASE [" + (Get-MetaDb) + "]")) { throw "禁止删除业务库" }
    foreach ($item in @($script:Cfg.Protected)) {
        $top = ([string]$item).TrimEnd("\") + "\"
        if (Test-PathText $Query $top) { throw "禁止 U8 安装目录所在路径" }
    }
    if (-not ((Test-HasText $Query (Get-DataDb)) -or (Test-HasText $Query (Get-MetaDb)))) {
        throw "管理语句没有点名测试账套"
    }
}

function Assert-ConnDb([string]$Database) {
    if ($Database -eq "master") { return }
    # 只读连接可以连本次的两个快照(快照本身只读);写操作仍只走 Assert-AllowedDb。
    if ($Database -ceq (Get-SnapName (Get-DataDb))) { return }
    if ($Database -ceq (Get-SnapName (Get-MetaDb))) { return }
    Assert-AllowedDb $Database
}

function Get-SqlExe {
    if (-not (Test-Path -LiteralPath $script:SqlExe)) { throw "找不到 sqlcmd" }
    # sqlcmd 用 -f 65001 输出 UTF-8；PowerShell 按 [Console]::OutputEncoding 解码外部命令的输出。服务器缺省是 936，
    # 看门狗（计划任务）里不改的话，奇数字节的中文后面紧跟的 CHAR(31) 会被吞掉（U8TaskConfig 行解析失败，还原中止）。
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
    return $script:SqlExe
}

function Get-SqlText([string]$Database, [string]$Query) {
    Assert-ConnDb $Database
    Assert-NoProd $Query
    $exe = Get-SqlExe
    $raw = & $exe -S ([string]$script:Cfg.SqlServer) -E -b -W -h-1 -w 65535 -f 65001 -d $Database -Q $Query 2>&1
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($item in @($raw)) { $lines.Add([string]$item) }
    if ($LASTEXITCODE -ne 0) { throw ("sqlcmd 退出码 " + $LASTEXITCODE + " " + ($lines -join " ")) }
    return $lines
}

function Invoke-Master([string]$Query) {
    Assert-MasterQuery $Query
    $exe = Get-SqlExe
    $raw = & $exe -S ([string]$script:Cfg.SqlServer) -E -b -W -f 65001 -d "master" -Q $Query 2>&1
    if ($LASTEXITCODE -ne 0) {
        $text = (@($raw) | ForEach-Object { [string]$_ }) -join " "
        throw ("sqlcmd 退出码 " + $LASTEXITCODE + " " + $text)
    }
}

function Write-Utf8([string]$Path, [string]$Text) {
    $full = Assert-E2EPath $Path
    $dir = [System.IO.Path]::GetDirectoryName($full)
    [void][System.IO.Directory]::CreateDirectory($dir)
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($full, $Text, $utf8)
}

function Quote-Ident([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name)) { throw "标识符为空" }
    $bad = @("'", ";", "`n", "`r", "`0")
    foreach ($item in $bad) {
        if ($Name.Contains($item)) { throw "标识符含非法字符" }
    }
    return "[" + $Name.Replace("]", "]]") + "]"
}

function Get-SqlServiceSid {
    $name = [string]$script:Cfg.SqlService
    $svc = Get-CimInstance -ClassName Win32_Service -Filter ("Name='" + $name + "'")
    if ($null -eq $svc) { throw ("找不到 SQL Server 服务 " + $name) }
    $account = [string]$svc.StartName
    if ([string]::IsNullOrWhiteSpace($account)) { throw "SQL 服务账号为空" }
    # 服务管理器里的 LocalSystem 不是可解析的账户名,直接对应 SYSTEM。
    if ($account -ieq "LocalSystem") { return "S-1-5-18" }
    $nt = New-Object System.Security.Principal.NTAccount($account)
    return [string]$nt.Translate([System.Security.Principal.SecurityIdentifier]).Value
}

function Grant-SnapModify([string]$Dir, [string]$Sid) {
    $full = Assert-E2EPath $Dir
    if (-not $full.EndsWith("\snap", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "只授权 snap 目录"
    }
    [void][System.IO.Directory]::CreateDirectory($full)
    & icacls.exe $full /grant:r ("*" + $Sid + ":(OI)(CI)M")
    if ($LASTEXITCODE -ne 0) { throw "授权 SQL 服务账号失败" }
}

function Get-SnapDrive {
    return $script:E2ERoot.Substring(0, 2).ToUpperInvariant()
}

function Get-SnapDisk {
    $drive = Get-SnapDrive
    $disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter ("DeviceID='" + $drive + "'")
    if ($null -eq $disk) { throw ("找不到 " + $drive) }
    return $disk
}

function Assert-FreeSpace {
    $disk = Get-SnapDisk
    if ([int64]$disk.FreeSpace -lt [int64]$script:Cfg.MinFreeBytes) { throw ((Get-SnapDrive) + " 剩余空间不足") }
}

function Test-DatabaseName([string]$Name) {
    $query = "SET NOCOUNT ON; SELECT name FROM sys.databases WHERE name = N'" + $Name + "';"
    foreach ($line in @(Get-SqlText "master" $query)) {
        if (([string]$line).Trim() -eq $Name) { return $true }
    }
    return $false
}

function Assert-SnapPresent([string]$Name) {
    if (-not (Test-DatabaseName $Name)) { throw ("快照不在，拒绝执行 " + $Name) }
}

function Assert-DoneAbsent {
    $done = Assert-E2EPath (Get-E2EPath "done")
    if (-not [System.IO.File]::Exists($done)) { return }
    $text = [System.IO.File]::ReadAllText($done).Trim()
    throw ("看门狗已还原，之后的写入未还原 " + $text)
}

function Assert-WatchdogMargin {
    $name = [string]$script:Cfg.RevertTask
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($null -eq $task) { throw "看门狗不在，拒绝执行" }
    $info = Get-ScheduledTaskInfo -TaskName $name
    if ($null -eq $info.NextRunTime) { throw "看门狗已触发，拒绝执行" }
    $left = ($info.NextRunTime - (Get-Date)).TotalMinutes
    if ($left -lt 5) { throw "看门狗剩余不足 5 分钟" }
}

function Assert-LiveWindow {
    # sql / call / deploy 在服务器上确认窗口还开着。不是沙箱。
    Assert-SnapPresent (Get-SnapName (Get-DataDb))
    Assert-SnapPresent (Get-SnapName (Get-MetaDb))
    Assert-DoneAbsent
    Assert-WatchdogMargin
}

function Assert-SqlLine([string]$Line) {
    $trim = $Line.Trim()
    if ($trim -match '^(?i)(!!|:|ed\b|exit\b|quit\b|reset\b)') { throw "拒绝 sqlcmd 指令" }
}

function Assert-SqlWords([string]$Query) {
    $words = '(?i)(\bexec(ute)?\b|\bsp_executesql\b|\bsp_msforeach|\bopenrowset\b|\bopenquery\b|\bopendatasource\b|\bbackup\b|\brestore\b|\buse\b|\b(alter|drop|create)\s+database\b|\bsynonym\b|\bbulk\b|\bdbcc\b|\bxp_|\bshutdown\b|\bkill\b|\bgrant\b|\brevoke\b|\bdeny\b|\b(create|alter|drop)\s+(login|user)\b)'
    if ($Query -match $words) { throw "拒绝危险语句" }
    $part = '(?i)(\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)\s*\.\s*(\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)\s*\.\s*(\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)'
    if ($Query -match $part) { throw "拒绝三段及以上名称" }
}

function Assert-SqlGuard([string]$Query) {
    # 开发者自己用的文本守卫，不是沙箱。
    Assert-NoProd $Query
    if ($Query.Contains("..")) { throw "拒绝跨库引用" }
    if ($Query -match '(?i)UFSystem') { throw "拒绝系统库" }
    if ($Query -match '(?i)(^|[^A-Za-z0-9_])master([^A-Za-z0-9_]|$)') { throw "拒绝 master" }
    foreach ($item in @($script:Cfg.Protected)) {
        $top = ([string]$item).TrimEnd("\") + "\"
        if (Test-PathText $Query $top) { throw "拒绝 U8 安装目录所在路径" }
    }
    if ($Query.Contains('$(')) { throw "拒绝 sqlcmd 变量" }
    Assert-SqlWords $Query
    foreach ($line in $Query.Split("`n")) { Assert-SqlLine $line }
}
