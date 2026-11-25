#Requires -Version 7.4
#Requires -PSEdition Core
param(
    [Parameter(Mandatory = $true)][string]$Since,
    [Parameter(Mandatory = $true)][string]$Operator
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
if ($Since -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$') { throw "时间格式不对" }
if ($Operator -notmatch '^[A-Za-z0-9_-]{1,20}$') { throw "操作员编码不合法" }

function Read-One([string]$Query) {
    foreach ($line in @(Get-SqlText "master" $Query)) {
        $text = ([string]$line).Trim()
        if ($text.Length -gt 0) { return $text }
    }
    return ""
}

$access = Read-One "SET NOCOUNT ON; SELECT CONVERT(varchar(10), ISNULL(HAS_DBACCESS(N'UFSystem'), 0));"
$ufdata = ""
$ufQuery = "SET NOCOUNT ON; SELECT CONVERT(varchar(20), COUNT_BIG(*)) FROM dbo.UA_Log_Ex WHERE cUser_Id = N'" + $Operator + "' AND dInTime >= '" + $Since + "';"
try {
    foreach ($line in @(Get-SqlText (Get-DataDb) $ufQuery)) {
        $text = ([string]$line).Trim()
        if ($text.Length -gt 0) { $ufdata = $text }
    }
} catch {
    $ufdata = "error"
}
$doc = [ordered]@{
    ufsystem_access = ($access -eq "1")
    ufdata_log_rows = $ufdata
}
Write-Utf8 (Get-E2EPath "out\login-count.json") ($doc | ConvertTo-Json -Depth 3)
Write-Host "login-count ok"
