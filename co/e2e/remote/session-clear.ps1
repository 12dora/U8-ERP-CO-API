#Requires -Version 7.4
#Requires -PSEdition Core
# 只删除这一个标记文件，不递归。
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$dest = Assert-E2EPath (Get-E2EPath "session.json")
if (-not $dest.EndsWith("\session.json", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "拒绝删除会话标记以外的文件"
}
if ([System.IO.File]::Exists($dest)) { [System.IO.File]::Delete($dest) }
Write-Host "session-clear ok"
