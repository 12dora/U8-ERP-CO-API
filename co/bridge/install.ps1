#Requires -Version 7.4
#Requires -PSEdition Core
# 安装或更新桥服务。以管理员身份在 U8 应用服务器上运行，先运行 build.ps1。
# 站点相关的值都从参数来，脚本里没有内置的服务器地址、账套或来源 IP。
# 首次安装（运行目录里还没有 config.json）必须给 -U8Server 和 -AllowedClients。
# -AllowedAccounts 不给就是空名单：服务能启动，但所有账套的请求都会被拒绝。
# config.json 已存在时不覆盖，写配置用的参数不生效（会逐个提示）。要改配置就直接编辑 config.json，再重跑本脚本。
[CmdletBinding()]
param(
    # 运行目录：程序、config.json、secret.hex、日志。缺省 %ProgramData%\U8Co\u8co。
    [string]$Root = "",
    # 服务名。防火墙规则同名（分组 U8Co），显示名为「U8 CO Bridge (服务名)」。已有同名服务必须是本运行目录的桥。
    [string]$ServiceName = "u8co",
    # U8 应用服务器（U8 登录时填的服务器名或 IP）。首次安装必填。
    [string]$U8Server = "",
    # 允许调用桥的来源 IP，逗号分隔。首次安装必填；同时用作防火墙规则的远程地址。
    [string[]]$AllowedClients = @(),
    # 允许登录的账套号（3 位数字），逗号分隔。缺省为空，拒绝所有账套。
    [string[]]$AllowedAccounts = @(),
    # 测试账套（3 位数字），逗号分隔，写进 testAccounts。缺省为空。只写测试账套。
    # 第二级写入（月末结账、存货核算、期初等）要同时给 -EnableReplicatedWrites 才对这些账套开放。
    [string[]]$TestAccounts = @(),
    # 监听端口和主机部分，组成 listenPrefix = http://<ListenHost>:<Port>/u8co/。
    [ValidateRange(1, 65535)][int]$Port = 18089,
    [string]$ListenHost = "+",
    # U8 安装目录。桥只从这里读程序集和 EAI 字段表；安装脚本拒绝在这里（以及它所在的非系统盘）写文件。
    # 已有 config.json 时以其中的 u8Home 为准，不给本参数即可；给了不一致的值会拒绝。
    [string]$U8Home = "C:\U8SOFT",
    # 写入 mobilePush=true：经本服务审批时推送 U8 移动审批（友空间）。不给就是 false，不推送。
    [switch]$MobilePush,
    # 写入 enableReplicatedWrites=true：打开第二级写入总开关，仍只对 testAccounts 开放。不给就不写这个键（等同 false）。
    [switch]$EnableReplicatedWrites
)
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
$ProgressPreference = "SilentlyContinue"
$PSNativeCommandUseErrorActionPreference = $false
$here = $PSScriptRoot
Set-Location -LiteralPath $here
. (Join-Path (Split-Path -Parent $here) "SafePath.ps1")
Initialize-BridgeStaging $here $Root $U8Home $PSBoundParameters.ContainsKey("U8Home")
$RuntimeDir = $script:RuntimeRoot
# 只在写新 config.json 时用到的参数。config.json 已存在时逐个提示未生效。
$ConfigKeysParams = @("U8Server", "AllowedClients", "AllowedAccounts", "TestAccounts", "Port", "ListenHost", "U8Home", "MobilePush", "EnableReplicatedWrites")
$script:BoundKeys = @($PSBoundParameters.Keys)
# 本脚本建的防火墙规则都带这个分组；只认名字和分组都对上的规则。
$FirewallGroup = "U8Co"
$script:OwnService = $false

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

# 逗号或空白分隔都认：pwsh -File 调用时数组参数会整个变成一个字符串。
function Split-List([string[]]$Items) {
    $list = New-Object System.Collections.Generic.List[string]
    foreach ($item in @($Items)) {
        foreach ($part in ([string]$item -split '[,\s]+')) {
            if ($part.Length -gt 0) { $list.Add($part) }
        }
    }
    return , $list.ToArray()
}

function Assert-ServiceName {
    if ($ServiceName -cmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') { return }
    throw "-ServiceName 只能含字母、数字、点、下划线、连字符，字母或数字开头，最长 64"
}

function Write-Rollback {
    Write-Host "回滚：以管理员运行 uninstall.ps1（-Root、-ServiceName 与安装时相同）"
    Write-Host ("  或：Stop-Service " + $ServiceName + "; sc.exe delete " + $ServiceName)
    Write-Host "  netsh http delete urlacl url=<config.json 里的 listenPrefix>"
    Write-Host ("  Remove-NetFirewallRule -Name '" + $ServiceName + "'（分组 " + $FirewallGroup + "）")
    Write-Host "  config.json 与 secret.hex 默认保留，避免调用方的密钥对不上"
}

function Stop-BridgeIfRunning {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -eq $svc) { return }
    if ($svc.Status -eq "Stopped") { return }
    Stop-Service -Name $ServiceName -Force
    (Get-Service -Name $ServiceName).WaitForStatus("Stopped", (New-TimeSpan -Seconds 30))
    Write-Host "已停止服务以便更新程序"
}

function Ensure-Layout {
    Lock-U8CoRoot
    [void][System.IO.Directory]::CreateDirectory((Get-RuntimePath "bin"))
    [void][System.IO.Directory]::CreateDirectory((Get-RuntimePath "logs"))
}

function Copy-BridgeFile([string]$Name) {
    $src = Assert-UnderStaging (Join-Path $here ("out\" + $Name))
    if (-not [System.IO.File]::Exists($src)) { throw "请先运行 build.ps1" }
    $dest = Get-RuntimePath ("bin\" + $Name)
    [System.IO.File]::Copy($src, $dest, $true)
    Write-Host ("已复制 " + $Name)
}

function Test-ClientIp([string]$Text) {
    $ip = $null
    if (-not [System.Net.IPAddress]::TryParse($Text, [ref]$ip)) { return $false }
    return $ip.ToString() -ceq $Text
}

function Assert-SiteParams([string[]]$Clients, [string[]]$Accounts) {
    if ([string]::IsNullOrWhiteSpace($U8Server)) { throw "首次安装必须给 -U8Server（U8 应用服务器的计算机名或 IP）" }
    if ($U8Server -match '[\s"'';<>&|]') { throw "-U8Server 不能含空白、引号、分号等字符" }
    if ($Clients.Count -eq 0) { throw "首次安装必须给 -AllowedClients（允许调用桥的来源 IP，逗号分隔）" }
    foreach ($item in $Clients) {
        if (-not (Test-ClientIp $item)) { throw ("-AllowedClients 只能写单个 IP 的规范写法：" + $item) }
    }
    Assert-AccountCodes $Accounts "-AllowedAccounts"
    if ($ListenHost -notmatch '^(\+|\*|[A-Za-z0-9.\-]+)$') { throw "-ListenHost 只能是 +、* 或主机名/IP" }
}

function Assert-AccountCodes([string[]]$Codes, [string]$Name) {
    foreach ($item in $Codes) {
        if ($item -notmatch '^\d{3}$') { throw ($Name + " 必须是 3 位数字账套号：" + $item) }
    }
}

function New-ConfigJson([string[]]$Clients, [string[]]$Accounts, [string[]]$Tests) {
    $u8 = Resolve-DriveDir $U8Home "-U8Home"
    $cfg = [ordered]@{
        listenPrefix    = "http://" + $ListenHost + ":" + $Port + "/u8co/"
        u8Server        = $U8Server
        allowedAccounts = @($Accounts)
        testAccounts    = @($Tests)
        allowedClients  = @($Clients)
        u8Home          = $u8
        mobilePush      = [bool]$MobilePush
    }
    if ($EnableReplicatedWrites) { $cfg["enableReplicatedWrites"] = $true }
    return ($cfg | ConvertTo-Json -Depth 3)
}

function Write-IgnoredParams {
    foreach ($key in $ConfigKeysParams) {
        if ($script:BoundKeys -notcontains $key) { continue }
        Write-Host ("警告: config.json 已存在，参数 -" + $key + " 未写入；需要时直接编辑 config.json")
    }
}

function Write-DefaultConfig {
    $path = Get-RuntimePath "config.json"
    if ([System.IO.File]::Exists($path)) {
        Write-Host "config.json 已存在，未覆盖"
        Write-IgnoredParams
        return
    }
    $clients = Split-List $AllowedClients
    $accounts = Split-List $AllowedAccounts
    $tests = Split-List $TestAccounts
    Assert-SiteParams $clients $accounts
    Assert-AccountCodes $tests "-TestAccounts"
    $json = New-ConfigJson $clients $accounts $tests
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($path, $json.Trim() + "`r`n", $utf8)
    Write-Host "已写入 config.json"
    if ($accounts.Count -eq 0) { Write-Host "警告: allowedAccounts 为空，所有账套的请求都会被拒绝。需要时编辑 config.json 后重跑本脚本" }
    if ($tests.Count -gt 0 -and -not $EnableReplicatedWrites) {
        Write-Host "提示: 未给 -EnableReplicatedWrites，第二级写入一律 403 feature_disabled；需要时在 config.json 设 enableReplicatedWrites 为 true 后重跑本脚本"
    }
}

function Remove-TempSecret([string]$Tmp, [string]$Final) {
    if ([System.IO.File]::Exists($Final)) { return }
    if (-not [System.IO.File]::Exists($Tmp)) { return }
    $full = Assert-UnderRuntime $Tmp
    if (-not $full.EndsWith(".tmp", [System.StringComparison]::OrdinalIgnoreCase)) { return }
    [System.IO.File]::Delete($full)
}

function Write-SecretHex {
    $final = Get-RuntimePath "secret.hex"
    if ([System.IO.File]::Exists($final)) {
        Write-Host "secret.hex 已存在，未改内容"
        return
    }
    $bytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
    $hex = [System.Convert]::ToHexString($bytes).ToLowerInvariant()
    $tmp = Get-RuntimePath (".secret-" + [guid]::NewGuid().ToString("n") + ".tmp")
    $utf8 = New-Object System.Text.UTF8Encoding $false
    try {
        [System.IO.File]::WriteAllText($tmp, $hex + "`r`n", $utf8)
        [System.IO.File]::Move($tmp, $final)
    } finally {
        Remove-TempSecret $tmp $final
    }
    Write-Host "已生成 secret.hex"
}

function Lock-RuntimeFile([string]$Name) {
    $path = Get-RuntimePath $Name
    if (-not [System.IO.File]::Exists($path)) { return }
    & icacls.exe $path /inheritance:r /grant:r "*S-1-5-18:(F)" "*S-1-5-32-544:(F)"
    if ($LASTEXITCODE -ne 0) { throw "权限设置失败" }
}

function Read-BridgeConfig {
    $path = Get-RuntimePath "config.json"
    return (Get-Content -LiteralPath $path -Encoding UTF8 -Raw | ConvertFrom-Json)
}

function Get-ListenPrefix($Cfg) {
    $prefix = [string]$Cfg.listenPrefix
    if ([string]::IsNullOrWhiteSpace($prefix)) { throw "config.json 缺少 listenPrefix" }
    return $prefix
}

function Get-PortFromPrefix([string]$Prefix) {
    $match = [regex]::Match($Prefix, ":(\d+)/")
    if (-not $match.Success) { throw "listenPrefix 里没有端口" }
    return $match.Groups[1].Value
}

function Get-ClientList($Cfg) {
    $list = New-Object System.Collections.Generic.List[string]
    foreach ($item in @($Cfg.allowedClients)) {
        $text = [string]$item
        if ([string]::IsNullOrWhiteSpace($text)) { continue }
        $list.Add($text)
    }
    return , $list.ToArray()
}

function Test-UrlAcl([string]$Prefix) {
    $text = (& netsh.exe http show urlacl | Out-String)
    return $text.Contains($Prefix, [System.StringComparison]::Ordinal)
}

function Add-UrlAcl([string]$Prefix) {
    if (Test-UrlAcl $Prefix) {
        Write-Host "urlacl 已存在"
        return
    }
    & netsh.exe http add urlacl $("url=" + $Prefix) "sddl=D:(A;;GX;;;SY)"
    if ($LASTEXITCODE -ne 0) { throw "urlacl 添加失败" }
    Write-Host "已添加 urlacl"
}

# 按规则名（-Name，不是显示名）找。同名规则分组不是 U8Co 时拒绝动它；
# 旧版本建的规则没有分组，只在同名服务确认是本桥时才当作自己的，删掉后按新分组重建。
function Get-OwnRule([string]$Name) {
    $rule = @(Get-NetFirewallRule -Name $Name -ErrorAction SilentlyContinue)
    if ($rule.Count -eq 0) { return $null }
    if ($rule[0].Group -eq $FirewallGroup) { return $rule[0] }
    if ([string]::IsNullOrEmpty($rule[0].Group) -and $script:OwnService) {
        $rule[0] | Remove-NetFirewallRule
        Write-Host ("已删除旧版本建的防火墙规则 " + $Name + "（没有分组）")
        return $null
    }
    if ([string]::IsNullOrEmpty($rule[0].Group)) {
        # 例如服务被手工删过：没法确认这条旧规则是本桥的。此时文件和配置已写好，只差服务注册。
        throw ("防火墙规则 " + $Name + " 没有分组，而同名服务不存在，无法确认是旧版本本桥建的。确认后手工删除：Remove-NetFirewallRule -Name '" + $Name + "'，再重跑本脚本")
    }
    throw ("防火墙规则 " + $Name + " 已存在但分组不是 " + $FirewallGroup + "，不是本脚本建的，拒绝修改")
}

function Remove-NamedFirewall([string]$Name) {
    $rule = Get-OwnRule $Name
    if ($null -eq $rule) { return }
    $rule | Remove-NetFirewallRule
    Write-Host ("已删除防火墙规则 " + $Name)
}

function Update-Firewall($Rule, [string]$Port, [string[]]$Clients) {
    $Rule | Set-NetFirewallRule -Direction Inbound -Action Allow -Profile Any -Enabled True
    $Rule | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol TCP -LocalPort $Port
    $Rule | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $Clients
    Write-Host ("防火墙规则已对齐，端口 " + $Port)
}

function Write-FirewallProfileWarning {
    foreach ($item in @(Get-NetFirewallProfile)) {
        if ($item.Enabled) { continue }
        Write-Host ("警告: 防火墙配置文件已关闭 " + $item.Name + "，规则可能不生效")
    }
}

# 规则名就是服务名，分组固定 U8Co。来源为空时不开端口（远程地址为空等于放行所有地址），并删掉旧规则。
function Ensure-Firewall([string]$Port, [string[]]$Clients) {
    $name = $ServiceName
    if ($Clients.Count -eq 0) {
        Remove-NamedFirewall $name
        Write-Host "警告: allowedClients 为空，未开放防火墙端口；桥会拒绝所有来源"
        return
    }
    $rule = Get-OwnRule $name
    if ($null -eq $rule) {
        New-NetFirewallRule -Name $name -DisplayName $name -Group $FirewallGroup -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -RemoteAddress $Clients -Profile Any | Out-Null
        Write-Host ("已新建防火墙规则 " + $name + "，端口 " + $Port)
    } else {
        Update-Firewall $rule $Port $Clients
    }
    Write-FirewallProfileWarning
}

function Get-BinaryPath {
    $exe = Get-RuntimePath "bin\u8co-bridge.exe"
    return ('"' + $exe + '" --root "' + $RuntimeDir + '" --service-name ' + $ServiceName)
}

function Get-ServiceText {
    return @{
        Display     = "U8 CO Bridge (" + $ServiceName + ")"
        Description = "U8 CO 桥：审批流与单据读写。账套不在 allowedAccounts 内不会登录 U8。"
    }
}

function New-BridgeService([string]$BinPath) {
    $text = Get-ServiceText
    New-Service -Name $ServiceName -BinaryPathName $BinPath -StartupType Automatic -DisplayName $text.Display -Description $text.Description | Out-Null
}

function Update-BridgeService([string]$BinPath) {
    $text = Get-ServiceText
    & sc.exe config $ServiceName binPath= $BinPath start= auto obj= LocalSystem DisplayName= $text.Display
    if ($LASTEXITCODE -ne 0) { throw "sc config 失败" }
    & sc.exe description $ServiceName $text.Description
    if ($LASTEXITCODE -ne 0) { throw "sc description 失败" }
}

function Set-BridgeFailure {
    & sc.exe failure $ServiceName reset= 86400 actions= "restart/60000/restart/60000//"
    if ($LASTEXITCODE -ne 0) { throw "sc failure 失败" }
}

function Install-BridgeService {
    $binPath = Get-BinaryPath
    # 再核对一次：前面各步之间服务可能被别人登记或改过。
    if (-not (Assert-BridgeService $ServiceName)) {
        New-BridgeService $binPath
    } else {
        Update-BridgeService $binPath
    }
    Set-BridgeFailure
    Write-Host ("服务 " + $ServiceName + " 已登记")
}

function Invoke-CheckConfig {
    $exe = Get-RuntimePath "bin\u8co-bridge.exe"
    & $exe --check-config --root $RuntimeDir
    if ($LASTEXITCODE -ne 0) { throw "配置检查失败" }
}

function Start-BridgeService {
    $svc = Get-Service -Name $ServiceName
    if ($svc.Status -ne "Running") { Start-Service -Name $ServiceName }
    (Get-Service -Name $ServiceName).WaitForStatus("Running", (New-TimeSpan -Seconds 20))
    Write-Host "服务已运行"
}

function Test-HealthOnce([string]$Url) {
    $resp = Invoke-WebRequest -UseBasicParsing -NoProxy -Uri $Url -TimeoutSec 5
    if ($resp.StatusCode -ne 200) { return $false }
    return $resp.Content.Contains('"ok":true', [System.StringComparison]::Ordinal)
}

function Test-Health([string]$Prefix) {
    $url = $Prefix.Replace("://+", "://127.0.0.1").Replace("://*", "://127.0.0.1")
    if (-not $url.EndsWith("/")) { $url = $url + "/" }
    $url = $url + "v1/health"
    $i = 0
    while ($i -lt 10) {
        try {
            if (Test-HealthOnce $url) {
                Write-Host "健康检查通过"
                return
            }
        } catch {
            Start-Sleep -Seconds 1
        }
        $i = $i + 1
    }
    throw "健康检查失败"
}

# COM 签名自检：只读注册表和类型库，核对桥调用的 U8 组件成员签名。结果只打印，不影响安装结果。
function Invoke-SignatureCheck {
    $exe = Get-RuntimePath "bin\u8co-bridge.exe"
    try {
        $text = (& $exe --check-signatures | Out-String)
        $report = $text | ConvertFrom-Json
        Write-Host ("签名自检: " + [string]$report.summary)
        if ($null -eq $report.features) { return }
        foreach ($prop in $report.features.PSObject.Properties) {
            if ($prop.Value.status -eq "ok") { continue }
            Write-Host ("  " + $prop.Name + ": " + [string]$prop.Value.status)
        }
    } catch {
        Write-Host ("警告: 签名自检没有完成：" + $_.Exception.Message)
    }
}

function Open-InstallLog {
    $dir = Assert-UnderStaging (Join-Path $here "results")
    [void][System.IO.Directory]::CreateDirectory($dir)
    $log = Assert-UnderStaging (Join-Path $dir ("u8co-install-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log"))
    Start-Transcript -LiteralPath $log | Out-Null
}

function Install-Bridge {
    Write-Rollback
    # 停、改、删服务之前，先确认同名服务（若已存在）就是这个运行目录的桥。
    $script:OwnService = Assert-BridgeService $ServiceName
    Stop-BridgeIfRunning
    Ensure-Layout
    Copy-BridgeFile "u8co-bridge.exe"
    Copy-BridgeFile "u8co-bridge.exe.config"
    Write-DefaultConfig
    Write-SecretHex
    foreach ($name in @("config.json", "secret.hex", "sql.json")) { Lock-RuntimeFile $name }
    Set-RootOwner
    $cfg = Read-BridgeConfig
    $prefix = Get-ListenPrefix $cfg
    Invoke-CheckConfig
    Add-UrlAcl $prefix
    $clients = Get-ClientList $cfg
    Ensure-Firewall (Get-PortFromPrefix $prefix) $clients
    Install-BridgeService
    Start-BridgeService
    Test-Health $prefix
    Invoke-SignatureCheck
    Write-Host ("完成。运行目录 " + $RuntimeDir + "，服务 " + $ServiceName + "，防火墙来源 " + ($clients -join ","))
}

if (-not (Test-Elevated)) {
    Write-Host "请以管理员身份运行。"
    exit 1
}
Assert-Pwsh64
Assert-ServiceName
Open-InstallLog
$failed = $false
try {
    Install-Bridge
} catch {
    Write-Host ("错误: " + $_.Exception.Message)
    $failed = $true
} finally {
    Stop-Transcript | Out-Null
}
if ($failed) { exit 1 }
