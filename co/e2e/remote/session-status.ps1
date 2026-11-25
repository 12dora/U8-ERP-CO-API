#Requires -Version 7.4
#Requires -PSEdition Core
# -Tag 给并行的 session 命令各写一份状态文件（out\<Tag>-session-status.json），互不覆盖；不给就写 out\session-status.json。
param(
    [string]$Tag = ""
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
if (($Tag -ne "") -and ($Tag -cnotmatch '^p\d{1,10}-[0-9a-f]{8}$')) { throw "调用标记不合法" }

function Read-SnapNames {
    $query = @"
SET NOCOUNT ON;
SELECT name FROM sys.databases
WHERE name IN (N'$(Get-SnapName (Get-DataDb))', N'$(Get-SnapName (Get-MetaDb))');
"@
    $names = New-Object System.Collections.Generic.List[string]
    foreach ($line in @(Get-SqlText "master" $query)) {
        $text = ([string]$line).Trim()
        if ($text.Length -gt 0) { $names.Add($text) }
    }
    return $names
}

function Read-MarkerFields {
    $path = Get-E2EPath "session.json"
    $found = [System.IO.File]::Exists($path)
    $report = ""
    $opened = ""
    $owner = ""
    $minutes = 0
    if ($found) {
        try {
            $doc = Get-Content -LiteralPath $path -Encoding UTF8 -Raw | ConvertFrom-Json
            $report = [string]$doc.report_dir
            $opened = [string]$doc.opened_at
            $owner = [string]$doc.owner
            if ($null -ne $doc.watchdog_min) { $minutes = [int]$doc.watchdog_min }
        } catch {
            $report = ""
        }
    }
    return [ordered]@{
        marker = $found
        report_dir = $report
        opened_at = $opened
        watchdog_min = $minutes
        owner = $owner
    }
}

function Read-NextRun($Info) {
    if ($null -eq $Info) { return "none" }
    if ($null -eq $Info.NextRunTime) { return "fired" }
    return $Info.NextRunTime.ToString("o")
}

function Test-DatabasesBusy {
    $query = @"
SET NOCOUNT ON;
SELECT name FROM sys.databases
WHERE name IN (N'$(Get-DataDb)', N'$(Get-MetaDb)')
  AND user_access_desc <> N'MULTI_USER';
"@
    foreach ($line in @(Get-SqlText "master" $query)) {
        if (([string]$line).Trim().Length -gt 0) { return $true }
    }
    return $false
}

$svc = Get-Service -Name ([string]$script:Cfg.ServiceName) -ErrorAction SilentlyContinue
$service = ""
if ($null -ne $svc) { $service = [string]$svc.Status }
$taskName = [string]$script:Cfg.RevertTask
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$state = "none"
$next = "none"
if ($null -ne $task) {
    $state = [string]$task.State
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    $next = Read-NextRun $info
}
$marker = Read-MarkerFields
$doc = [ordered]@{
    marker = $marker.marker
    report_dir = $marker.report_dir
    opened_at = $marker.opened_at
    watchdog_min = $marker.watchdog_min
    owner = $marker.owner
    snapshots = @(Read-SnapNames)
    done = [System.IO.File]::Exists((Get-E2EPath "done"))
    service = $service
    watchdog_state = $state
    next_run = $next
    databases_busy = (Test-DatabasesBusy)
    config = [ordered]@{
        account = [string]$script:Cfg.Account
        data_db = (Get-DataDb)
        meta_db = (Get-MetaDb)
        snap_suffix = [string]$script:Cfg.SnapSuffix
        revert_task = $taskName
    }
}
$name = "session-status.json"
if ($Tag -ne "") { $name = $Tag + "-" + $name }
Write-Utf8 (Get-E2EPath ("out\" + $name)) ($doc | ConvertTo-Json -Depth 4)
# 告诉 Linux 端这是认得 -Tag 的新版脚本（旧版会把 -Tag 当多余参数吞掉，照旧写不带标记的文件）。
if ($Tag -ne "") { Write-Host ("tag " + $Tag) }
Write-Host "session-status ok"
