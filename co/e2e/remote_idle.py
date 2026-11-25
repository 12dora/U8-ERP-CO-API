"""上传脚本和 e2e.json 之前的空闲检查（assert-idle）正文。不连接服务器，只拼 PowerShell 文本。"""

from __future__ import annotations

from co.e2e.settings import conf

# 上传脚本和 e2e.json 之前在服务器上确认没有打开的会话或看门狗，避免改掉正在等还原的那一份。
# 服务器上已有的 e2e.json 可能来自改过的配置（换了任务名前缀或账套）：它的计划任务或快照还在时也不覆盖，
# 否则旧的看门狗会按新配置去还原，旧账套没人还原。本通道自己的 S4U 探测任务例外：不在运行就清掉再继续。
_IDLE = r"""
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = '@ROOT@'
if ([System.IO.File]::Exists($root + '\session.json')) { throw '服务器上已有会话标记，不覆盖脚本和配置' }
$tasks = @('@TASK@', '@PROBE@')
$snaps = @()
$old = $root + '\e2e.json'
if ([System.IO.File]::Exists($old)) {
  try { $doc = [System.IO.File]::ReadAllText($old) | ConvertFrom-Json } catch { throw '服务器上旧的 e2e.json 读不了，请人工检查' }
  foreach ($task in @([string]$doc.RevertTask, [string]$doc.ProbeTask)) {
    if ($task -cnotmatch '^[A-Za-z0-9-]{3,60}$') { throw '旧 e2e.json 的任务名不合法，请人工检查' }
    $tasks += $task
  }
  $suffix = [string]$doc.SnapSuffix
  if ($suffix -cnotmatch '^_[A-Za-z0-9_]{3,40}$') { throw '旧 e2e.json 的 SnapSuffix 不合法，请人工检查' }
  foreach ($db in @([string]$doc.DataDb, [string]$doc.MetaDb)) {
    if ($db -cnotmatch '^UF(DATA_\d{3}_\d{4}|Meta_\d{3})$') { throw '旧 e2e.json 的库名不合法，请人工检查' }
    $snaps += ($db + $suffix)
  }
}
$probe = '@PROBE@'
foreach ($task in $tasks) {
  $found = @(Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue)
  if ($found.Count -eq 0) { continue }
  # 本通道的 S4U 探测任务只在 s4u-arm 运行期间存在，上次被中断（断线、Ctrl-C）时会留下：不在运行或排队就清掉。
  # 看门狗、旧配置的任务、同名多个或不在根路径的一律不动。
  $own = $task -ceq $probe -and $found.Count -eq 1 -and [string]$found[0].TaskPath -eq '\'
  if (-not $own -or @('Running', 'Queued') -contains [string]$found[0].State) { throw ('计划任务 ' + $task + ' 还在（看门狗或探测任务），不覆盖脚本和配置') }
  $state = [string]$found[0].State
  $last = '未知'
  $info = Get-ScheduledTaskInfo -TaskName $task -TaskPath '\' -ErrorAction SilentlyContinue
  if ($null -ne $info -and $null -ne $info.LastRunTime) { $last = ([datetime]$info.LastRunTime).ToString('yyyy-MM-dd HH:mm:ss') }
  Unregister-ScheduledTask -TaskName $task -TaskPath '\' -Confirm:$false
  if ($null -ne (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue)) { throw ('探测任务 ' + $task + ' 清不掉，请人工处理') }
  Write-Output ('清掉上次中断留下的探测任务 ' + $task + '（状态 ' + $state + '，上次运行 ' + $last + '）')
}
if ($snaps.Count -gt 0) {
  $list = ($snaps | ForEach-Object { "N'" + $_ + "'" }) -join ', '
  $query = 'SET NOCOUNT ON; SELECT name FROM sys.databases WHERE name IN (' + $list + ');'
  $rows = & '@SQLCMD@' -S '@SQL@' -E -b -W -h-1 -d master -Q $query 2>&1
  if ($LASTEXITCODE -ne 0) { throw ('查不了旧配置的快照：' + (@($rows) -join ' ')) }
  foreach ($row in @($rows)) {
    $text = ([string]$row).Trim()
    if ($snaps -contains $text) { throw ('旧配置的快照 ' + $text + ' 还在，先还原或人工处理，不覆盖脚本和配置') }
  }
}
"""


def idle_body() -> str:
    cfg = conf()
    body = _IDLE.replace("@ROOT@", cfg.remote_root).replace("@TASK@", cfg.revert_task)
    body = body.replace("@PROBE@", cfg.probe_task).replace("@SQLCMD@", cfg.sqlcmd)
    return body.replace("@SQL@", cfg.sql_target)
