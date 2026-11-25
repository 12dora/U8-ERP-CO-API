"""comprobe 在服务器上的目录、编译和启动。路径来自 e2e 配置；只在 <e2e 目录>\\comprobe 里写。"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from co.e2e.remote import ensure_body as locked_root
from co.e2e.settings import conf

# 先跑 e2e 的 ensure_body()：它锁上 <e2e 目录>（去掉继承，只留 SYSTEM、Administrators 全权和计划任务账户修改，
# 所有者 Administrators；已有目录的所有者不可信就拒绝）。comprobe 目录只在锁好的 e2e 目录里建，继承它的权限。
# 下面这段接在 e2e 那段后面，沿用其中的 $root。
_ENSURE = r"""
$prefix = [System.IO.Path]::GetFullPath('@PROBE@')
if (-not $prefix.StartsWith($root + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
  throw 'comprobe 目录不在 e2e 目录里'
}
foreach ($item in @(@PROTECTED@)) {
  $top = $item.TrimEnd('\')
  if ($prefix.Equals($top, [System.StringComparison]::OrdinalIgnoreCase) -or $prefix.StartsWith($top + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '拒绝在 U8 安装目录或其所在盘上建目录'
  }
}
foreach ($path in @('@PROBE@', '@PROBE@\src', '@PROBE@\out')) {
  $full = [System.IO.Path]::GetFullPath($path)
  $ok = $full.Equals($prefix, [System.StringComparison]::OrdinalIgnoreCase) -or $full.StartsWith($prefix + '\', [System.StringComparison]::OrdinalIgnoreCase)
  if (-not $ok) { throw '拒绝创建名单外目录' }
  [void][System.IO.Directory]::CreateDirectory($full)
}
"""
_LAUNCH = r"""
$ErrorActionPreference = 'Stop'
$exe = '@ROOT@\out\comprobe.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw '找不到 comprobe.exe' }
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $exe
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
$psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
$psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
$proc = [System.Diagnostics.Process]::Start($psi)
$stdin = [Console]::OpenStandardInput()
$stdin.CopyTo($proc.StandardInput.BaseStream)
$proc.StandardInput.Close()
$out = $proc.StandardOutput.ReadToEnd()
$err = $proc.StandardError.ReadToEnd()
$proc.WaitForExit()
[Console]::Out.Write($out)
if ($err) { [Console]::Error.Write($err) }
exit $proc.ExitCode
"""


def probe_root() -> str:
    return conf().remote("comprobe")


def _quoted(items: tuple[str, ...]) -> str:
    for item in items:
        if "'" in item:
            raise ValueError("路径含单引号")
    return ", ".join("'" + item + "'" for item in items)


def ensure_body() -> str:
    body = _ENSURE.replace("@PROTECTED@", _quoted(conf().protected))
    return locked_root() + body.replace("@PROBE@", probe_root())


def build_body() -> str:
    return "& '" + probe_root() + "\\build.ps1'"


def launch_body() -> str:
    return _LAUNCH.replace("@ROOT@", probe_root())


def probe_config() -> dict:
    # comprobe.exe 旁边的 comprobe.json。探测程序只登录这个账套，且要求它就在桥的 allowedAccounts 里。
    cfg = conf()
    return {
        "account": cfg.account,
        "year": cfg.year,
        "bridgeRoot": cfg.bridge_root,
        "u8Home": cfg.u8_home,
        "probeRoot": probe_root(),
        "protected": list(cfg.protected),
    }


def upload_sources() -> None:
    from co.e2e.flow import make_remote

    remote = make_remote()
    folder = Path(__file__).resolve().parent / "comprobe"
    sources = sorted(folder.glob("*.cs"))
    if not sources:
        raise RuntimeError("没有 C# 源文件")
    root = probe_root()
    for path in sources:
        remote.upload(path, root + "\\src\\" + path.name)
    remote.upload(folder / "build.ps1", root + "\\build.ps1")
    remote.upload(folder / "comprobe.exe.config", root + "\\comprobe.exe.config")
    with tempfile.TemporaryDirectory(prefix="u8co-probe-") as tmp:
        remote.upload(_write_probe_config(Path(tmp)), root + "\\comprobe.json")


def _write_probe_config(folder: Path) -> Path:
    # 只有账套号、年度和路径，没有口令。build.ps1 把它复制到 out\comprobe.json。
    local = folder / "comprobe.json"
    local.write_text(json.dumps(probe_config(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return local


def remote_ssh(body: str, payload: bytes | None, timeout: int) -> tuple[int, str]:
    from co.e2e.flow import make_remote
    from co.e2e.remote import decode_output, remote_launch, ssh_argv, stdin_launch

    remote = make_remote()
    argv = ssh_argv(remote.key, remote.known_hosts)
    # 自己不用 stdin 的正文（建目录那段很长）经 stdin 送过去，命令行不超 cmd.exe 的长度上限；
    # 启动 comprobe 要把 stdin 转给它，正文短，直接放在命令行上。
    if payload is None:
        command, payload = stdin_launch(body)
    else:
        command = remote_launch(body)
    argv.append(command)
    try:
        done = subprocess.run(argv, input=payload, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        text = decode_output(exc.stdout) + decode_output(exc.stderr)
        raise RuntimeError("远程命令超时 " + text[:200]) from None
    return done.returncode, decode_output(done.stdout) + decode_output(done.stderr)
