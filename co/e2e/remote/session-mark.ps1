#Requires -Version 7.4
#Requires -PSEdition Core
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$src = Assert-E2EPath (Get-E2EPath "out\session-body.json")
$dest = Assert-E2EPath (Get-E2EPath "session.json")
if (-not [System.IO.File]::Exists($src)) { throw "没有会话内容" }
$text = [System.IO.File]::ReadAllText($src)
if ($text -match '(?i)"password"') { throw "会话标记含口令字段" }
Write-Utf8 $dest $text
Write-Host "session-mark ok"
