#Requires -Version 7.4
#Requires -PSEdition Core
# 一次回归或一次 session 命令里的长驻 SQL。stdin 关闭后退出，不留在服务器上。
# 门禁最多缓存 30 秒，与 sqlchan.server_should_check 同一条规则。
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
. (Join-Path $PSScriptRoot "common.ps1")

$script:Utf8 = New-Object System.Text.UTF8Encoding $false
$script:GateAt = [datetime]::MinValue
# 同一会话里可以并行跑多条 session sql，每条各起一个工人：查询文件名带本进程号和随机数，用完即删。
$script:QueryFile = Get-E2EPath ("out\worker-query-" + $PID + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8) + ".sql")
[Console]::InputEncoding = $script:Utf8
[Console]::OutputEncoding = $script:Utf8
$OutputEncoding = $script:Utf8

function Write-JsonLine($Payload) {
    $json = $Payload | ConvertTo-Json -Compress -Depth 4 -EscapeHandling EscapeNonAscii
    $flat = $json -replace "[\r\n]", ""
    [Console]::Out.WriteLine($flat)
    [Console]::Out.Flush()
}

function Test-GateFresh {
    if ($script:GateAt -eq [datetime]::MinValue) { return $false }
    return (((Get-Date) - $script:GateAt).TotalSeconds -lt 30)
}

function Update-WorkerGate([bool]$Force) {
    if ((-not $Force) -and (Test-GateFresh)) { return }
    Assert-LiveWindow
    $script:GateAt = Get-Date
}

function Format-SqlOut($Raw) {
    if ($null -eq $Raw) { return "" }
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($item in @($Raw)) {
        if ($null -ne $item) { $lines.Add([string]$item) }
    }
    return ($lines -join "`n")
}

function Invoke-WorkerSql([string]$Database, [string]$Query) {
    Assert-AllowedDb $Database
    if ([string]::IsNullOrWhiteSpace($Query)) { throw "SQL 为空" }
    Assert-SqlGuard $Query
    $queryPath = Assert-E2EPath $script:QueryFile
    Write-Utf8 $queryPath $Query
    try {
        $exe = Get-SqlExe
        $raw = & $exe -S ([string]$script:Cfg.SqlServer) -E -b -W -s "`t" -w 65535 -f 65001 -x -X1 -d $Database -i $queryPath 2>&1
        $code = 0
        if ($null -ne $LASTEXITCODE) { $code = [int]$LASTEXITCODE }
        return @{ code = $code; out = (Format-SqlOut $raw) }
    } finally {
        Remove-QueryFile
    }
}

function Remove-QueryFile {
    # 只删本工人自己的这一个文件，不枚举、不递归。
    $full = Assert-E2EPath $script:QueryFile
    if ([System.IO.File]::Exists($full)) { [System.IO.File]::Delete($full) }
}

function Convert-Request([string]$Line) {
    $req = $Line | ConvertFrom-Json
    $force = $false
    if ($req.recheck -eq $true) { $force = $true }
    return @{ db = [string]$req.db; sql = [string]$req.sql; force = $force }
}

function Send-Reply([bool]$Ok, [int]$Code, [string]$Out) {
    Write-JsonLine @{ ok = $Ok; code = $Code; out = $Out }
}

function Invoke-Request([string]$Line) {
    $req = Convert-Request $Line
    Update-WorkerGate ([bool]$req.force)
    $ran = Invoke-WorkerSql ([string]$req.db) ([string]$req.sql)
    if ([int]$ran.code -ne 0) {
        Send-Reply $false ([int]$ran.code) ([string]$ran.out)
        return
    }
    Send-Reply $true 0 ([string]$ran.out)
}

function Read-WorkerLoop {
    while ($true) {
        $line = [Console]::In.ReadLine()
        if ($null -eq $line) { break }
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            Invoke-Request $line
        } catch {
            Send-Reply $false 1 ([string]$_.ToString())
        }
    }
}

Read-WorkerLoop
