#Requires -Version 7.4
#Requires -PSEdition Core
# 先写 done，再取消任务。done 存在时看门狗不再还原。
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
. (Join-Path $PSScriptRoot "common.ps1")

$done = Assert-E2EPath (Get-E2EPath "done")
$task = [string]$script:Cfg.RevertTask
Write-Utf8 $done ("done " + (Get-Date).ToString("o"))
Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
$left = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
if ($null -ne $left) { throw "看门狗任务仍在" }
Write-Host "watchdog-disarm ok"
