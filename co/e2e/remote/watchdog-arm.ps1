#Requires -Version 7.4
#Requires -PSEdition Core
param(
    [Parameter(Mandatory = $true)][int]$Minutes
)
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
. (Join-Path $PSScriptRoot "common.ps1")

if ($Minutes -lt 10) { $Minutes = 10 }
if ($Minutes -gt 120) { $Minutes = 120 }
$task = [string]$script:Cfg.RevertTask
$existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
if ($null -ne $existing) { throw "看门狗任务已存在" }
$done = Assert-E2EPath (Get-E2EPath "done")
if ([System.IO.File]::Exists($done)) { [System.IO.File]::Delete($done) }
$pwsh = [string]$script:Cfg.Pwsh
if (-not (Test-Path -LiteralPath $pwsh)) { throw "找不到 pwsh 7" }
$script = Assert-E2EPath (Get-E2EPath "watchdog-fire.ps1")
$when = (Get-Date).AddMinutes($Minutes)
$action = New-ScheduledTaskAction -Execute $pwsh -Argument ("-NoProfile -NonInteractive -File `"" + $script + "`"")
$trigger = New-ScheduledTaskTrigger -Once -At $when
$principal = New-ScheduledTaskPrincipal -UserId (Get-Principal) -LogonType S4U -RunLevel Highest
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal | Out-Null
Write-Host ("watchdog-arm " + $when.ToString("o"))
