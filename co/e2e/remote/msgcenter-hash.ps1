#Requires -Version 7.4
#Requires -PSEdition Core
# 只读 <U8 安装目录>\MessageCenter\MsgCenter.db。不写 U8 安装目录，不哈希其他文件。
# U8 消息中心常驻打开这个 SQLite 文件，Get-FileHash 的独占读会失败；这里以共享读写删除方式只读打开，
# 不拒绝 U8 的任何访问。读不到时仍写出 msgcenter.json（busy=true 加原因），由 Linux 端只记警告。
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Get-MsgCenterPath {
    $expect = Join-Path ([string]$script:Cfg.U8Home) "MessageCenter\MsgCenter.db"
    $full = [System.IO.Path]::GetFullPath($expect)
    if (-not $full.Equals($expect, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝读取名单外的文件"
    }
    if (-not [System.IO.File]::Exists($full)) { throw "找不到 MsgCenter.db" }
    Assert-NoReparse $full
    return $full
}

function Get-SharedSha256([string]$Path) {
    $share = [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha.ComputeHash($stream)
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
    return ([System.BitConverter]::ToString($bytes) -replace "-", "").ToLower()
}

function Get-ErrText($Record) {
    $err = $Record.Exception
    if ($null -ne $err.InnerException) { $err = $err.InnerException }
    return $err.Message
}

function Read-MsgCenterHash([string]$Path) {
    # 共享读遇到 IOException（被占用、字节区间被锁）隔 1 秒重试一次，并记 InUse；
    # 没有权限（UnauthorizedAccessException）不重试。仍失败返回 Hash=$null 和原因，不抛出。
    $res = @{ Hash = $null; Reason = ""; InUse = $false; Denied = $false }
    for ($try = 0; $try -lt 2; $try++) {
        if ($try -gt 0) { Start-Sleep -Seconds 1 }
        try {
            $res.Hash = Get-SharedSha256 $Path
            return $res
        } catch [System.IO.IOException] {
            $res.InUse = $true
            $res.Reason = Get-ErrText $_
        } catch [System.UnauthorizedAccessException] {
            $res.Denied = $true
            $res.Reason = Get-ErrText $_
            return $res
        }
    }
    return $res
}

$full = Get-MsgCenterPath
$read = Read-MsgCenterHash $full
$info = Get-Item -LiteralPath $full
$doc = [ordered]@{
    sha256 = [string]$read.Hash
    size = [int64]$info.Length
    mtime = $info.LastWriteTimeUtc.ToString("o")
    in_use = [bool]$read.InUse
    busy = ($null -eq $read.Hash)
    denied = [bool]$read.Denied
    reason = [string]$read.Reason
}
Write-Utf8 (Get-E2EPath "out\msgcenter.json") ($doc | ConvertTo-Json -Depth 3)
if ($doc.denied) {
    Write-Host ("msgcenter-hash busy: 没有权限读取 MsgCenter.db " + $doc.reason)
} elseif ($doc.busy) {
    Write-Host ("msgcenter-hash busy: MsgCenter.db 正被占用，重试后仍读不到 " + $doc.reason)
} elseif ($doc.in_use) {
    Write-Host "msgcenter-hash ok (in use, shared read)"
} else {
    Write-Host "msgcenter-hash ok"
}
