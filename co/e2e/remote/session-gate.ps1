#Requires -Version 7.4
#Requires -PSEdition Core
# call / deploy 在服务器上确认两个快照还在、done 不在、看门狗至少还有 5 分钟。
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
Assert-LiveWindow
Write-Host "session-gate ok"
