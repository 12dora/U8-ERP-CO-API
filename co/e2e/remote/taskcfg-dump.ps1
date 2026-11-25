#Requires -Version 7.4
#Requires -PSEdition Core
# U8TaskConfig 逐行导出。还原批自己也会写这两份；这个脚本留给手工核对。
param(
    [Parameter(Mandatory = $true)][ValidateSet("live", "snapshot")][string]$Source,
    [Parameter(Mandatory = $true)][string]$OutFile
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
. (Join-Path $PSScriptRoot "tasklib.ps1")
$dest = Assert-E2EPath $OutFile
$db = Get-DataDb
if ($Source -eq "snapshot") {
    $db = Get-SnapName (Get-DataDb)
    Assert-SnapPair (Get-DataDb) $db
}
$rows = ConvertTo-TaskRows @(Get-SqlText $db (Get-TaskQuery))
$now = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff")
$doc = [ordered]@{ source = $Source; server_time = $now; rows = $rows }
Write-Utf8 $dest ($doc | ConvertTo-Json -Depth 5)
Write-Host ("taskcfg " + $Source + " rows=" + $rows.Count)
