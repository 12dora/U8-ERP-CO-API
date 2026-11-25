#Requires -Version 7.4
#Requires -PSEdition Core
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Get-LogicalNames([string]$Database) {
    Assert-AllowedDb $Database
    $query = "SET NOCOUNT ON; SELECT name FROM sys.master_files WHERE database_id = DB_ID(N'" + $Database + "') AND type = 0 ORDER BY file_id;"
    $names = New-Object System.Collections.Generic.List[string]
    foreach ($line in @(Get-SqlText "master" $query)) {
        $text = ([string]$line).Trim()
        if ($text.Length -gt 0) { $names.Add($text) }
    }
    if ($names.Count -eq 0) { throw "没有数据文件" }
    return $names
}

function New-FileClause([string]$Database, [string]$Logical, [int]$Index) {
    $path = Get-E2EPath ("snap\" + $Database + "_" + $Index + ".ss")
    $full = Assert-E2EPath $path
    if ([System.IO.File]::Exists($full)) { throw "快照文件已存在" }
    return "(NAME = " + (Quote-Ident $Logical) + ", FILENAME = N'" + $full + "')"
}

function Confirm-Snapshot([string]$Database, [string]$Snapshot) {
    $query = "SET NOCOUNT ON; SELECT DB_NAME(source_database_id) FROM sys.databases WHERE name = N'" + $Snapshot + "';"
    $got = ""
    foreach ($line in @(Get-SqlText "master" $query)) {
        $text = ([string]$line).Trim()
        if ($text.Length -gt 0) { $got = $text }
    }
    if ($got -ne $Database) { throw "快照创建后源库不匹配" }
}

function New-Snapshot([string]$Database, [string]$Snapshot) {
    Assert-SnapPair $Database $Snapshot
    $names = @(Get-LogicalNames $Database)
    $parts = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt $names.Count; $i++) {
        $parts.Add((New-FileClause $Database $names[$i] $i))
    }
    $sql = "CREATE DATABASE " + (Quote-Ident $Snapshot) + " ON " + ($parts -join ", ") + " AS SNAPSHOT OF " + (Quote-Ident $Database) + ";"
    Invoke-Master $sql
    Confirm-Snapshot $Database $Snapshot
    Write-Host ("snapshot " + $Snapshot)
}

Assert-FreeSpace
$snap = Assert-E2EPath (Get-E2EPath "snap")
Grant-SnapModify $snap (Get-SqlServiceSid)
New-Snapshot (Get-DataDb) (Get-SnapName (Get-DataDb))
New-Snapshot (Get-MetaDb) (Get-SnapName (Get-MetaDb))
Write-Host "snapshot-create ok"
