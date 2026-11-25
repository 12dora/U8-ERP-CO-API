"""SSH 与 scp。口令不进入命令行；脚本参数只允许固定字符集。"""

from __future__ import annotations

import base64
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from co.e2e.kitpack import repo_root
from co.e2e.mux import exit_master, mux_options, prepare_control_path
from co.e2e.remote_idle import idle_body
from co.e2e.settings import conf

_SCRIPT = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.ps1\Z")
_FLAG = re.compile(r"^[A-Za-z]+\Z")
_ARG = re.compile(r"^[A-Za-z0-9_.:\\-]+\Z")
_SUBDIRS = ("", "out", "snap", "src", "kit", "kit\\bridge", "kit\\bridge\\src")
# 看门狗和 S4U 计划任务以最高权限运行 e2e 目录里的脚本，所以目录只能由 SYSTEM、Administrators 和计划任务账户写。
# 这段在上传脚本之前跑，不能 dot-source SafePath.ps1，检查写在这里：
# - 上级目录不能是联接点，所有者须是 SYSTEM、Administrators 或 TrustedInstaller，也不能让别人删除、改名、改 ACL 或取得所有权
#   （否则可以把整条路径换成自己的）。建文件夹的权限不算：别人只能建旁边的目录，换不掉本脚本建的那个。
# - 缺的上级目录和 e2e 目录由这里新建并锁上；已有的 e2e 目录要求所有者可信，锁之前和锁上之后都逐项检查里面的东西。
# - 锁目录时删掉允许名单以外账户的显式授权（例如换过计划任务账户后旧账户的修改权），锁完再核对一遍授权名单。
_ENSURE = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$PSNativeCommandUseErrorActionPreference = $false
$root = [System.IO.Path]::GetFullPath('@ROOT@')
$protected = @(@PROTECTED@)
foreach ($item in $protected) {
  $top = $item.TrimEnd('\')
  if ($root.Equals($top, [System.StringComparison]::OrdinalIgnoreCase) -or $root.StartsWith($top + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '拒绝在 U8 安装目录或其所在盘上建目录'
  }
}
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$name = '@PRINCIPAL@'
if ($name -eq '') { $principal = $me }
else { $principal = (New-Object System.Security.Principal.NTAccount($name)).Translate([System.Security.Principal.SecurityIdentifier]).Value }
$admins = @('S-1-5-18', 'S-1-5-32-544')
# 当前 SSH 账户是管理员、跑所有这些步骤，它建的上级目录（所有者可能是账户本身）也可信。
$owners = $admins + @('S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464', $me)
$trusted = $owners + @('S-1-3-0', $principal)
# e2e 目录里的东西可以属于 SYSTEM、Administrators、当前 SSH 账户（管理员）和计划任务账户。
$mine = $admins + @($me, $principal)
function Test-Reparse([string]$Path) {
  return (([System.IO.File]::GetAttributes($Path) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
}
function Get-OwnerSid([string]$Path) {
  return (Get-Acl -LiteralPath $Path).GetOwner([System.Security.Principal.SecurityIdentifier]).Value
}
function Assert-Ancestor([string]$Dir) {
  if (Test-Reparse $Dir) { throw ('拒绝联接点：' + $Dir) }
  if ($owners -notcontains (Get-OwnerSid $Dir)) { throw ('上级目录的所有者不是 SYSTEM、Administrators、TrustedInstaller 或当前账户：' + $Dir) }
  $rules = (Get-Acl -LiteralPath $Dir).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
  foreach ($rule in $rules) {
    if ($rule.AccessControlType -ne 'Allow') { continue }
    if (($rule.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) { continue }
    if ($trusted -contains $rule.IdentityReference.Value) { continue }
    # Delete、DeleteSubdirectoriesAndFiles、ChangePermissions、TakeOwnership、GENERIC_ALL
    if (([int64]$rule.FileSystemRights -band 0x100D0040) -ne 0) {
      throw ('上级目录允许 ' + $rule.IdentityReference.Value + ' 删除、改名或改权限：' + $Dir)
    }
  }
}
function Get-ExtraSids([string]$Dir, [string[]]$Allowed) {
  $extra = @()
  foreach ($rule in (Get-Acl -LiteralPath $Dir).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
    if ($rule.AccessControlType -ne 'Allow') { continue }
    if ($Allowed -notcontains $rule.IdentityReference.Value) { $extra += $rule.IdentityReference.Value }
  }
  return $extra
}
function Lock-Dir([string]$Dir, [string[]]$Extra, [string[]]$Allowed) {
  if (Test-Reparse $Dir) { throw ('拒绝联接点：' + $Dir) }
  # 刚建出的目录所有者不可信，就是检查与新建之间被别人抢先建的，不去接管。
  if ($owners -notcontains (Get-OwnerSid $Dir)) { throw ('目录的所有者不可信，可能被别人抢先建出，请人工检查：' + $Dir) }
  $grants = @('*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F') + $Extra
  $out = & icacls.exe $Dir /inheritance:r /grant:r @grants /Q 2>&1
  if ($LASTEXITCODE -ne 0) { throw ('锁目录权限失败：' + $Dir + ' ' + (@($out) -join ' ')) }
  # /grant:r 只替换列出的账户，别的账户的显式授权要逐个删掉。
  foreach ($sid in @(Get-ExtraSids $Dir $Allowed | Select-Object -Unique)) {
    $out = & icacls.exe $Dir /remove:g ('*' + $sid) /Q 2>&1
    if ($LASTEXITCODE -ne 0) { throw ('删除多余授权失败：' + $Dir + ' ' + $sid + ' ' + (@($out) -join ' ')) }
  }
  $out = & icacls.exe $Dir /setowner '*S-1-5-32-544' /Q 2>&1
  if ($LASTEXITCODE -ne 0) { throw ('设置目录所有者失败：' + $Dir + ' ' + (@($out) -join ' ')) }
  # 锁完再核对：抓住锁的过程中别人（例如抢先建出目录的人）又加的授权。
  $left = @(Get-ExtraSids $Dir $Allowed)
  if ($left.Count -gt 0) { throw ('目录上还有名单外账户的授权，请人工检查：' + $Dir + ' ' + ($left -join ' ')) }
}
function Read-Entry([string]$Item, [bool]$Snap) {
  # 并行的 session 命令会删掉自己在 out 下的临时文件：枚举之后才消失的条目返回 $null，还在的照样报错。
  try {
    $reparse = Test-Reparse $Item
    $owner = ''
    if (-not $Snap) { $owner = Get-OwnerSid $Item }
    $children = @()
    if ((-not $reparse) -and [System.IO.Directory]::Exists($Item)) { $children = [System.IO.Directory]::GetFileSystemEntries($Item) }
    return @{ reparse = $reparse; owner = $owner; children = $children }
  } catch {
    if ([System.IO.File]::Exists($Item) -or [System.IO.Directory]::Exists($Item)) { throw }
    return $null
  }
}
function Assert-Contents([string]$Root) {
  # 旧版本建的目录可能还继承过宽的权限：锁上之后逐项确认没有联接点、没有别人的文件。snap 里是 SQL 服务写的快照文件，只查联接点。
  $queue = New-Object System.Collections.Generic.Queue[string]
  foreach ($entry in [System.IO.Directory]::GetFileSystemEntries($Root)) { $queue.Enqueue($entry) }
  while ($queue.Count -gt 0) {
    $item = $queue.Dequeue()
    $snap = $item.StartsWith($Root + '\snap\', [System.StringComparison]::OrdinalIgnoreCase)
    $info = Read-Entry $item $snap
    if ($null -eq $info) { continue }
    if ($info.reparse) { throw ('e2e 目录里有联接点：' + $item) }
    if (-not $snap -and $mine -notcontains $info.owner) {
      throw ('e2e 目录里有别人的文件，请人工检查后删除：' + $item)
    }
    foreach ($child in $info.children) { $queue.Enqueue($child) }
  }
}
$chain = New-Object System.Collections.Generic.List[string]
$up = $root
while (-not [string]::IsNullOrEmpty($up)) { $chain.Insert(0, $up); $up = [System.IO.Path]::GetDirectoryName($up) }
foreach ($dir in $chain) {
  if ($dir -eq $root) { break }
  if ([System.IO.Directory]::Exists($dir)) { Assert-Ancestor $dir; continue }
  [void][System.IO.Directory]::CreateDirectory($dir)
  Lock-Dir $dir @() $admins
  Assert-Ancestor $dir
}
$existed = [System.IO.Directory]::Exists($root)
if ($existed) {
  if (Test-Reparse $root) { throw ('e2e 目录是联接点：' + $root) }
  if (($admins + @($me)) -notcontains (Get-OwnerSid $root)) { throw ('e2e 目录的所有者不是 SYSTEM、Administrators 或当前账户，请人工检查后删除：' + $root) }
  # 锁（会向下传播权限）之前先查一遍联接点和所有者，不让继承权限顺着别人放的联接点传出去。
  Assert-Contents $root
} else {
  [void][System.IO.Directory]::CreateDirectory($root)
}
Lock-Dir $root @('*' + $principal + ':(OI)(CI)M') ($admins + @($principal))
# 新建的也查：检查与新建之间别人可能抢先建出目录、放进东西。
Assert-Contents $root
foreach ($path in @(@PATHS@)) {
  $full = [System.IO.Path]::GetFullPath($path)
  $same = $full.Equals($root, [System.StringComparison]::OrdinalIgnoreCase)
  if (-not $same -and -not $full.StartsWith($root + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '拒绝创建名单外目录'
  }
  [void][System.IO.Directory]::CreateDirectory($full)
}
"""


def _quote(text: str) -> str:
    if "'" in text:
        raise ValueError("路径含单引号")
    return "'" + text + "'"


def ensure_body() -> str:
    cfg = conf()
    paths = [cfg.remote(item) if item else cfg.remote_root for item in _SUBDIRS]
    body = _ENSURE.replace("@ROOT@", cfg.remote_root)
    body = body.replace("@PRINCIPAL@", cfg.windows_principal)
    body = body.replace("@PROTECTED@", ", ".join(_quote(item) for item in cfg.protected))
    return body.replace("@PATHS@", ", ".join(_quote(item) for item in paths))


def _fixed(command: str) -> str:
    cfg = conf()
    kit = cfg.remote("kit", "bridge")
    exe = kit + "\\out\\u8co-bridge.exe"
    if command == "build":
        # 可选的附加源码目录（桥源码旁的 private 子目录，存在时 kitpack 打进 kit\bridge\private，用 -ExtraSourceDir 一并编译）。
        has_private = any((repo_root() / "co" / "bridge" / "private").glob("*.cs"))
        extra = " -ExtraSourceDir " + _quote(kit + "\\private") if has_private else ""
        return "& " + _quote(kit + "\\build.ps1") + " -U8Home " + _quote(cfg.u8_home) + extra
    if command == "selftest":
        return "& " + _quote(exe) + " --selftest --u8-home " + _quote(cfg.u8_home)
    # 桥的运行目录不是缺省值时，--check-config 要读配置里的那一份。
    root = "" if cfg.bridge_root == r"C:\ProgramData\U8Co\u8co" else " --root " + _quote(cfg.bridge_root)
    return "& " + _quote(exe) + " --check-config" + root


class RemoteError(RuntimeError):
    def __init__(self, message: str, output: str) -> None:
        super().__init__(message)
        self.output = output


@dataclass
class RemoteResult:
    code: int
    out: str


def encode_pwsh(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


# 远端控制台默认是 GBK,进度记录会以 CLIXML 混进输出;统一改成 UTF-8、关掉进度。
_PRELUDE = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$ProgressPreference = 'SilentlyContinue'; "
)


# Win32-OpenSSH 缺省经 cmd.exe /c 执行远端命令，命令行上限 8191 字符；留出余量。
CMDLINE_LIMIT = 8000
_LAUNCH = "pwsh -NoProfile -NonInteractive -EncodedCommand "
# 长脚本不上命令行：命令行上只有这段固定的引导，它从 stdin 读 base64(UTF-8) 的正文，
# 再 dot-source 执行（与直接 -EncodedCommand 一样在顶层作用域，throw 和 exit 的退出码不变）。
# 正文最后一行。空的或截断的 stdin 没有它，引导拒绝执行（否则空正文会当成功，守卫形同虚设）。按序数比较，不受区域设置影响。
_BODY_END = "# u8co-body-end"
_STDIN_STUB = (
    "try { $u8coIn = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), [System.Text.Encoding]::ASCII); "
    "$u8coBody = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($u8coIn.ReadToEnd().Trim())) } "
    "catch { [Console]::Error.WriteLine('读不了 stdin 上的脚本'); exit 1 }; "
    "if ($null -eq $u8coBody -or -not $u8coBody.EndsWith([string][char]10 + '@END@', [System.StringComparison]::Ordinal)) "
    "{ [Console]::Error.WriteLine('stdin 上的脚本不完整'); exit 1 }; "
    ". ([scriptblock]::Create($u8coBody))"
).replace("@END@", _BODY_END)


def remote_launch(body: str) -> str:
    """正文直接编码在命令行上。只给自己要用 stdin 的短脚本（comprobe 启动、SQL 工人）用。"""
    command = _LAUNCH + encode_pwsh(_PRELUDE + body)
    if len(command) >= CMDLINE_LIMIT:
        raise ValueError("远端命令行过长，改用 stdin_launch")
    return command


def stdin_launch(body: str) -> tuple[str, bytes]:
    """返回（命令行，stdin 内容）。命令行长度固定，与正文长短无关。"""
    # -EncodedCommand 在最后一条命令失败（$? 为假，例如脚本 exit 非 0）时退出码为 1；dot-source 会吞掉它，这里补上。
    text = body + "\nif (-not $?) { exit 1 }\n" + _BODY_END
    payload = base64.b64encode(text.encode("utf-8")) + b"\n"
    return _LAUNCH + encode_pwsh(_PRELUDE + _STDIN_STUB), payload


def decode_output(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="replace")


def script_call(name: str, named: dict[str, str] | None = None) -> str:
    if _SCRIPT.fullmatch(name) is None:
        raise ValueError("脚本名不合法")
    parts = ["& '" + conf().remote_root + "\\" + name + "'"]
    for key, value in (named or {}).items():
        if _FLAG.fullmatch(key) is None or _ARG.fullmatch(value) is None:
            raise ValueError("参数不合法")
        parts.append("-" + key)
        parts.append("'" + value + "'")
    return " ".join(parts)


def fixed_call(command: str) -> str:
    if command not in ("build", "selftest", "check"):
        raise ValueError("命令不在允许列表")
    return _fixed(command)


def win_to_scp(path: str) -> str:
    prefix = conf().remote_root
    if path != prefix and not path.startswith(prefix + "\\"):
        raise ValueError("拒绝传输 e2e 目录以外的文件")
    if ".." in path.split("\\"):
        raise ValueError("拒绝 .. 段")
    return "/" + path.replace("\\", "/")


def ssh_argv(key: str, known_hosts: str, extra: list[str] | None = None) -> list[str]:
    argv = [
        "ssh",
        "-i",
        key,
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "UserKnownHostsFile=" + known_hosts,
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "BatchMode=yes",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "ServerAliveInterval=30",
    ]
    if extra:
        argv.extend(extra)
    argv.append(conf().ssh_target)
    return argv


class Remote:
    def __init__(self, key: str, known_hosts: str) -> None:
        self.key = key
        self.known_hosts = known_hosts
        self.control_path = prepare_control_path(conf().control_dir or None)
        self._sql = None
        self._pool = None
        _LIVE.append(self)

    def mux_args(self) -> list[str]:
        return mux_options(self.control_path)

    def sql_channel(self):
        # 开着通道池时，别的线程（并行跑的用例）各用自己的一条；建池的线程照旧用这一条。
        pool = self._pool
        if pool is not None and threading.get_ident() != pool.owner:
            return pool.channel()
        if self._sql is None:
            from co.e2e.sqlssh import open_channel

            self._sql = open_channel(self)
        return self._sql

    def open_sql_pool(self) -> None:
        from co.e2e.sqlchan import SqlPool
        from co.e2e.sqlssh import open_channel

        if self._pool is None:
            self._pool = SqlPool(lambda: open_channel(self))

    def close_sql_pool(self) -> None:
        pool = self._pool
        self._pool = None
        if pool is not None:
            pool.close()

    def close_sql(self) -> None:
        self.close_sql_pool()
        channel = self._sql
        self._sql = None
        if channel is None:
            return
        try:
            channel.close()
        except (OSError, subprocess.TimeoutExpired):
            return

    def close_master(self) -> None:
        exit_master(self.key, self.known_hosts, self.control_path)

    def run_body(self, body: str, timeout: int) -> RemoteResult:
        argv = ssh_argv(self.key, self.known_hosts, self.mux_args())
        command, payload = stdin_launch(body)
        argv.append(command)
        return _run(argv, timeout, payload)

    def run_script(self, name: str, named: dict[str, str] | None, timeout: int) -> RemoteResult:
        return self.run_body(script_call(name, named), timeout)

    def run_fixed(self, command: str, timeout: int) -> RemoteResult:
        return self.run_body(fixed_call(command), timeout)

    def ensure_dirs(self) -> RemoteResult:
        return self.run_body(ensure_body(), 60)

    def assert_idle(self) -> RemoteResult:
        return self.run_body(idle_body(), 60)

    def upload(self, local: Path, remote_win: str) -> None:
        target = conf().ssh_target + ":" + win_to_scp(remote_win)
        argv = _scp_argv(self.key, self.known_hosts, self.mux_args()) + [str(local), target]
        result = _run(argv, 120)
        if result.code != 0:
            raise RemoteError("上传失败 " + local.name, result.out)

    def upload_many(self, paths: list[Path], remote_dir_win: str) -> None:
        """一次 scp 把多个文件传进同一个远端目录，文件名不变。"""
        if not paths:
            return
        target = conf().ssh_target + ":" + win_to_scp(remote_dir_win) + "/"
        argv = _scp_argv(self.key, self.known_hosts, self.mux_args()) + [str(item) for item in paths] + [target]
        result = _run(argv, 300)
        if result.code != 0:
            raise RemoteError("上传失败 " + str(len(paths)) + " 个文件", result.out)

    def download(self, remote_win: str, local: Path) -> None:
        source = conf().ssh_target + ":" + win_to_scp(remote_win)
        argv = _scp_argv(self.key, self.known_hosts, self.mux_args()) + [source, str(local)]
        result = _run(argv, 120)
        if result.code != 0:
            raise RemoteError("下载失败", result.out)


def _scp_argv(key: str, known_hosts: str, extra: list[str] | None = None) -> list[str]:
    argv = [
        "scp",
        "-i",
        key,
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "UserKnownHostsFile=" + known_hosts,
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "BatchMode=yes",
        "-o",
        "LogLevel=ERROR",
    ]
    if extra:
        argv.extend(extra)
    return argv


_LIVE: list[Remote] = []


def release_all() -> None:
    items = list(_LIVE)
    _LIVE.clear()
    for item in items:
        item.close_sql()
    if not items:
        return
    items[0].close_master()


def _run(argv: list[str], timeout: int, payload: bytes | None = None) -> RemoteResult:
    try:
        completed = subprocess.run(
            argv,
            input=payload,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        out = decode_output(exc.stdout) + decode_output(exc.stderr)
        raise RemoteError("远程命令超时", out) from None
    out = decode_output(completed.stdout) + decode_output(completed.stderr)
    return RemoteResult(completed.returncode, out)
