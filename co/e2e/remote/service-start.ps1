#Requires -Version 7.4
#Requires -PSEdition Core
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
. (Join-Path $PSScriptRoot "common.ps1")
$name = [string]$script:Cfg.ServiceName
$svc = Get-Service -Name $name -ErrorAction SilentlyContinue
if ($null -eq $svc) { throw ("服务 " + $name + " 不存在") }
# 只启动本通道的桥（见 common.ps1 Assert-ServiceRoot）。
Assert-ServiceRoot
if ([string]$svc.Status -ne "Running") { Start-Service -Name $name }
(Get-Service -Name $name).WaitForStatus("Running", (New-TimeSpan -Seconds 30))
Write-Host "service-start ok"
exit 0
