"""多条调试通道（lane）：通道指纹和同机冲突的启发式检查。不读文件，不连接服务器。

一条通道 = 一个测试账套 + 一个开发用桥服务 + 一份 e2e 配置（每次调用用环境变量选，见 settings.py）。
加载配置时看不到别的通道，所以这里只做启发式检查：写了顶层 "lane"（2–12 位小写字母或数字）时，
下面这些名字不能停在缺省值上，而且要含账套号或通道名；否则两条通道会在同一台服务器上抢同一个
计划任务、快照、目录、服务或端口。docs/testing.md 4.5 有完整说明。
"""

from __future__ import annotations

import re
from pathlib import Path

from co.e2e.settings_parse import ConfigError

# co/e2e/e2e.example.json 的各项缺省值。改样例时一起改。
DEFAULT_SSH_USER = "u8co-ops"
DEFAULT_PORT = 18089
_DEFAULT_NAMES = {
    "server.remote_root": r"C:\ProgramData\U8Co\u8co-e2e",
    "snapshot.suffix": "_u8co_e2e_snap",
    "snapshot.task_prefix": "u8co-e2e",
    "bridge.root": r"C:\ProgramData\U8Co\u8co",
    "bridge.service_name": "u8co",
}
_TAG = re.compile(r"^[a-z0-9]{2,12}\Z")


def lane_tag(value: object) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or _TAG.fullmatch(value) is None:
        raise ConfigError("配置项 lane 不合法：只收 2–12 位小写字母或数字")
    return value


def _names(cfg) -> dict[str, str]:
    return {
        "server.remote_root": cfg.remote_root,
        "snapshot.suffix": cfg.snap_suffix,
        "snapshot.task_prefix": cfg.task_prefix,
        "bridge.root": cfg.bridge_root,
        "bridge.service_name": cfg.service_name,
    }


def fingerprint(cfg) -> dict[str, str]:
    # 同一台服务器上的每条通道，下面每一项都必须互不相同（ssh_user 见 docs/testing.md：预检不许一个登录看到别的账套）。
    return {
        "tag": cfg.lane or "-",
        "account": cfg.account,
        "ssh_user": cfg.ssh_user,
        "remote_root": cfg.remote_root,
        "snapshot_suffix": cfg.snap_suffix,
        "task_prefix": cfg.task_prefix,
        "bridge_root": cfg.bridge_root,
        "service_name": cfg.service_name,
        "bridge_port": str(cfg.bridge_port),
        "report_dir": cfg.report_dir,
    }


def fingerprint_lines(cfg) -> list[str]:
    return ["lane_" + key + " " + value for key, value in fingerprint(cfg).items()]


def _tagged(value: str, tags: tuple[str, ...]) -> bool:
    low = value.lower()
    return any(tag in low for tag in tags)


def _leaf(label: str, value: str) -> str:
    # 目录只看最后一段：C:\ProgramData\... 这类上级路径里碰巧含账套号不算数。
    if label in ("server.remote_root", "bridge.root"):
        return value.rsplit("\\", 1)[-1]
    return value


def _name_problems(cfg, tags: tuple[str, ...]) -> list[str]:
    problems = []
    for label, value in _names(cfg).items():
        if value.lower() == _DEFAULT_NAMES[label].lower():
            problems.append(f"{label} 还是缺省值 {value!r}")
        elif not _tagged(_leaf(label, value), tags):
            problems.append(f"{label} 必须含账套号或通道名（{'/'.join(tags)}）：{value!r}")
    return problems


def _local_problems(cfg, default_report: str, tags: tuple[str, ...]) -> list[str]:
    problems = []
    if cfg.bridge_port == DEFAULT_PORT:
        problems.append(f"bridge.port 还是缺省值 {DEFAULT_PORT}")
    report = str(Path(cfg.report_dir).expanduser())
    if report == str(Path(default_report).expanduser()):
        problems.append("report.dir 还是缺省目录（会话标记 session.json 在这里，两条通道会互相覆盖）")
    elif not _tagged(Path(report).name, tags):
        problems.append(f"report.dir 的最后一段必须含账套号或通道名：{report!r}")
    if cfg.ssh_user == DEFAULT_SSH_USER:
        problems.append(f"server.ssh_user 还是缺省的 {DEFAULT_SSH_USER}（每条通道要用只能访问自己账套的 Windows 登录）")
    return problems


def lane_problems(cfg, default_report: str) -> list[str]:
    # 没写通道名：就是缺省的那一条通道，不查。
    if not cfg.lane:
        return []
    tags = tuple(item for item in (cfg.account, cfg.lane) if item)
    return _name_problems(cfg, tags) + _local_problems(cfg, default_report, tags)


def check(cfg, default_report: str) -> None:
    problems = lane_problems(cfg, default_report)
    if problems:
        head = "写了 lane，按多条通道检查（docs/testing.md 4.5）："
        raise ConfigError(head + "；".join(problems))
