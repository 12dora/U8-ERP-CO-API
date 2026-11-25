#Requires -Version 7.4
#Requires -PSEdition Core
# session close --keep：不还原，只删掉本次的两个快照，账套保留造数后的状态。
# -Mode check：取消看门狗之前核对，不删任何东西（也证明服务器上有这个脚本）。看门狗必须还在、done 必须不在。
# -Mode drop：看门狗必须已取消（done 已写、计划任务已不在），再核对一遍后删快照。
# 两种模式都要求：e2e.json 的 Seedable 为 true；两个快照都在、名字和源库对；没有还原会话内的指纹文件；两个库都是 MULTI_USER。
# 少一个快照、有 fp-inrevert、库不在多用户，都说明看门狗可能已经还原过一部分，拒绝。
param(
    # 必须写明模式：旧版脚本没有这个参数，漏传时不能默认去删。
    [Parameter(Mandatory = $true)][ValidateSet("check", "drop")]
    [string]$Mode
)
$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"
. (Join-Path $PSScriptRoot "common.ps1")

function Assert-Seedable {
    if ($script:Cfg.Seedable -ne $true) { throw "e2e.json 的 Seedable 不是 true，拒绝保留造数结果" }
}

function Assert-Armed {
    Assert-DoneAbsent
    $task = Get-ScheduledTask -TaskName ([string]$script:Cfg.RevertTask) -ErrorAction SilentlyContinue
    if ($null -eq $task) { throw "看门狗任务不在，拒绝保留（可能已触发还原）" }
}

function Assert-Disarmed {
    $done = Assert-E2EPath (Get-E2EPath "done")
    if (-not [System.IO.File]::Exists($done)) { throw "看门狗还没取消，拒绝删除快照" }
    $task = Get-ScheduledTask -TaskName ([string]$script:Cfg.RevertTask) -ErrorAction SilentlyContinue
    if ($null -ne $task) { throw "看门狗任务仍在，拒绝删除快照" }
}

function Assert-NoInRevert([string]$Database) {
    $path = Assert-E2EPath (Get-E2EPath ("out\fp-inrevert-" + $Database + ".json"))
    if ([System.IO.File]::Exists($path)) { throw ("有还原会话内的指纹 " + $Database + "，看门狗可能还原过，拒绝") }
}

function Assert-MultiUser([string]$Database) {
    Assert-AllowedDb $Database
    $query = "SET NOCOUNT ON; SELECT user_access_desc FROM sys.databases WHERE name = N'" + $Database + "';"
    $got = ""
    foreach ($line in @(Get-SqlText "master" $query)) {
        $text = ([string]$line).Trim()
        if ($text.Length -gt 0) { $got = $text }
    }
    if ($got -ne "MULTI_USER") { throw ($Database + " 不是 MULTI_USER（" + $got + "），拒绝") }
}

# 只核对名字和源库，不查剩余空间（删快照只会腾出空间）。
function Assert-KeepPair([string]$Database) {
    $snap = Get-SnapName $Database
    Assert-SnapPair $Database $snap
    Assert-SnapPresent $snap
    Assert-SnapSource $Database $snap
    Assert-NoInRevert $Database
    Assert-MultiUser $Database
    return $snap
}

Assert-Seedable
$dataSnap = Assert-KeepPair (Get-DataDb)
$metaSnap = Assert-KeepPair (Get-MetaDb)
if ($Mode -eq "check") {
    Assert-Armed
    Write-Host "snapshot-drop check ok"
    exit 0
}
Assert-Disarmed
Drop-Snap $dataSnap
Write-Host ("已删除快照 " + $dataSnap)
Drop-Snap $metaSnap
Write-Host ("已删除快照 " + $metaSnap)
Write-Host "snapshot-drop ok"
