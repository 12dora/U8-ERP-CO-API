#Requires -Version 7.4
#Requires -PSEdition Core
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Get-BridgeProcs {
    $raw = Get-CimInstance -ClassName Win32_Process -Filter ("Name='" + [string]$script:Cfg.BridgeExe + "'")
    if ($null -eq $raw) { return @() }
    return @($raw)
}

$root = [System.IO.Path]::GetFullPath((Get-E2EPath "\"))
$found = @(Get-BridgeProcs)
foreach ($proc in $found) {
    $path = [string]$proc.ExecutablePath
    if ([string]::IsNullOrWhiteSpace($path)) { continue }
    $full = [System.IO.Path]::GetFullPath($path)
    if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
    Stop-Process -Id $proc.ProcessId -Force
}
Start-Sleep -Seconds 2
$left = @(Get-BridgeProcs)
foreach ($proc in $left) {
    $path = [string]$proc.ExecutablePath
    if ([string]::IsNullOrWhiteSpace($path)) { continue }
    $full = [System.IO.Path]::GetFullPath($path)
    if ($full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "e2e 目录下的桥进程仍在运行"
    }
}
Write-Host "stop-bridge ok"
