#Requires -Version 7.4
#Requires -PSEdition Core
# 目录查询只读（SELECT）。可选的 U8 Login 会在 UFSystem 留下登录记录，不能说成完全不改库。
# 不写注册表、不用 sa。脚本写出的文件只有本目录 results\u8co-probe-*.txt。
# 读 U8 安装目录（-U8Home，缺省 D:\U8SOFT）下的文件必须显式带 -ReadU8Files。
# -DenyAccounts 里的账套（如正式账套）始终拒绝登录。其他账套必须出现在显式传入的 -TestAccountAllowlist 里。
# 两个清单都写成逗号分隔的账套号，例如 -DenyAccounts 001,002。
param(
    [string]$TestAccount = "",
    [string[]]$TestAccountAllowlist = @(),
    [string[]]$DenyAccounts = @(),
    [string]$Operator = "",
    [string]$Year = "",
    [string]$Date = "",
    [string]$Server = "127.0.0.1",
    [string]$U8Home = "D:\U8SOFT",
    [switch]$ReadU8Files
)

$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
$PSNativeCommandUseErrorActionPreference = $false

function Add-TextArg($Psi, $Bound, [string]$Name, [string]$Value) {
    if (-not $Bound.ContainsKey($Name)) { return }
    $Psi.ArgumentList.Add("-" + $Name)
    $Psi.ArgumentList.Add($Value)
}

# 列表参数只传一次，逗号连成一个字符串：pwsh -File 不接受同一个参数出现两次。
function Add-ListArgs($Psi, $Bound, [string]$Name, [string[]]$Items) {
    if (-not $Bound.ContainsKey($Name)) { return }
    $Psi.ArgumentList.Add("-" + $Name)
    $Psi.ArgumentList.Add((@($Items) -join ","))
}

# 账套号清单可以写成 001,002（pwsh -File 收到的是一个字符串），也可以是数组。拆开、去空白、逐个校验。
function Split-AccountList([string[]]$Items, [string]$Name) {
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($item in @($Items)) {
        foreach ($part in ([string]$item).Split(",")) {
            $code = $part.Trim()
            if ($code.Length -eq 0) { continue }
            if ($code -cnotmatch '^[0-9]{3}$') {
                # 在 pwsh 里直接写 001,002 会被当成数字 1,2，所以要加引号。
                Write-Host ("-" + $Name + " 里的账套号必须是三位数字：" + $code + "。在 pwsh 里调用时加引号，例如 '001,002'")
                exit 1
            }
            if (-not $out.Contains($code)) { $out.Add($code) }
        }
    }
    return ,$out.ToArray()
}

function Add-BoundArgs($Psi, $Bound) {
    Add-TextArg $Psi $Bound "TestAccount" $TestAccount
    Add-TextArg $Psi $Bound "Operator" $Operator
    Add-TextArg $Psi $Bound "Year" $Year
    Add-TextArg $Psi $Bound "Date" $Date
    Add-TextArg $Psi $Bound "Server" $Server
    Add-TextArg $Psi $Bound "U8Home" $U8Home
    Add-ListArgs $Psi $Bound "TestAccountAllowlist" $TestAccountAllowlist
    Add-ListArgs $Psi $Bound "DenyAccounts" $DenyAccounts
    if ($Bound.ContainsKey("ReadU8Files")) { $Psi.ArgumentList.Add("-ReadU8Files") }
}

function Start-Probe32($Bound) {
    $exe = "C:\Program Files (x86)\PowerShell\7\pwsh.exe"
    if (-not [System.IO.File]::Exists($exe)) {
        Write-Host "找不到 32 位 pwsh。请安装 C:\Program Files (x86)\PowerShell\7\pwsh.exe"
        exit 1
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.UseShellExecute = $false
    foreach ($flag in @("-NoLogo", "-NoProfile", "-Sta", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath)) {
        $psi.ArgumentList.Add($flag)
    }
    Add-BoundArgs $psi $Bound
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.WaitForExit()
    exit $proc.ExitCode
}

# 空参数也要原样传给 32 位 pwsh。ArgumentList 不会丢掉空字符串。
$script:AllowListGiven = $PSBoundParameters.ContainsKey("TestAccountAllowlist")
$TestAccountAllowlist = Split-AccountList $TestAccountAllowlist "TestAccountAllowlist"
$DenyAccounts = Split-AccountList $DenyAccounts "DenyAccounts"
if ([Environment]::Is64BitProcess) {
    Start-Probe32 $PSBoundParameters
}

$here = $PSScriptRoot
Set-Location -LiteralPath $here
. (Join-Path (Split-Path -Parent $here) "SafePath.ps1")
. (Join-Path $here "u8co-probe-login.ps1")
# -U8Home 同时决定哪个目录（以及非系统盘时哪个盘）不能写。
Initialize-Staging $here "" $U8Home

$script:ProbeLines = New-Object System.Collections.Generic.List[string]
$script:AccountRows = @()
$script:Hkcr32 = $null
$script:AccountSql = "SELECT cAcc_Id, cAcc_Name, iYear FROM UFSystem..UA_Account"
$script:DatabaseSql = "SELECT name, create_date, state FROM sys.databases WHERE name LIKE N'UFDATA[_]%' ORDER BY name"

function Add-ProbeLine([string]$Text) {
    $clean = ($Text -replace "[\r\n]+", " ")
    $script:ProbeLines.Add($clean) | Out-Null
    Write-Host $clean
}

function Protect-Text([string]$Text, [string]$Secret) {
    # 异常文本里可能夹着刚输入的口令或 OLE DB 的 password=，结果文件不能留下。
    $out = ""
    if ($null -ne $Text) { $out = $Text }
    if (-not [string]::IsNullOrEmpty($Secret)) { $out = $out.Replace($Secret, "***") }
    $out = [regex]::Replace($out, "(?i)(password|pwd)\s*=\s*[^;,\s]*", '${1}=***')
    if ($out.Length -gt 300) { return $out.Substring(0, 300) }
    return $out
}

function Open-LocalMachine([string]$ViewName) {
    try {
        $view = [Microsoft.Win32.RegistryView]$ViewName
        return [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $view)
    } catch { return $null }
}

function Open-ClassesRoot32 {
    # 32 位视图才是 U8 进程内 COM 的注册位置。
    if ($null -eq $script:Hkcr32) {
        $script:Hkcr32 = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
            [Microsoft.Win32.RegistryHive]::ClassesRoot, [Microsoft.Win32.RegistryView]::Registry32)
    }
    return $script:Hkcr32
}

function Read-DefaultValue($Root, [string]$Sub) {
    if ($null -eq $Root) { return "" }
    $child = $Root.OpenSubKey($Sub)
    if ($null -eq $child) { return "" }
    $value = $child.GetValue("")
    if ($null -eq $value) { return "" }
    return [string]$value
}

function Get-ComServerPath([string]$Clsid) {
    if ([string]::IsNullOrWhiteSpace($Clsid)) { return "" }
    $root = Open-ClassesRoot32
    $inproc = Read-DefaultValue $root ("CLSID\" + $Clsid + "\InprocServer32")
    $path = $inproc
    if ([string]::IsNullOrWhiteSpace($path)) {
        $path = Read-DefaultValue $root ("CLSID\" + $Clsid + "\LocalServer32")
    }
    if ([string]::IsNullOrWhiteSpace($path)) { return "" }
    return [Environment]::ExpandEnvironmentVariables($path.Trim().Trim('"'))
}

function Test-U8InstallFile([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    $full = $Path.Trim().Trim('"')
    try { $full = [System.IO.Path]::GetFullPath($full) } catch { }
    $u8dir = $U8Home.TrimEnd("\")
    if ($full.StartsWith($u8dir + "\", [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    return $full.Equals($u8dir, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-FileVersionText([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return "missing" }
    if ((Test-U8InstallFile $Path) -and (-not $ReadU8Files)) { return "skipped-need-ReadU8Files" }
    if (-not (Test-Path -LiteralPath $Path)) { return "file-missing" }
    return [string]([System.Diagnostics.FileVersionInfo]::GetVersionInfo($Path).FileVersion)
}

function Write-ProgId([string]$ProgId) {
    # 第二个参数 false：未注册时返回空，不要把探测主流程打成异常。
    $found = "no"
    try {
        if ($null -ne [type]::GetTypeFromProgID($ProgId, $false)) { $found = "yes" }
    } catch { $found = "error" }
    $clsid = Read-DefaultValue (Open-ClassesRoot32) ($ProgId + "\CLSID")
    $path = Get-ComServerPath $clsid
    Add-ProbeLine ("progid=" + $ProgId + " GetTypeFromProgID=" + $found + " clsid=" + $clsid + " server=" + $path + " file_version=" + (Get-FileVersionText $path))
}

function Get-DotNetLabel([int]$Release) {
    if ($Release -ge 533320) { return "4.8.1+" }
    if ($Release -ge 528040) { return "4.8" }
    if ($Release -ge 461808) { return "4.7.2" }
    if ($Release -ge 461308) { return "4.7.1" }
    if ($Release -ge 460798) { return "4.7" }
    if ($Release -ge 394802) { return "4.6.2" }; if ($Release -ge 393295) { return "4.6" }
    return "低于 4.6 或未知"
}

function Write-DotNetRelease {
    foreach ($viewName in @("Registry64", "Registry32")) {
        $base = Open-LocalMachine $viewName
        if ($null -eq $base) { Add-ProbeLine ("dotnet view=" + $viewName + " key=unavailable"); continue }
        $key = $base.OpenSubKey("SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full")
        if ($null -eq $key) { Add-ProbeLine ("dotnet view=" + $viewName + " key=missing"); continue }
        $rel = $key.GetValue("Release")
        $ver = [string]$key.GetValue("Version")
        if ($null -eq $rel) { Add-ProbeLine ("dotnet view=" + $viewName + " Release=missing Version=" + $ver); continue }
        Add-ProbeLine ("dotnet view=" + $viewName + " Release=" + $rel + " Version=" + $ver + " label=" + (Get-DotNetLabel ([int]$rel)))
    }
}

function Read-InstallValue([string]$ViewName, [string]$Sub, [string]$Name) {
    $base = Open-LocalMachine $ViewName
    if ($null -eq $base) { return "" }
    $key = $base.OpenSubKey($Sub)
    if ($null -eq $key) { return "" }
    $value = $key.GetValue($Name)
    if ($null -eq $value) { return "" }
    return ([string]$value).Trim()
}

function Write-U8InstallPath {
    # 常见安装项。命中哪条就记哪条，不把没读到的键写成空。
    $specs = "Registry32/SOFTWARE\UFIDA\U8\Setup/InstallPath;Registry32/SOFTWARE\UFIDA\U8\Install/InstallPath;Registry32/SOFTWARE\Ufsoft\U8/InstallPath;Registry64/SOFTWARE\WOW6432Node\UFIDA\U8\Setup/InstallPath;Registry64/SOFTWARE\UFIDA\U8\Setup/InstallPath;Registry32/SOFTWARE\UFSOFT\UF2000/InstallPath"
    foreach ($spec in ($specs -split ";")) {
        $item = $spec -split "/"
        $val = Read-InstallValue $item[0] $item[1] $item[2]
        if ([string]::IsNullOrWhiteSpace($val)) { continue }
        Add-ProbeLine ("u8path registry view=" + $item[0] + " key=" + $item[1] + " value=" + $item[2] + "=" + $val)
    }
    foreach ($dir in (@($U8Home, "D:\U8SOFT", "C:\U8SOFT", "E:\U8SOFT") | Select-Object -Unique)) {
        Write-InstallDll $dir
    }
}

function Write-InstallDll([string]$Dir) {
    $dll = Join-Path $Dir "ufcomsql\U8Login.dll"
    if ((Test-U8InstallFile $dll) -and (-not $ReadU8Files)) {
        Add-ProbeLine ("u8path filesystem " + $dll + " skipped=need-ReadU8Files")
        return
    }
    Add-ProbeLine ("u8path filesystem " + $dll + " exists=" + (Test-Path -LiteralPath $dll))
}

function ConvertTo-CellText($Value) {
    if ($null -eq $Value -or $Value -is [System.DBNull]) { return "" }
    if ($Value -is [datetime]) { return ([datetime]$Value).ToString("yyyy-MM-ddTHH:mm:ss") }
    return (([string]$Value) -replace "[\r\n]+", " ")
}

function Get-SqlTargets {
    $list = New-Object System.Collections.Generic.List[string]
    [void]$list.Add("localhost")
    [void]$list.Add(".")
    $base = Open-LocalMachine "Registry64"
    if ($null -eq $base) { return ,$list }
    $key = $base.OpenSubKey("SOFTWARE\Microsoft\Microsoft SQL Server\Instance Names\SQL")
    if ($null -eq $key) { return ,$list }
    foreach ($name in @($key.GetValueNames())) {
        # 实例名会拼进连接串，只留字母数字，避免分号改掉后面的键。
        if ($name -eq "MSSQLSERVER" -or $name -notmatch '^[A-Za-z0-9_]+$') { continue }
        [void]$list.Add("localhost\" + $name)
    }
    return ,$list
}

function Release-Com($Obj) {
    if ($null -eq $Obj) { return }
    try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Obj) } catch { }
}

function Close-Com($Obj) {
    if ($null -eq $Obj) { return }
    try { $Obj.Close() } catch { }
    Release-Com $Obj
}

function Read-AdoRows($Conn, [string]$Sql) {
    $rs = $null
    $rows = New-Object System.Collections.Generic.List[object]
    try {
        $rs = $Conn.Execute($Sql)
        while (($null -ne $rs) -and (-not $rs.EOF)) {
            $map = @{}
            for ($i = 0; $i -lt $rs.Fields.Count; $i++) {
                $field = $rs.Fields.Item($i)
                $map[$field.Name] = ConvertTo-CellText $field.Value
            }
            [void]$rows.Add($map)
            [void]$rs.MoveNext()
        }
    } finally { Close-Com $rs }
    return ,$rows
}

function Open-AdoIntegrated([string]$Server) {
    $conn = New-Object -ComObject ADODB.Connection
    try {
        $conn.ConnectionTimeout = 8
        # 集成身份，等价于 sqlcmd -E。不用 sa，语句只有 SELECT。
        $conn.Open("Provider=SQLOLEDB;Data Source=" + $Server + ";Initial Catalog=master;Integrated Security=SSPI;")
        return $conn
    } catch {
        Release-Com $conn
        throw
    }
}

function Open-FirstAdo {
    if ($null -eq [type]::GetTypeFromProgID("ADODB.Connection", $false)) {
        Add-ProbeLine "sql=ado-unavailable ADODB.Connection 未注册"
        return $null
    }
    foreach ($server in @(Get-SqlTargets)) {
        try {
            $conn = Open-AdoIntegrated $server
            Add-ProbeLine ("sql=connected provider=SQLOLEDB server=" + $server + " auth=integrated")
            return $conn
        } catch {
            Add-ProbeLine ("sql=ado-fail server=" + $server + " " + (Protect-Text $_.Exception.Message ""))
        }
    }
    return $null
}

function Write-KindRows([string]$Kind, $Rows, [string[]]$Keys) {
    $items = @($Rows)
    Add-ProbeLine ($Kind + " count=" + $items.Count)
    foreach ($row in $items) {
        $bits = @()
        foreach ($key in $Keys) { $bits += ($key + "=" + [string]$row[$key]) }
        Add-ProbeLine ($Kind + " " + ($bits -join " "))
    }
}

function Write-SqlCatalog {
    $ado = Open-FirstAdo
    if ($null -eq $ado) {
        Add-ProbeLine "sql=integrated-auth-failed 集成身份连不上本机 SQL。未使用 sa，没有写任何库。"
        return
    }
    try {
        $script:AccountRows = Read-AdoRows $ado $script:AccountSql
        Write-KindRows "account" $script:AccountRows @("cAcc_Id", "iYear", "cAcc_Name")
        Write-KindRows "database" (Read-AdoRows $ado $script:DatabaseSql) @("name", "create_date", "state")
    } catch {
        Add-ProbeLine ("sql=query-failed " + (Protect-Text $_.Exception.Message ""))
    } finally { Close-Com $ado }
}

function Save-ProbeResult {
    $dir = Assert-UnderStaging (Join-Path $PSScriptRoot "results")
    [void][System.IO.Directory]::CreateDirectory($dir)
    $path = Assert-UnderStaging (Join-Path $dir ("u8co-probe-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".txt"))
    $script:ProbeLines | Set-Content -LiteralPath $path -Encoding utf8BOM
    Write-Host ("结果已写入 " + $path)
}

$exitCode = 1
try {
    Add-ProbeLine "probe=u8co-probe catalog=select-only optional-login=writes-ufsystem-rows writes=results-file-only"
    Add-ProbeLine ("when=" + (Get-Date -Format "yyyy-MM-ddTHH:mm:ss") + " host=" + [Environment]::MachineName)
    Add-ProbeLine ("os=" + [Environment]::OSVersion.VersionString + " ps=" + $PSVersionTable.PSVersion.ToString() + " clr=" + [Environment]::Version.ToString())
    $bits = "64"
    if (-not [Environment]::Is64BitProcess) { $bits = "32" }
    Add-ProbeLine ("ptr=" + [IntPtr]::Size + " bitness=" + $bits + " apartment=" + [System.Threading.Thread]::CurrentThread.GetApartmentState().ToString())
    Add-ProbeLine "section=com"
    foreach ($id in @("U8Login.clsLogin", "USSAServer.clsSystem", "VoucherCO_Sa.ClsVoucherCO_SA", "USERPCO.VoucherCO", "ADODB.Connection", "MSXML2.DOMDocument")) {
        Write-ProgId $id
    }
    Add-ProbeLine "section=dotnet"
    Write-DotNetRelease
    Add-ProbeLine "section=u8path"
    Write-U8InstallPath
    Add-ProbeLine "section=sql"
    $sqlcmd = "C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\170\Tools\Binn\SQLCMD.EXE"
    Add-ProbeLine ("sqlcmd=" + $sqlcmd + " exists=" + [System.IO.File]::Exists($sqlcmd) + " probe_uses=ADO-integrated")
    Add-ProbeLine "sql.note=只执行 SELECT。集成身份失败会写明，不会改用 sa。"
    Add-ProbeLine "account.note=UA_Account.iYear 是账套启用年度，不是当前会计年。登录请另传 -Year"
    Write-SqlCatalog
    Add-ProbeLine "section=login"
    $exitCode = Invoke-OptionalLogin
    Save-ProbeResult
} catch {
    $msg = Protect-Text $_.Exception.Message ""
    Write-Host ("探测失败: " + $msg)
    Add-ProbeLine ("fatal=" + $msg)
    try { Save-ProbeResult } catch { Write-Host "结果文件没有写成" }
    $exitCode = 1
}
exit $exitCode
