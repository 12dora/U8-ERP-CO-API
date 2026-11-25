#Requires -Version 7.4
#Requires -PSEdition Core
param(
    [Parameter(Mandatory = $true)][string]$Path
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$full = Assert-E2EPath $Path
$out = [System.IO.Path]::GetFullPath((Get-E2EPath "out\"))
if (-not $full.StartsWith($out, [System.StringComparison]::OrdinalIgnoreCase)) { throw "只能删除 out 下的文件" }
if (-not [System.IO.File]::Exists($full)) { exit 0 }
[System.IO.File]::Delete($full)
Write-Host "remove-file ok"
