#Requires -Version 7.4
#Requires -PSEdition Core
# 本通道桥的健康检查、源码哈希文件和桥进程查找 / 结束，service-stop.ps1、service-redeploy.ps1 和 bridge-hash.ps1 共用。
# 先 dot-source common.ps1。

# 同一台服务器上可能还有别的调试通道的桥（同一个 exe 名，不同运行目录）。只认本通道的：
# 路径正好是 <BridgeRoot>\bin\<exe>，或者是停服务之前记下的本服务进程号（进程创建时间不晚于记录时刻，防进程号复用）。
# 读不到路径的别的进程不算。不按进程名结束进程。
$script:OldPid = 0
$script:OldPidAt = [datetime]::MinValue

function Save-ServicePid {
    $svc = Get-CimInstance -ClassName Win32_Service -Filter ("Name='" + [string]$script:Cfg.ServiceName + "'")
    if ($null -eq $svc) { return }
    $script:OldPid = [int]$svc.ProcessId
    $script:OldPidAt = Get-Date
}

function Get-BridgeExePath {
    $exe = [string]$script:Cfg.BridgeExe
    return [System.IO.Path]::GetFullPath((Get-BridgePath ("bin\" + $exe)))
}

function Test-OwnBridge($Proc, [string]$Want) {
    $path = [string]$Proc.ExecutablePath
    $same = $false
    if (-not [string]::IsNullOrWhiteSpace($path)) {
        $same = ([System.IO.Path]::GetFullPath($path)).Equals($Want, [System.StringComparison]::OrdinalIgnoreCase)
    }
    if ($same) { return $true }
    # 进程号对上只在读不到路径时才认；读得到而路径不同的是别的通道的桥。
    if (($script:OldPid -le 0) -or ([int]$Proc.ProcessId -ne $script:OldPid)) { return $false }
    if (-not [string]::IsNullOrWhiteSpace($path)) { return $false }
    $born = $Proc.CreationDate
    return (($null -ne $born) -and ([datetime]$born -le $script:OldPidAt))
}

function Get-OwnBridgeProcs {
    $want = Get-BridgeExePath
    $procs = @(Get-CimInstance -ClassName Win32_Process -Filter ("Name='" + [string]$script:Cfg.BridgeExe + "'"))
    return @($procs | Where-Object { Test-OwnBridge $_ $want })
}

function Test-BridgeAlive {
    return (@(Get-OwnBridgeProcs).Count -gt 0)
}

# 服务已报 Stopped 时进程仍可能活着（COM 卡在 STA、RCW 终结器），exe 被锁。按上面的规则轮询，最多 $Seconds 秒。
function Wait-BridgeExit([int]$Seconds) {
    # 按墙钟计时：每次 CIM 查询本身也要时间，按轮数数会拖长。
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $Seconds) {
        if (-not (Test-BridgeAlive)) { return $true }
        Start-Sleep -Seconds 1
    }
    return (-not (Test-BridgeAlive))
}

# 只按进程号结束本通道的桥进程（见 Test-OwnBridge）。
function Stop-BridgeImage {
    $want = Get-BridgeExePath
    if ([System.IO.File]::Exists($want)) { Assert-NoReparse $want }
    foreach ($proc in @(Get-OwnBridgeProcs)) {
        Write-Host ("结束本通道桥进程 " + [int]$proc.ProcessId)
        try {
            Stop-Process -Id ([int]$proc.ProcessId) -Force
        } catch {
            Write-Host ("结束进程失败: " + $_.Exception.Message)
        }
    }
}

# 等本通道的桥进程退出，最多 $Seconds 秒；还在就结束它，再等 15 秒确认。结束不掉抛错，调用方据此不覆盖 exe、不启动服务。
# 返回 $true 表示动手结束过进程。
function Stop-BridgeLeftover([int]$Seconds) {
    if (Wait-BridgeExit $Seconds) { return $false }
    Write-Host ("服务已停但桥进程 " + $Seconds + " 秒内未退出，按路径和进程号结束")
    Stop-BridgeImage
    if (Wait-BridgeExit 15) { return $true }
    throw ("本通道的桥进程结束不掉（" + (Get-BridgeExePath) + "），不能继续，请在服务器上人工处理")
}

# 健康检查每次都写一行审计。应答了 200 而本通道运行目录的审计日志没有变长，说明端口上是别的桥
# （例如另一条调试通道用了同一个端口，本通道的新桥没绑上），不算部署成功。
function Get-AuditMark {
    $name = "u8co-" + (Get-Date).ToString("yyyyMMdd") + ".log"
    $path = Get-BridgePath ("logs\" + $name)
    $size = [int64]-1
    if ([System.IO.File]::Exists($path)) { $size = (New-Object System.IO.FileInfo $path).Length }
    return @{ name = $name; size = $size }
}

function Test-AuditGrew($Mark) {
    $now = Get-AuditMark
    if ($now.name -ne $Mark.name) { return ($now.size -gt 0) }
    return ($now.size -gt $Mark.size)
}

function Test-ServiceHealth([int]$Tries = 10) {
    $url = "http://127.0.0.1:" + [int]$script:Cfg.BridgePort + [string]$script:Cfg.BridgePath + "/v1/health"
    $i = 0
    while ($i -lt $Tries) {
        $mark = Get-AuditMark
        $resp = $null
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -NoProxy -Uri $url -TimeoutSec 5
        } catch {
            Start-Sleep -Seconds 1
        }
        if ($null -ne $resp -and $resp.StatusCode -eq 200 -and $resp.Content.Contains('"ok":true')) {
            if (Test-AuditGrew $mark) { return }
            throw ("端口 " + [int]$script:Cfg.BridgePort + " 上应答健康检查的不是本通道的桥（" + (Get-BridgePath "logs") + " 没有新的审计行），检查是否与别的桥用了同一个端口")
        }
        $i = $i + 1
    }
    throw "健康检查失败"
}

# 桥源码哈希（Linux 端 kitpack.source_hash 算的 64 位小写十六进制），部署成功后写在 exe 旁边的 bin\src.sha256。
# 文件三行：源码哈希、当时 exe 的 SHA-256、当时 exe.config 的 SHA-256。有人手工换过 exe 或 exe.config（例如重跑 install.ps1），
# 后两行就对不上，Read-SrcHash 当没有哈希，调用方照常编译部署。
$script:SrcHashName = "src.sha256"

function Test-SrcHashText([string]$Text) {
    return ($Text -cmatch '^[0-9a-f]{64}$')
}

function Get-BinFileHash([string]$Name) {
    $path = [System.IO.Path]::GetFullPath((Get-BridgePath ("bin\" + $Name)))
    if (-not [System.IO.File]::Exists($path)) { return "" }
    Assert-NoReparse $path
    return ([string](Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash).ToLowerInvariant()
}

function Format-SrcHashDoc([string]$Src) {
    $exe = [string]$script:Cfg.BridgeExe
    $lines = @($Src, (Get-BinFileHash $exe), (Get-BinFileHash ($exe + ".config")))
    return ($lines -join "`n") + "`n"
}

function Read-SrcHashDoc {
    $path = [System.IO.Path]::GetFullPath((Get-BridgePath ("bin\" + $script:SrcHashName)))
    if (-not [System.IO.File]::Exists($path)) { return @() }
    Assert-NoReparse $path
    return @(([System.IO.File]::ReadAllText($path)).Split("`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" })
}

function Read-SrcHash {
    # 没有文件、是重解析点、读不了、格式不对、exe 或 exe.config 已不是写哈希时那一份，一律返回空串。
    try {
        $lines = @(Read-SrcHashDoc)
        if ($lines.Count -ne 3) { return "" }
        if (-not (Test-SrcHashText $lines[0])) { return "" }
        $exe = [string]$script:Cfg.BridgeExe
        if ($lines[1] -cne (Get-BinFileHash $exe)) { return "" }
        if ($lines[2] -cne (Get-BinFileHash ($exe + ".config"))) { return "" }
        return $lines[0]
    } catch {
        return ""
    }
}
