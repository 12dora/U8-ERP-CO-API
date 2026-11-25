"""测试账套端到端的步骤和预检判断。这里不连接服务器。站点值来自 co.e2e.settings。"""

from __future__ import annotations

import re

from co.e2e.settings import MIN_FREE_BYTES, conf
from co.e2e.settings_parse import drive

# 看门狗上下限是安全边界，不进配置；配置只选缺省分钟数，仍会被夹在这个范围里。
WATCHDOG_MIN_FLOOR = 10
WATCHDOG_MIN_CEIL = 120

_READ = (
    ("preflight", "SSH、服务在跑、测试账套两个库在线且 MULTI_USER、无残留快照或会话、权限与快照盘剩余空间"),
    ("fingerprint_before", "每个用户表的行数，以及 CHECKSUM_AGG(BINARY_CHECKSUM(*))"),
)
_WRITE = (
    ("s4u_preflight", "task", "S4U 计划任务执行 SELECT 1；失败则在写库之前停止"),
    ("snapshot", "write", "创建测试账套两个库的数据库快照"),
    ("watchdog", "task", "登记一次性还原任务，分钟数限制在 10 到 120"),
    ("pick_docs", "read", "挑选未审核、未走审批流的销售订单和蓝字发货单"),
    ("bridge", "process", "编译桥并换上已安装服务的程序和 exe.config，不改 config.json 和密钥"),
    ("http_tests", "write", "健康检查、错误签名、拒绝名单外账套、登录、审核弃审和审批流回归"),
    ("revert", "write", "停服务，从快照还原，再启动服务，取消看门狗"),
    ("fingerprint_after", "read", "再取指纹，与快照时点逐表比较"),
)


def clamp_minutes(value: int) -> int:
    if value < WATCHDOG_MIN_FLOOR:
        return WATCHDOG_MIN_FLOOR
    if value > WATCHDOG_MIN_CEIL:
        return WATCHDOG_MIN_CEIL
    return value


def secret_text_problem(mode: int, text: str, exists: bool) -> str:
    if not exists:
        return "找不到服务密钥"
    if mode & 0o077:
        return "服务密钥权限过宽"
    if re.fullmatch(r"[0-9a-f]{64}", text.strip()) is None:
        return "服务密钥格式不对"
    return ""


def watchdog_default() -> int:
    return clamp_minutes(conf().run_minutes)


def session_watchdog_default() -> int:
    return clamp_minutes(conf().session_minutes)


def snapshot_name(database: str) -> str:
    cfg = conf()
    if database not in cfg.databases:
        raise ValueError("只允许测试账套的两个库")
    return database + cfg.snap_suffix


def build_plan(dry_run: bool) -> list[dict[str, str]]:
    steps = [{"name": name, "effect": "read", "text": text} for name, text in _READ]
    if dry_run:
        return steps
    for name, effect, text in _WRITE:
        steps.append({"name": name, "effect": effect, "text": text})
    return steps


def _flag(value: object, unknown: bool) -> bool:
    if value is True or value is False:
        return bool(value)
    if value in (0, "0"):
        return False
    if value in (1, "1"):
        return True
    return unknown


def _free_bytes(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return -1
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return -1


def interpret_preflight(info: dict) -> dict:
    found = []
    for row in info.get("databases") or []:
        if isinstance(row, dict) and row.get("name"):
            found.append(row)
    snaps = [str(item) for item in (info.get("snapshots") or [])]
    return {
        "databases": found,
        "snapshots": snaps,
        "is_sysadmin": _flag(info.get("is_sysadmin"), unknown=True),
        "create_any_database": _flag(info.get("create_any_database"), unknown=False),
        "free_bytes": _free_bytes(info.get("free_bytes")),
        "volume": info.get("volume"),
        "watchdog_task": bool(info.get("watchdog_task")),
        "s4u_task": bool(info.get("s4u_task")),
        "bridge_config_exists": bool(info.get("bridge_config_exists")),
        "bridge_secret_exists": bool(info.get("bridge_secret_exists")),
        "service_exists": bool(info.get("service_exists")),
        "service_running": info.get("service_running") is True,
        "port_busy": bool(info.get("port_busy")),
        "audit_log_readable": info.get("audit_log_readable") is True,
        "session_marker": info.get("session_marker") is True,
        "expected_owner": str(info.get("expected_owner") or ""),
    }


def _check_databases(info: dict, problems: list[str]) -> None:
    found = {row["name"]: row for row in info["databases"]}
    names = conf().databases
    owner = _expected_owner(info)
    for name in names:
        row = found.get(name)
        if row is None:
            problems.append(f"缺少数据库 {name}")
            continue
        if row.get("state") != "ONLINE":
            problems.append(f"{name} 不是 ONLINE")
        if row.get("access") != "MULTI_USER":
            problems.append(f"{name} 不是 MULTI_USER")
        if not owner or str(row.get("owner") or "").lower() != owner.lower():
            problems.append(f"{name} 的所有者不是运行 e2e 的 Windows 账户")
    extra = [name for name in found if name not in names]
    if extra:
        problems.append("预检返回了名单外的库")


def _expected_owner(info: dict) -> str:
    # 配置里写了 windows_principal 就按它；否则用 preflight.ps1 报的「本机名\\SSH 用户」。
    return conf().windows_principal or str(info.get("expected_owner") or "")


def _check_rights(info: dict, problems: list[str]) -> None:
    if info["is_sysadmin"] is not False:
        problems.append("运行 e2e 的 Windows 账户必须不是 sysadmin")
    if info["create_any_database"] is not True:
        problems.append("缺少 CREATE ANY DATABASE")


def _check_space(info: dict, problems: list[str]) -> None:
    if info["free_bytes"] < MIN_FREE_BYTES:
        problems.append("快照所在卷剩余空间不足 2 GiB")
    want = drive(conf().remote_root)
    if str(info["volume"] or "").upper() != want:
        problems.append("快照目录不在 " + want)


def _check_installed(info: dict, problems: list[str]) -> None:
    if not info["bridge_config_exists"] or not info["bridge_secret_exists"]:
        problems.append("已安装服务缺少 config.json 或 secret.hex")
    if not info["service_exists"]:
        problems.append("未注册服务 " + conf().service_name)
    elif not info["service_running"]:
        problems.append("服务 " + conf().service_name + " 未在运行")
    if not info["port_busy"]:
        problems.append(str(conf().bridge_port) + " 端口未在监听")
    if not info["audit_log_readable"]:
        problems.append("读不到已安装服务的审计日志")


def _as_list(value: object) -> list[str] | None:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value]
    return None


def _check_accounts(info: dict, problems: list[str]) -> None:
    # 桥除本通道账套外还放行别的账套时，那些账套必须都在 readOnlyAccounts 里（桥对它们拒绝一切写入），
    # 本通道账套自己不能只读。例：一个桥同时服务测试账套和只读开放的其他账套。
    code = conf().account
    allowed = _as_list(info.get("allowed_accounts"))
    read_only = _as_list(info.get("read_only_accounts")) or []
    extra = [acc for acc in (allowed or []) if acc != code]
    ok = allowed is not None and code in allowed and code not in read_only and all(acc in read_only for acc in extra)
    if not ok:
        problems.append(f'config.json 的 allowedAccounts 必须是 ["{code}"]，其余账套须都在 readOnlyAccounts 里')


def _check_binding(info: dict, problems: list[str]) -> None:
    # 服务名、端口都只是配置里的值：确认服务 binPath 正是本通道的桥，桥 config.json 的端口就是 bridge.port，
    # 否则预检、调用和部署后的健康检查可能落到别的调试通道的桥上。旧版预检脚本没有这两项时也拒绝。
    if "service_root_problem" not in info or "listen_port" not in info:
        problems.append("没有核对桥服务的 binPath 和监听端口（服务器上的脚本是旧版本？）")
        return
    problem = str(info.get("service_root_problem") or "")
    if problem:
        problems.append(problem[:300])
    port = info.get("listen_port")
    if port != conf().bridge_port:
        problems.append(f"桥 config.json 的 listenPrefix 端口是 {port}，不是配置的 bridge.port {conf().bridge_port}")


def _check_foreign_access(info: dict, problems: list[str]) -> None:
    found = _as_list(info.get("foreign_db_access"))
    if found is None:
        problems.append("没有核对其他 U8 库的访问权")
        return
    if found:
        problems.append("运行 e2e 的 Windows 账户能访问名单外的 U8 库")


def _check_leftovers(info: dict, problems: list[str]) -> None:
    if info["snapshots"]:
        problems.append("已有快照，本运行不会覆盖或删除：" + ",".join(info["snapshots"]))
    if info["watchdog_task"]:
        problems.append("计划任务 " + conf().revert_task + " 已存在")
    if info["s4u_task"]:
        problems.append("计划任务 " + conf().probe_task + " 已存在")
    if info["session_marker"]:
        problems.append("已有调试会话标记，请先 session close")


def preflight_problems(info: dict) -> list[str]:
    data = interpret_preflight(info)
    problems: list[str] = []
    _check_databases(data, problems)
    _check_rights(data, problems)
    _check_space(data, problems)
    _check_installed(data, problems)
    _check_leftovers(data, problems)
    _check_accounts(info, problems)
    _check_foreign_access(info, problems)
    _check_binding(info, problems)
    return problems


def preflight_warnings(info: dict) -> list[str]:
    """只提示、不拦：还原时用来释放单用户槽位的签名过程（scripts/lane/install-release-proc.sql）。"""
    value = info.get("release_proc")
    if value is True:
        return []
    if value is None:
        why = "预检没有核对（服务器上的 preflight.ps1 是旧版本？）"
    else:
        why = "通道登录调用不了"
    return [
        "警告：master.dbo.u8co_release_db " + why + "；还原时单用户槽位被 U8 应用服务器的连接占住，"
        "只能等它断开后重试。请 sysadmin 跑一次 scripts/lane/install-release-proc.sql"
    ]
