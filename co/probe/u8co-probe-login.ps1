#Requires -Version 7.4
#Requires -PSEdition Core
# 由 u8co-probe.ps1 dot-source。没有 param：参数留在主脚本。
# U8 Login 会在 UFSystem 写登录记录。-DenyAccounts 里的账套始终拒绝。其余账套必须在 -TestAccountAllowlist 里。

function Test-ProductionAccount([string]$Acc) {
    foreach ($item in @($DenyAccounts)) {
        if (([string]$item).Trim() -eq $Acc.Trim()) { return $true }
    }
    return $false
}

function Test-OnAllowList([string]$Acc) {
    if (-not $script:AllowListGiven) { return $false }
    foreach ($item in @($TestAccountAllowlist)) {
        if ([string]$item -eq $Acc) { return $true }
    }
    return $false
}

function Test-LoginPair([string]$Acc, [string]$Op) {
    if (($Acc -eq "") -and ($Op -eq "")) { return "skip" }
    if (($Acc -eq "") -or ($Op -eq "")) { return "need-both" }
    return "ok"
}

function Test-LoginReady([string]$Acc) {
    if ($Acc -notmatch '^\d{3}$') { return "账套号必须是 3 位数字" }
    if ($null -eq [type]::GetTypeFromProgID("U8Login.clsLogin", $false)) { return "U8Login.clsLogin 未注册，未提示口令" }
    if ([System.Threading.Thread]::CurrentThread.GetApartmentState().ToString() -ne "STA") { return "当前线程不是 STA。请用 run-u8co-probe.bat" }
    if ($Server -notmatch '^[A-Za-z0-9._-]{1,128}$') { return "-Server 只能是主机名或 IPv4，不能是连接串" }
    if ((-not [Environment]::UserInteractive) -or [Console]::IsInputRedirected) { return "没有交互控制台，不提示口令" }
    return ""
}

function Resolve-LoginYear([string]$Acc) {
    Add-ProbeLine ("account.note acc=" + $Acc + " UA_Account.iYear 是启用年度，不是当前会计年")
    if ($Year -match '^\d{4}$') { return $Year }
    if (-not [string]::IsNullOrWhiteSpace($Year)) {
        Add-ProbeLine "login=skipped -Year 必须是 4 位数字"
        return ""
    }
    Add-ProbeLine "login=skipped 请显式传 -Year"
    return ""
}

function Resolve-LoginDate {
    if ([string]::IsNullOrWhiteSpace($Date)) { return (Get-Date -Format "yyyy-MM-dd") }
    if ($Date -notmatch '^\d{4}-\d{2}-\d{2}$') {
        Add-ProbeLine "login=skipped -Date 必须是 yyyy-MM-dd"
        return ""
    }
    return $Date
}

function Read-ProbePassword {
    $sec = Read-Host -AsSecureString "请输入 U8 操作员口令（不回显，不会写入结果文件）"
    if ($null -eq $sec -or $sec.Length -eq 0) { return "" }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Add-OleDbPiece($Shape, [string]$Name, [string]$Value) {
    if ($Name -match '(?i)^(password|pwd)$') { $Shape.HasSecret = $true; return }
    if ($Name -match '(?i)^provider$') { $Shape.Provider = $Value; return }
    if ($Name -match '(?i)^(data source|server|address|addr|network address)$') { $Shape.Server = $Value; return }
    if ($Name -match '(?i)^(initial catalog|database)$') { $Shape.Database = $Value; return }
    if ($Name -ne "") { [void]$Shape.Others.Add($Name) }
}

function New-DbNameShape([string]$Raw) {
    # 连接串里可能有口令。只留下提供程序、服务器、库名，其余键只记名字。
    $shape = [pscustomobject]@{
        Provider = ""; Server = ""; Database = ""; HasSecret = $false
        Others = (New-Object System.Collections.Generic.List[string])
    }
    foreach ($part in ($Raw -split ";")) {
        $eq = $part.IndexOf("=")
        if ($eq -lt 1) { continue }
        Add-OleDbPiece $shape $part.Substring(0, $eq).Trim() $part.Substring($eq + 1).Trim()
    }
    return ,$shape
}

function Write-DbNameShape($Login, [string]$Secret) {
    $raw = ""
    try { $raw = [string]$Login.UfDbName } catch {
        Add-ProbeLine ("UfDbName=unreadable " + (Protect-Text $_.Exception.Message $Secret))
        return
    }
    if ([string]::IsNullOrWhiteSpace($raw)) { Add-ProbeLine "UfDbName=empty"; return }
    $shape = New-DbNameShape $raw
    Add-ProbeLine ("UfDbName.Provider=" + (Protect-Text $shape.Provider $Secret))
    Add-ProbeLine ("UfDbName.Server=" + (Protect-Text $shape.Server $Secret))
    Add-ProbeLine ("UfDbName.Database=" + (Protect-Text $shape.Database $Secret))
    Add-ProbeLine ("UfDbName.has_secret=" + $shape.HasSecret)
    Add-ProbeLine ("UfDbName.other_keys=" + ($shape.Others -join ","))
}

function Stop-U8Login($Login) {
    if ($null -eq $Login) { return }
    try { [void]$Login.ShutDown() } catch { }
    Release-Com $Login
}

function Connect-U8Login([string]$Acc, [string]$YearText, [string]$DateText) {
    $plain = ""
    $login = $null
    try {
        $plain = Read-ProbePassword
        if ([string]::IsNullOrEmpty($plain)) { Add-ProbeLine "login=skipped 口令为空"; return 1 }
        $op = $Operator.Trim()
        Add-ProbeLine "login.note=U8 Login 会在 UFSystem 留下记录，这一步不是只读"
        Add-ProbeLine ("login.attempt acc=" + $Acc + " year=" + $YearText + " operator=" + $op + " date=" + $DateText + " server=" + $Server + " sub=SA")
        $login = New-Object -ComObject "U8Login.clsLogin"
        $ok = $login.Login("SA", $Acc, $YearText, $op, $plain, $DateText, $Server, "")
        if (-not (($ok -eq $true) -or ($ok -eq 1))) {
            $share = ""
            try { $share = [string]$login.ShareString } catch { $share = "读不到 ShareString" }
            Add-ProbeLine ("login=false ShareString=" + (Protect-Text $share $plain))
            return 1
        }
        $state = [string]$login.LogState
        Add-ProbeLine ("login=true LogState=" + $state)
        Write-DbNameShape $login $plain
        if ($state -ne "0") { Add-ProbeLine "login=LogState 不是 0。桥接只把 0 当成登录成功。"; return 1 }
        return 0
    } catch {
        Add-ProbeLine ("login=error " + (Protect-Text $_.Exception.Message $plain))
        return 1
    } finally {
        $plain = ""
        Stop-U8Login $login
    }
}

function Invoke-OptionalLogin {
    $acc = $TestAccount.Trim()
    $op = $Operator.Trim()
    $pair = Test-LoginPair $acc $op
    if ($pair -eq "skip") { Add-ProbeLine "login=skipped"; return 0 }
    if ($pair -eq "need-both") { Add-ProbeLine "login=skipped 需要同时提供 -TestAccount 和 -Operator"; return 1 }
    if (Test-ProductionAccount $acc) { Add-ProbeLine ("login=refused 账套 " + $acc + " 在 -DenyAccounts 里，禁止探测登录"); return 2 }
    if (-not (Test-OnAllowList $acc)) { Add-ProbeLine "login=refused 账套不在显式的 -TestAccountAllowlist 里"; return 2 }
    $reason = Test-LoginReady $acc
    if ($reason -ne "") { Add-ProbeLine ("login=skipped " + $reason); return 1 }
    $loginYear = Resolve-LoginYear $acc
    if ($loginYear -eq "") { return 1 }
    $loginDate = Resolve-LoginDate
    if ($loginDate -eq "") { return 1 }
    return (Connect-U8Login $acc $loginYear $loginDate)
}
