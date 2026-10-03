#Requires -Version 7.4
#Requires -PSEdition Core
# 停并删除桥服务、程序、urlacl 和防火墙规则。config.json、secret.hex、sql.json 和日志保留。
# -Root、-ServiceName 要与安装时相同。
[CmdletBinding()]
param(
    # 运行目录。缺省 %ProgramData%\U8Co\u8co。
    [string]$Root = "",
    # 服务名；防火墙规则同名（分组 U8Co）。已有同名服务必须是本运行目录的桥，否则拒绝删除。
    [string]$ServiceName = "u8co",
    # 只在运行目录里没有 config.json 时使用，用来拼出要删的 urlacl：http://+:<Port>/u8co/。
    [ValidateRange(1, 65535)][int]$Port = 18089,
    # U8 安装目录，与安装时相同；已有 config.json 时以其中的 u8Home 为准。脚本拒绝在这里（以及它所在的非系统盘）写文件。
    [string]$U8Home = "C:\U8SOFT"
)
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
$PSNativeCommandUseErrorActionPreference = $false
$here = $PSScriptRoot
Set-Location -LiteralPath $here
. (Join-Path (Split-Path -Parent $here) "SafePath.ps1")
Initialize-BridgeStaging $here $Root $U8Home $PSBoundParameters.ContainsKey("U8Home")
$RuntimeDir = $script:RuntimeRoot
# 安装脚本建的防火墙规则都带这个分组；只删名字和分组都对上的规则。
$FirewallGroup = "U8Co"

function Assert-Pwsh64 {
    if ([Environment]::Is64BitProcess) { return }
    throw "请用 64 位 pwsh：C:\Program Files\PowerShell\7\pwsh.exe"
}

function Test-Elevated {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-RuntimePath([string]$Name) {
    return (Assert-UnderRuntime (Join-Path $RuntimeDir $Name))
}

function Assert-ServiceName {
    if ($ServiceName -cmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') { return }
    throw "-ServiceName 只能含字母、数字、点、下划线、连字符，字母或数字开头，最长 64"
}

function Read-Prefix {
    $fallback = "http://+:" + $Port + "/u8co/"
    $path = Get-RuntimePath "config.json"
    if (-not [System.IO.File]::Exists($path)) { return $fallback }
    $cfg = Get-Content -LiteralPath $path -Encoding UTF8 -Raw | ConvertFrom-Json
    $prefix = [string]$cfg.listenPrefix
    if ([string]::IsNullOrWhiteSpace($prefix)) { return $fallback }
    return $prefix
}

function Test-UrlAcl([string]$Prefix) {
    $text = (& netsh.exe http show urlacl | Out-String)
    return $text.Contains($Prefix, [System.StringComparison]::Ordinal)
}

function Remove-UrlAcl([string]$Prefix) {
    if (-not (Test-UrlAcl $Prefix)) {
        Write-Host "urlacl 不存在"
        return
    }
    & netsh.exe http delete urlacl $("url=" + $Prefix)
    if ($LASTEXITCODE -ne 0) { throw "urlacl 删除失败" }
    Write-Host "已删除 urlacl"
}

# 按规则名（-Name）找，分组必须是 U8Co。旧版本建的规则没有分组，只在同名服务确认是本桥时才删。
function Remove-NamedFirewall([string]$Name, [bool]$OwnService) {
    $rule = @(Get-NetFirewallRule -Name $Name -ErrorAction SilentlyContinue)
    if ($rule.Count -eq 0) {
        Write-Host ("防火墙规则不存在 " + $Name)
        return
    }
    $legacy = [string]::IsNullOrEmpty($rule[0].Group) -and $OwnService
    if ($rule[0].Group -ne $FirewallGroup -and -not $legacy) {
        Write-Host ("警告: 防火墙规则 " + $Name + " 的分组不是 " + $FirewallGroup + "，不是安装脚本建的，未删除")
        return
    }
    $rule[0] | Remove-NetFirewallRule
    Write-Host ("已删除防火墙规则 " + $Name)
}

# 同名服务必须是这个运行目录的桥（Assert-BridgeService），否则抛错，不停也不删。
function Remove-BridgeService {
    if (-not (Assert-BridgeService $ServiceName)) {
        Write-Host "服务不存在"
        return
    }
    $svc = Get-Service -Name $ServiceName
    if ($svc.Status -ne "Stopped") {
        Stop-Service -Name $ServiceName -Force
        $svc.WaitForStatus("Stopped", (New-TimeSpan -Seconds 30))
    }
    & sc.exe delete $ServiceName
    if ($LASTEXITCODE -ne 0) { throw "sc delete 失败" }
    Write-Host "已删除服务"
}

function Remove-BridgeFile([string]$Name) {
    $path = Get-RuntimePath ("bin\" + $Name)
    if ([System.IO.File]::Exists($path)) {
        Remove-Item -LiteralPath $path -Force
        Write-Host ("已删除 " + $Name)
        return
    }
    Write-Host ($Name + " 不存在")
}

function Write-Kept {
    Write-Host ("保留 " + $RuntimeDir + "（config.json、secret.hex、sql.json 和 logs）")
    Write-Host "确认不再使用并已拷走审计后，只能手工删除这个目录："
    Write-Host ('  $root = [System.IO.Path]::GetFullPath("' + $RuntimeDir + '")')
    Write-Host ('  if ($root -ne "' + $RuntimeDir + '") { throw "拒绝删除" }')
    Write-Host "  Remove-Item -LiteralPath `$root -Recurse -Force"
}

function Open-UninstallLog {
    $dir = Assert-UnderStaging (Join-Path $here "results")
    [void][System.IO.Directory]::CreateDirectory($dir)
    $log = Assert-UnderStaging (Join-Path $dir ("u8co-uninstall-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log"))
    Start-Transcript -LiteralPath $log | Out-Null
}

if (-not (Test-Elevated)) {
    Write-Host "请以管理员身份运行。"
    exit 1
}
Assert-Pwsh64
Assert-ServiceName
Open-UninstallLog
$failed = $false
try {
    $prefix = Read-Prefix
    $ownService = Assert-BridgeService $ServiceName
    Remove-BridgeService
    Remove-BridgeFile "u8co-bridge.exe"
    Remove-BridgeFile "u8co-bridge.exe.config"
    Remove-UrlAcl $prefix
    Remove-NamedFirewall $ServiceName $ownService
    Write-Kept
    Write-Host "完成,请通知管理员"
} catch {
    Write-Host ("错误: " + $_.Exception.Message)
    $failed = $true
} finally {
    Stop-Transcript | Out-Null
}
if ($failed) { exit 1 }
