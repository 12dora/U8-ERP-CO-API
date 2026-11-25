#Requires -Version 7.4
#Requires -PSEdition Core
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$out = Assert-E2EPath (Get-E2EPath "out\s4u-probe.txt")
$rows = @(Get-SqlText (Get-DataDb) "SET NOCOUNT ON; SELECT 1;")
$text = ""
foreach ($line in $rows) {
    $item = ([string]$line).Trim()
    if ($item.Length -gt 0) { $text = $item }
}
Write-Utf8 $out $text
try {
    $svc = Get-Service -Name ([string]$script:Cfg.ServiceName) -ErrorAction Stop
    $svcPath = Assert-E2EPath (Get-E2EPath "out\s4u-service.txt")
    Write-Utf8 $svcPath ([string]$svc.Status)
} catch {
    Write-Host ("s4u-service best-effort: " + $_.Exception.Message)
}
Write-Host "s4u-probe ok"
