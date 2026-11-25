#Requires -Version 7.4
#Requires -PSEdition Core
# 只停配置里的桥服务。StopPending 不等到底；超时后只结束本通道的桥进程（路径正好是桥 exe，或停之前记下的服务进程号）。
# SCM 报 Stopped 不等于进程已退出（COM 卡在 STA 时 exe 仍被锁）：之后再等进程退出，15 秒不退就结束它并确认已退出。
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
. (Join-Path $PSScriptRoot "common.ps1")
. (Join-Path $PSScriptRoot "bridgelib.ps1")
$script:Service = [string]$script:Cfg.ServiceName

function Request-BridgeStop {
    try {
        Stop-Service -Name $script:Service -Force -NoWait
    } catch {
        Write-Host ("停止请求: " + $_.Exception.Message)
    }
}

function Wait-Stopped([int]$Seconds) {
    $svc = Get-Service -Name $script:Service
    try {
        $svc.WaitForStatus("Stopped", (New-TimeSpan -Seconds $Seconds))
        return $true
    } catch {
        $svc.Refresh()
        return [string]$svc.Status -eq "Stopped"
    }
}

$svc = Get-Service -Name $script:Service -ErrorAction SilentlyContinue
if ($null -eq $svc) { throw ("服务 " + $script:Service + " 不存在") }
# 只停本通道的桥：binPath 必须是 <BridgeRoot>\bin\<exe> 且带 --root。服务名配错时拒绝，不去停别的通道的桥。
Assert-ServiceRoot
if ([string]$svc.Status -eq "Stopped") {
    # 上一次停止留下的残留进程（服务已停、exe 仍被锁）也在这里清掉；给 5 秒让刚停的进程自己退出。
    if (Stop-BridgeLeftover 5) { Write-Host "service-stop already killed" } else { Write-Host "service-stop already" }
    exit 0
}
Save-ServicePid
Request-BridgeStop
$killed = $false
if (-not (Wait-Stopped 120)) {
    # 服务还在 StopPending 时结束进程，服务控制管理器可能当作故障按恢复动作（60 秒后）重启服务。
    # 新版桥 90 秒内会自己报已停止并结束进程，走到这里的只剩旧版桥或卡在 DLL 卸载的情况，很少见。
    Stop-BridgeImage
    if (-not (Wait-Stopped 30)) { throw ("服务 " + $script:Service + " 没有停下来") }
    $killed = $true
}
if (Stop-BridgeLeftover 15) { $killed = $true }
if ($killed) { Write-Host "service-stop killed" } else { Write-Host "service-stop ok" }
exit 0
