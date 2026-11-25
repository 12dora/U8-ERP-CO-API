#Requires -Version 7.4
#Requires -PSEdition Core
param(
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string]$OutFile,
    [ValidateSet("live", "snapshot")][string]$Source = "live"
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
Assert-AllowedDb $Database
$dest = Assert-E2EPath $OutFile
# snapshot:读本次快照(还原的目标时点)。快照名由库名推出,不接受外部传入。
$target = $Database
if ($Source -eq "snapshot") { $target = Get-SnapName $Database; Assert-SnapPair $Database $target }

. (Join-Path $PSScriptRoot "fingerlib.ps1")

$doc = ConvertTo-FingerDoc $Database @(Get-SqlText $target $script:FingerSql)
Write-Utf8 $dest ($doc | ConvertTo-Json -Depth 5)
Write-Host ("fingerprint " + $target + " tables=" + $doc.tables.Count)
