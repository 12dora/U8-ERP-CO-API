#Requires -Version 7.4
#Requires -PSEdition Core
param(
    [Parameter(Mandatory = $true)][ValidateSet("sale", "dispatch")][string]$Kind,
    [Parameter(Mandatory = $true)][int]$Id,
    [Parameter(Mandatory = $true)][string]$OutFile
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
if ($Id -lt 1) { throw "单据 id 无效" }
$dest = Assert-E2EPath $OutFile
$sql = ""
if ($Kind -eq "sale") {
    $sql = "SET NOCOUNT ON; SELECT ISNULL(cVerifier, N'') + CHAR(31) + CASE WHEN dverifydate IS NULL THEN N'' ELSE CONVERT(varchar(19), dverifydate, 120) END FROM dbo.SO_SOMain WHERE ID = $Id;"
} else {
    $sql = "SET NOCOUNT ON; SELECT ISNULL(cVerifier, N'') + CHAR(31) + CASE WHEN dverifydate IS NULL THEN N'' ELSE CONVERT(varchar(19), dverifydate, 120) END FROM dbo.DispatchList WHERE DLID = $Id;"
}
$line = ""
foreach ($item in @(Get-SqlText (Get-DataDb) $sql)) {
    $text = ([string]$item).Trim()
    if ($text.Length -gt 0) { $line = $text }
}
if ($line.Length -eq 0) { throw "单据不存在" }
$bits = $line.Split([char]31)
if ($bits.Count -ne 2) { throw "单据状态无法解析" }
$doc = [ordered]@{
    cVerifier = $bits[0]
    dverifydate = $bits[1]
}
Write-Utf8 $dest ($doc | ConvertTo-Json -Depth 3)
Write-Host "doc-state ok"
