#Requires -Version 7.4
#Requires -PSEdition Core
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
. (Join-Path $PSScriptRoot "common.ps1")

function Invoke-E2EStep([string]$Name) {
    & (Join-Path $PSScriptRoot $Name)
    if ($LASTEXITCODE -ne 0) { throw ($Name + " 退出码 " + $LASTEXITCODE) }
}

$done = Get-E2EPath "done"
if ([System.IO.File]::Exists($done)) {
    Write-Host "done 已存在，不还原"
    exit 0
}
try {
    Invoke-E2EStep "service-stop.ps1"
} catch {
    Write-Host ("停止服务失败，仍尝试还原: " + $_.Exception.Message)
}
$revertCode = 0
try {
    & (Join-Path $PSScriptRoot "snapshot-revert.ps1") -AllowMissing
    if ($LASTEXITCODE -ne 0) { throw ("snapshot-revert.ps1 退出码 " + $LASTEXITCODE) }
} catch {
    $revertCode = 1
    Write-Host ("还原失败: " + $_.Exception.Message)
}
if ($revertCode -eq 0) {
    try {
        Write-Utf8 $done ("done " + (Get-Date).ToString("o"))
    } catch {
        $revertCode = 1
        Write-Host ("写 done 失败: " + $_.Exception.Message)
    }
}
try {
    Invoke-E2EStep "service-start.ps1"
} catch {
    Write-Host ("启动服务失败: " + $_.Exception.Message)
    if ($revertCode -eq 0) { $revertCode = 1 }
}
exit $revertCode
