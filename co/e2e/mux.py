"""SSH 连接复用。套接字放在短路径里（配置 ssh.control_dir，缺省 $TMPDIR 或 /tmp），目录权限 0700。

每个进程用自己的套接字（名字带 pid 和 ssh 的 %C 连接哈希）：结束时的 ssh -O exit 只关自己的主连接，
不会切断同时在跑的另一个 session 命令或另一条通道的连接。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from co.e2e.settings import conf

# sun_path 上限 108 字节；ssh 建套接字时先在路径后追加 17 个字符的临时后缀再改名。
_LIMIT = 108 - 1 - 17
_HASH = 40


def _default_base() -> Path:
    # 目录太深时加上后缀会超长，所以缺省用 TMPDIR 或 /tmp。
    return Path(os.environ.get("TMPDIR") or "/tmp")


def prepare_control_path(root: Path | str | None = None) -> str:
    base = _default_base() if root is None else Path(root)
    folder = base / ("u8co-mux-" + str(os.getuid()))
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    # %C 由 ssh 展开成 本机、目标主机、端口、用户 的哈希（40 个十六进制字符）：进程号被别的进程重用时，
    # 用另一个 ssh 用户（另一条通道）的连接也不会接到上一个进程留下、还没退出的主连接上。
    path = str(folder / ("cm-" + str(os.getpid()) + "-%C"))
    if len(path.encode("utf-8")) - len("%C") + _HASH > _LIMIT:
        raise RuntimeError("ControlPath 过长")
    return path


def mux_options(control_path: str) -> list[str]:
    return [
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPath=" + control_path,
        "-o",
        "ControlPersist=120",
    ]


def exit_master(key: str, known_hosts: str, control_path: str) -> None:
    argv = [
        "ssh",
        "-O",
        "exit",
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
        "ControlPath=" + control_path,
        conf().ssh_target,
    ]
    try:
        subprocess.run(argv, check=False, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return
