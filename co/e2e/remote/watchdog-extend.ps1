#Requires -Version 7.4
#Requires -PSEdition Core
# session extend：把看门狗任务的触发时刻原地改到 -Until（服务器本地时间 yyyy-MM-ddTHH:mm:ss）。
# 只改触发器，不删不重建，任何时刻都有看门狗。会话从打开起最长 240 分钟，这里按服务器会话标记再核对一次。
param(
    [Parameter(Mandatory = $true)][string]$Until
)
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
. (Join-Path $PSScriptRoot "common.ps1")

$MaxMinutes = 240
$Culture = [System.Globalization.CultureInfo]::InvariantCulture

function Read-Until([string]$Text) {
    $value = [datetime]::MinValue
    $style = [System.Globalization.DateTimeStyles]::AssumeLocal
    if (-not [datetime]::TryParseExact($Text, "yyyy-MM-ddTHH:mm:ss", $Culture, $style, [ref]$value)) {
        throw "到点时刻格式不对"
    }
    return $value
}

function Read-OpenedUtc {
    # 用 System.Text.Json 读，ConvertFrom-Json 会把时间串转成本地 DateTime。
    $path = Assert-E2EPath (Get-E2EPath "session.json")
    if (-not [System.IO.File]::Exists($path)) { throw "服务器上没有会话标记，不能延长" }
    $doc = [System.Text.Json.JsonDocument]::Parse([System.IO.File]::ReadAllText($path))
    try {
        $root = $doc.RootElement
        $owner = [System.Text.Json.JsonElement]::new()
        if ((-not $root.TryGetProperty("owner", [ref]$owner)) -or ($owner.ToString() -cne "session")) {
            throw "只能延长 session open 打开的会话"
        }
        $opened = [System.Text.Json.JsonElement]::new()
        if (-not $root.TryGetProperty("opened_at", [ref]$opened)) { throw "会话标记缺少打开时间" }
        $text = $opened.ToString()
    } finally {
        $doc.Dispose()
    }
    $value = [datetime]::MinValue
    $style = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal
    if (-not [datetime]::TryParseExact($text, "yyyy-MM-ddTHH:mm:ssZ", $Culture, $style, [ref]$value)) {
        throw "会话打开时间格式不对"
    }
    return $value
}

function Assert-Extendable([string]$Name, [datetime]$When) {
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -eq $task) { throw "看门狗不在，不能延长" }
    if ([string]$task.State -eq "Running") { throw "看门狗正在还原，不能延长" }
    $info = Get-ScheduledTaskInfo -TaskName $Name
    if ($null -eq $info.NextRunTime) { throw "看门狗已触发，不能延长" }
    if (($info.NextRunTime - (Get-Date)).TotalMinutes -lt 1) { throw "看门狗剩余不足 1 分钟，不能延长" }
    if ($When -le $info.NextRunTime) { throw "新的到点时刻不晚于现在的到点时刻" }
}

$when = Read-Until $Until
$opened = Read-OpenedUtc
if (($when.ToUniversalTime() - $opened).TotalMinutes -gt $MaxMinutes) { throw "会话已到 240 分钟上限，请 session close" }
Assert-SnapPresent (Get-SnapName (Get-DataDb))
Assert-SnapPresent (Get-SnapName (Get-MetaDb))
Assert-DoneAbsent
$name = [string]$script:Cfg.RevertTask
Assert-Extendable $name $when
# 原地换触发器：动作、S4U 主体和最高权限保持登记时的设置。
$trigger = New-ScheduledTaskTrigger -Once -At $when
Set-ScheduledTask -TaskName $name -Trigger $trigger | Out-Null
$after = Get-ScheduledTaskInfo -TaskName $name
if ($null -eq $after.NextRunTime) { throw "改完看门狗没有下次运行时间，请 session status 核对" }
if ([math]::Abs(($after.NextRunTime - $when).TotalSeconds) -gt 60) { throw "改完看门狗的下次运行时间与要求不符，请 session status 核对" }
# 改的过程中看门狗若已先触发，done 会出现：报错，让 Linux 端知道延长没有意义。
Assert-DoneAbsent
Write-Host ("watchdog-extend " + $after.NextRunTime.ToString("o"))
