#Requires -Version 7.4
#Requires -PSEdition Core
# 只删除这两个还原指纹。不用通配符，不递归。
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Remove-NamedOut([string]$Name) {
    $allowed = @(("fp-inrevert-" + (Get-DataDb) + ".json"), ("fp-inrevert-" + (Get-MetaDb) + ".json"))
    if ($allowed -cnotcontains $Name) {
        throw "拒绝删除名单外的指纹"
    }
    $full = Assert-E2EPath (Get-E2EPath ("out\" + $Name))
    $out = [System.IO.Path]::GetFullPath((Get-E2EPath "out\"))
    if (-not $full.StartsWith($out, [System.StringComparison]::OrdinalIgnoreCase)) { throw "指纹不在 out 下" }
    if ([System.IO.File]::Exists($full)) { [System.IO.File]::Delete($full) }
}

Remove-NamedOut ("fp-inrevert-" + (Get-DataDb) + ".json")
Remove-NamedOut ("fp-inrevert-" + (Get-MetaDb) + ".json")
Write-Host "clear-inrevert ok"
