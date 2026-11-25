#Requires -Version 7.4
#Requires -PSEdition Core
# 停服务、等旧进程退出、换上套件里的 exe 和 exe.config、再启动。
# 失败时按是否已经覆盖决定恢复上一份，并尽量把服务拉起来。不改 config.json 和密钥。
# -SrcHash：本次套件的源码哈希（kitpack.source_hash）。换程序之前先删掉旧的 bin\src.sha256，健康检查通过后才写新的，
# 中途失败（含恢复上一份）时服务器上没有哈希文件，下一次一定重新编译部署。
param(
    [string]$SrcHash = ""
)
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
. (Join-Path $PSScriptRoot "common.ps1")
. (Join-Path $PSScriptRoot "bridgelib.ps1")
if (($SrcHash -ne "") -and -not (Test-SrcHashText $SrcHash)) { throw "源码哈希不合法" }
$script:Replacing = $false
$script:Exe = [string]$script:Cfg.BridgeExe

function Get-BinPath([string]$Name) {
    return (Get-BridgePath ("bin\" + $Name))
}

function Test-BridgeAllowed([string]$Full) {
    # 只允许桥运行目录 bin 下这五个文件名。运行目录由 e2e.json 的 BridgeRoot 给出，不能落在 U8 安装目录或其所在盘。
    if (Test-Protected $Full) { return $false }
    $names = @($script:Exe, ($script:Exe + ".prev"), ($script:Exe + ".config"), ($script:Exe + ".config.prev"), $script:SrcHashName)
    foreach ($name in $names) {
        $want = [System.IO.Path]::GetFullPath((Get-BinPath $name))
        if ($Full.Equals($want, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Assert-BridgeFile([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-BridgeAllowed $full)) { throw "拒绝覆盖名单以外的文件" }
    $bin = [System.IO.Path]::GetDirectoryName($full)
    if ([System.IO.Directory]::Exists($bin)) { Assert-NoReparse $bin }
    if ([System.IO.File]::Exists($full)) { Assert-NoReparse $full }
    return $full
}

# 本通道桥进程的查找、等待和结束（Save-ServicePid、Test-BridgeAlive、Stop-BridgeLeftover）在 bridgelib.ps1：
# 只认路径正好是本运行目录 exe 的进程，或停服务之前记下的本服务进程号，不碰别的调试通道的同名 exe。

function Save-Previous([string]$Name, [string]$Backup) {
    $dest = Assert-BridgeFile (Get-BinPath $Name)
    if (-not [System.IO.File]::Exists($dest)) { return }
    $prev = Assert-BridgeFile (Get-BinPath $Backup)
    [System.IO.File]::Copy($dest, $prev, $true)
}

function Copy-Kit([string]$Source, [string]$Name, [string]$Missing) {
    $src = Assert-E2EPath (Get-E2EPath ("kit\bridge\" + $Source))
    if (-not [System.IO.File]::Exists($src)) { throw $Missing }
    $dest = Assert-BridgeFile (Get-BinPath $Name)
    [System.IO.File]::Copy($src, $dest, $true)
}

function Copy-KitExe {
    Copy-Kit ("out\" + $script:Exe) $script:Exe ("套件里没有 " + $script:Exe)
}

function Copy-KitConfig {
    Copy-Kit ($script:Exe + ".config") ($script:Exe + ".config") ("套件里没有 " + $script:Exe + ".config")
}

# 健康检查（Get-AuditMark、Test-AuditGrew、Test-ServiceHealth）在 bridgelib.ps1。

function Remove-SrcHash {
    $path = Assert-BridgeFile (Get-BinPath $script:SrcHashName)
    if ([System.IO.File]::Exists($path)) { [System.IO.File]::Delete($path) }
}

function Write-SrcHash {
    if ($SrcHash -eq "") { return }
    $path = Assert-BridgeFile (Get-BinPath $script:SrcHashName)
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($path, (Format-SrcHashDoc $SrcHash), $utf8)
}

function Start-Installed {
    # 同一路径的旧进程还活着时不启动：新旧两个进程会同时持有 COM 和端口，exe 也换不下来。
    $svc = Get-Service -Name ([string]$script:Cfg.ServiceName)
    if (([string]$svc.Status -ne "Running") -and (Test-BridgeAlive)) {
        throw ("旧桥进程仍在运行（" + (Get-BridgeExePath) + "），不启动服务，请在服务器上人工结束后再部署")
    }
    & (Join-Path $PSScriptRoot "service-start.ps1")
    if ($LASTEXITCODE -ne 0) { throw "启动服务失败" }
}

function Restore-One([string]$From, [string]$To, [string]$Note) {
    try {
        $prev = Assert-BridgeFile (Get-BinPath $From)
        $dest = Assert-BridgeFile (Get-BinPath $To)
        if ([System.IO.File]::Exists($prev)) {
            [System.IO.File]::Copy($prev, $dest, $true)
            Write-Host $Note
        }
    } catch {
        Write-Host ("恢复失败: " + $_.Exception.Message)
    }
}

function Restore-Previous {
    Restore-One ($script:Exe + ".prev") $script:Exe ("已恢复上一份 " + $script:Exe)
    Restore-One ($script:Exe + ".config.prev") ($script:Exe + ".config") ("已恢复上一份 " + $script:Exe + ".config")
}

function Repair-Bridge {
    if ($script:Replacing) { Restore-Previous }
    try {
        Start-Installed
    } catch {
        Write-Host ("失败后启动服务失败: " + $_.Exception.Message)
    }
}

# 停服务之前先确认服务 binPath 正是本通道的桥（common.ps1 Assert-ServiceRoot：程序路径和 --root 都对上）。
# 旧版本登记的服务 binPath 不带 --root，换上新 exe 后会去缺省目录找 config.json，起不来，也在这里拒绝。

function Update-InstalledBridge {
    Assert-ServiceRoot
    Save-ServicePid
    try {
        & (Join-Path $PSScriptRoot "service-stop.ps1")
        if ($LASTEXITCODE -ne 0) { throw "停止服务失败" }
        # service-stop.ps1 已确认进程退出；这里再兜一次：60 秒不退就按路径和进程号结束，确认退出后才换 exe。
        [void](Stop-BridgeLeftover 60)
        Remove-SrcHash
        Save-Previous $script:Exe ($script:Exe + ".prev")
        Save-Previous ($script:Exe + ".config") ($script:Exe + ".config.prev")
        $script:Replacing = $true
        Copy-KitExe
        Copy-KitConfig
        Start-Installed
        Test-ServiceHealth
    } catch {
        Repair-Bridge
        throw
    }
    # 新程序已经健康。哈希写不进去不回退程序，只是下一次照常编译部署。
    try {
        Write-SrcHash
    } catch {
        Write-Host ("写源码哈希失败，下次照常编译部署: " + $_.Exception.Message)
    }
}

Update-InstalledBridge
Write-Host "service-redeploy ok"
