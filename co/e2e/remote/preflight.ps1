#Requires -Version 7.4
#Requires -PSEdition Core
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Test-AuditReadable {
    $dir = Get-BridgePath "logs"
    if (-not [System.IO.Directory]::Exists($dir)) { return $false }
    $name = "u8co-" + (Get-Date).ToString("yyyyMMdd") + ".log"
    $path = [System.IO.Path]::GetFullPath((Join-Path $dir $name))
    $root = [System.IO.Path]::GetFullPath($dir + "\")
    if (-not $path.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    try {
        if ([System.IO.File]::Exists($path)) {
            $mode = [System.IO.FileMode]::Open
            $access = [System.IO.FileAccess]::Read
            $share = [System.IO.FileShare]::ReadWrite
            $stream = [System.IO.File]::Open($path, $mode, $access, $share)
            $stream.Dispose()
            return $true
        }
        [void][System.IO.Directory]::GetFiles($dir)
        return $true
    } catch {
        return $false
    }
}

function Get-DbList {
    return ("N'" + (Get-DataDb) + "', N'" + (Get-MetaDb) + "'")
}

function Read-DbRows {
    $query = @"
SET NOCOUNT ON;
SELECT name COLLATE DATABASE_DEFAULT + CHAR(31) + state_desc COLLATE DATABASE_DEFAULT + CHAR(31) + user_access_desc COLLATE DATABASE_DEFAULT + CHAR(31) + ISNULL(SUSER_SNAME(owner_sid), N'') COLLATE DATABASE_DEFAULT
FROM sys.databases
WHERE name IN ($(Get-DbList));
"@
    return @(Get-SqlText "master" $query)
}

function Read-SnapRows {
    $query = @"
SET NOCOUNT ON;
SELECT name FROM sys.databases
WHERE name IN (N'$(Get-SnapName (Get-DataDb))', N'$(Get-SnapName (Get-MetaDb))')
   OR source_database_id IN (DB_ID(N'$(Get-DataDb)'), DB_ID(N'$(Get-MetaDb)'));
"@
    return @(Get-SqlText "master" $query)
}

function Read-AccountList([string]$Key) {
    $path = Get-BridgePath "config.json"
    $names = New-Object System.Collections.Generic.List[string]
    if (-not [System.IO.File]::Exists($path)) { return $names }
    $doc = Get-Content -LiteralPath $path -Encoding UTF8 -Raw | ConvertFrom-Json
    foreach ($item in @($doc.$Key)) {
        if ($null -ne $item -and ([string]$item).Length -gt 0) { $names.Add([string]$item) }
    }
    return $names
}

function Read-AllowedAccounts { return (Read-AccountList "allowedAccounts") }

function Read-ListenPort {
    # 桥 config.json 的 listenPrefix 里的端口；与 e2e.json 的 BridgePort 不一致时，预检会拒绝（可能在访问别的通道的桥）。
    $path = Get-BridgePath "config.json"
    if (-not [System.IO.File]::Exists($path)) { return 0 }
    $doc = Get-Content -LiteralPath $path -Encoding UTF8 -Raw | ConvertFrom-Json
    $prefix = [string]$doc.listenPrefix
    if ($prefix -match '^https?://[^/:]+:(\d{1,5})/') { return [int]$Matches[1] }
    return 0
}

function Read-ForeignAccess {
    $query = @"
SET NOCOUNT ON;
SELECT name FROM sys.databases
WHERE state_desc = N'ONLINE'
  AND HAS_DBACCESS(name) = 1
  AND name NOT IN ($(Get-DbList))
  AND (
       name LIKE N'UFDATA[_]%'
    OR name LIKE N'UFMeta[_]%'
    OR name LIKE N'UFSystem%'
    OR name LIKE N'UFModel%'
  );
"@
    $names = New-Object System.Collections.Generic.List[string]
    foreach ($line in @(Get-SqlText "master" $query)) {
        $text = ([string]$line).Trim()
        if ($text.Length -gt 0) { $names.Add($text) }
    }
    return $names
}

# 第三列：能否执行 master.dbo.u8co_release_db（scripts/lane/install-release-proc.sql）。过程不在或看不见时 HAS_PERMS_BY_NAME 为 NULL。
function Read-Rights {
    $query = @"
SET NOCOUNT ON;
SELECT CONVERT(varchar(10), ISNULL(IS_SRVROLEMEMBER('sysadmin'), -1))
  + CHAR(31)
  + CONVERT(varchar(10), ISNULL(HAS_PERMS_BY_NAME(NULL, NULL, 'CREATE ANY DATABASE'), -1))
  + CHAR(31)
  + CONVERT(varchar(10), ISNULL(HAS_PERMS_BY_NAME(N'master.dbo.u8co_release_db', N'OBJECT', N'EXECUTE'), -1));
"@
    return @(Get-SqlText "master" $query)
}

function Convert-DbLine([string]$Line) {
    $bits = $Line.Trim().Split([char]31)
    if ($bits.Count -ne 4) { throw "库状态行无法解析" }
    return [ordered]@{
        name = $bits[0]
        state = $bits[1]
        access = $bits[2]
        owner = $bits[3]
    }
}

$dbs = New-Object System.Collections.Generic.List[object]
foreach ($line in Read-DbRows) {
    $text = ([string]$line).Trim()
    if ($text.Length -eq 0) { continue }
    $dbs.Add((Convert-DbLine $text))
}
$snaps = New-Object System.Collections.Generic.List[string]
foreach ($line in Read-SnapRows) {
    $text = ([string]$line).Trim()
    if ($text.Length -gt 0) { $snaps.Add($text) }
}
$rights = ""
foreach ($line in Read-Rights) {
    $text = ([string]$line).Trim()
    if ($text.Contains([char]31)) { $rights = $text }
}
$parts = $rights.Split([char]31)
$isAdmin = $true
$createAny = $false
$releaseProc = $false
if ($parts.Count -ge 1 -and $parts[0] -eq "0") { $isAdmin = $false }
if ($parts.Count -ge 2 -and $parts[1] -eq "1") { $createAny = $true }
if ($parts.Count -ge 3 -and $parts[2] -eq "1") { $releaseProc = $true }
$disk = Get-SnapDisk
$svc = Get-Service -Name ([string]$script:Cfg.ServiceName) -ErrorAction SilentlyContinue
$serviceExists = $false
$serviceRunning = $false
if ($null -ne $svc) { $serviceExists = $true }
$serviceRoot = ""
if ($serviceExists) { $serviceRoot = Get-ServiceRootProblem }
if ($null -ne $svc -and [string]$svc.Status -eq "Running") { $serviceRunning = $true }
$task = Get-ScheduledTask -TaskName ([string]$script:Cfg.RevertTask) -ErrorAction SilentlyContinue
$watchdog = $false
if ($null -ne $task) { $watchdog = $true }
$probe = Get-ScheduledTask -TaskName ([string]$script:Cfg.ProbeTask) -ErrorAction SilentlyContinue
$s4uTask = $false
if ($null -ne $probe) { $s4uTask = $true }
$portBusy = $false
try {
    $listen = @(Get-NetTCPConnection -LocalPort ([int]$script:Cfg.BridgePort) -State Listen -ErrorAction Stop)
    if ($listen.Count -gt 0) { $portBusy = $true }
} catch {
    $portBusy = $false
}
$doc = [ordered]@{
    databases = $dbs
    snapshots = $snaps
    is_sysadmin = $isAdmin
    create_any_database = $createAny
    release_proc = $releaseProc
    free_bytes = [int64]$disk.FreeSpace
    volume = (Get-SnapDrive)
    watchdog_task = $watchdog
    s4u_task = $s4uTask
    bridge_config_exists = [System.IO.File]::Exists((Get-BridgePath "config.json"))
    bridge_secret_exists = [System.IO.File]::Exists((Get-BridgePath "secret.hex"))
    service_exists = $serviceExists
    service_running = $serviceRunning
    port_busy = $portBusy
    service_root_problem = $serviceRoot
    listen_port = (Read-ListenPort)
    audit_log_readable = (Test-AuditReadable)
    session_marker = [System.IO.File]::Exists((Get-E2EPath "session.json"))
    expected_owner = (Get-Principal)
    sqlcmd = [System.IO.File]::Exists($script:SqlExe)
    allowed_accounts = @(Read-AllowedAccounts)
    read_only_accounts = @(Read-AccountList "readOnlyAccounts")
    foreign_db_access = @(Read-ForeignAccess)
}
$out = Get-E2EPath "out\preflight.json"
Write-Utf8 $out ($doc | ConvertTo-Json -Depth 6)
Write-Host "preflight ok"
