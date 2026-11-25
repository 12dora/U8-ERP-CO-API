#Requires -Version 7.4
#Requires -PSEdition Core
# -Tag 给并行的 session audit 各复制一份（out\<Tag>-audit-<Day>.log）；回归不给，仍写 out\audit-<Day>.log。
param(
    [Parameter(Mandatory = $true)][string]$Day,
    [string]$Tag = ""
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
if ($Day -notmatch '^\d{8}$') { throw "日期不对" }
if (($Tag -ne "") -and ($Tag -cnotmatch '^p\d{1,10}-[0-9a-f]{8}$')) { throw "调用标记不合法" }
$src = [System.IO.Path]::GetFullPath((Get-BridgePath ("logs\u8co-" + $Day + ".log")))
$expect = [System.IO.Path]::GetFullPath((Get-BridgePath "logs\"))
if (-not $src.StartsWith($expect, [System.StringComparison]::OrdinalIgnoreCase)) { throw "审计路径异常" }
$name = "audit-" + $Day + ".log"
if ($Tag -ne "") { $name = $Tag + "-" + $name }
$dest = Assert-E2EPath (Get-E2EPath ("out\" + $name))
if (-not [System.IO.File]::Exists($src)) {
    Write-Utf8 $dest ""
    Write-Host "audit missing"
    exit 0
}
[System.IO.File]::Copy($src, $dest, $true)
Write-Host "read-audit ok"
