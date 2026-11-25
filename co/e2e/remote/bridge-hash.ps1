#Requires -Version 7.4
#Requires -PSEdition Core
# 报告本通道已部署的桥是不是某份源码，供 Linux 端决定能否跳过编译部署（bridge_job.deploy_service）。
# 输出四行：binpath ok|bad、src_sha256 <哈希>|none、config ok|bad|skipped、health ok|bad|skipped。
# 不停服务、不换程序、本脚本不写文件；但健康检查请求会让桥在审计日志里追加一行，--check-config 只读已安装的配置。
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
. (Join-Path $PSScriptRoot "bridgelib.ps1")

function Get-BinPathState {
    try {
        if ((Get-ServiceRootProblem) -eq "") { return "ok" }
    } catch {
        return "bad"
    }
    return "bad"
}

function Get-ConfigState([string]$BinPath, [string]$Hash) {
    # 跳过部署时没有套件里的 --check-config，改用已安装的 exe 核对已安装的配置。输出不打印（只看退出码）。
    if (($BinPath -ne "ok") -or ($Hash -eq "")) { return "skipped" }
    try {
        $root = [string]$script:Cfg.BridgeRoot
        $exe = [System.IO.Path]::GetFullPath((Get-BridgePath ("bin\" + [string]$script:Cfg.BridgeExe)))
        Assert-NoReparse $exe
        $null = & $exe --check-config --root $root 2>&1
        if ($LASTEXITCODE -eq 0) { return "ok" }
    } catch {
        return "bad"
    }
    return "bad"
}

function Get-HealthState([string]$BinPath, [string]$Hash, [string]$Config) {
    # 服务登记不对、没有哈希或配置核对没过时反正要部署，不必再等健康检查。
    if (($BinPath -ne "ok") -or ($Hash -eq "") -or ($Config -ne "ok")) { return "skipped" }
    try {
        Test-ServiceHealth 3
        return "ok"
    } catch {
        return "bad"
    }
}

$binPath = Get-BinPathState
$hash = Read-SrcHash
$config = Get-ConfigState $binPath $hash
$health = Get-HealthState $binPath $hash $config
$shown = $hash
if ($shown -eq "") { $shown = "none" }
Write-Host ("binpath " + $binPath)
Write-Host ("src_sha256 " + $shown)
Write-Host ("config " + $config)
Write-Host ("health " + $health)
