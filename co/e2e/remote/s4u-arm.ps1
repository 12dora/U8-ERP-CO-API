#Requires -Version 7.4
#Requires -PSEdition Core
# 在任何写库之前确认 S4U 计划任务能用运行 e2e 的 Windows 账户连上测试账套的库。
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
. (Join-Path $PSScriptRoot "common.ps1")

$task = [string]$script:Cfg.ProbeTask
$pwsh = [string]$script:Cfg.Pwsh
if (-not (Test-Path -LiteralPath $pwsh)) { throw "找不到 pwsh 7" }
$script = Assert-E2EPath (Get-E2EPath "s4u-probe.ps1")
$result = Assert-E2EPath (Get-E2EPath "out\s4u-probe.txt")
if ([System.IO.File]::Exists($result)) { [System.IO.File]::Delete($result) }

$action = New-ScheduledTaskAction -Execute $pwsh -Argument ("-NoProfile -NonInteractive -File `"" + $script + "`"")
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(30)
$principal = New-ScheduledTaskPrincipal -UserId (Get-Principal) -LogonType S4U -RunLevel Limited
try {
    Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal | Out-Null
    Start-ScheduledTask -TaskName $task
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        if ([System.IO.File]::Exists($result)) { break }
        Start-Sleep -Seconds 2
    }
    if (-not [System.IO.File]::Exists($result)) { throw "S4U 任务没有写出结果，无法在写库前确认 SQL" }
    $text = [System.IO.File]::ReadAllText($result).Trim()
    if ($text -ne "1") { throw "S4U 读到的不是 SELECT 1" }
    $svcPath = Assert-E2EPath (Get-E2EPath "out\s4u-service.txt")
    if ([System.IO.File]::Exists($svcPath)) {
        Write-Host ("s4u-service " + [System.IO.File]::ReadAllText($svcPath).Trim())
    }
    Write-Host "s4u-arm ok"
} finally {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
}
