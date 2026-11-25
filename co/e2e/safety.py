"""还原窗口的纯判断。不连接服务器。"""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from co.e2e.settings import conf


def _zone() -> ZoneInfo:
    return ZoneInfo(conf().timezone)


def text_list(value: object) -> list[str]:
    # ConvertTo-Json 会把只有一个元素的数组收成字符串。
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def unique_report_dir(root: Path, prefix: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for seq in range(20):
        path = root / f"{prefix}-{seq:02d}-{secrets.token_hex(3)}"
        try:
            path.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return path
    raise RuntimeError("报告目录无法创建")


def watchdog_running(status: dict) -> bool:
    return str(status.get("watchdog_state") or "") == "Running"


def start_block_reason(status: dict) -> str:
    if watchdog_running(status):
        return "看门狗正在还原，不启动服务"
    if status.get("databases_busy") is True:
        return "库不在多用户状态，不启动服务"
    return ""


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_zone())
    return value


def _parse_next(text: str) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat(text))
    except ValueError:
        return None


def minutes_left(next_run: str, now: datetime | None = None) -> str:
    if next_run in ("", "none"):
        return "无"
    if next_run == "fired":
        return "已触发"
    when = _parse_next(next_run)
    if when is None:
        return "未知"
    current = _aware(now or datetime.now(_zone()))
    left = int((when - current).total_seconds() // 60)
    if left < 0:
        return "0"
    return str(left)


def _snaps_ready(status: dict) -> bool:
    found = set(text_list(status.get("snapshots")))
    for name in conf().snapshots:
        if name not in found:
            return False
    return True


def _minutes_until(next_run: str, now: datetime) -> float | None:
    when = _parse_next(next_run)
    if when is None:
        return None
    return (_aware(when) - _aware(now)).total_seconds() / 60


def _time_block(status: dict, now: datetime) -> str:
    state = str(status.get("watchdog_state") or "none")
    next_run = str(status.get("next_run") or "none")
    if state in ("", "none") or next_run == "none":
        return "看门狗不在，拒绝执行"
    if next_run in ("", "fired"):
        return "看门狗已触发，拒绝执行"
    left = _minutes_until(next_run, now)
    if left is None:
        return "看门狗时间无法解析，拒绝执行"
    if left < 5:
        return "看门狗剩余不足 5 分钟"
    return ""


def window_block_reason(status: dict, now: datetime) -> str:
    # 与 session-sql.ps1 / session-gate.ps1 的 Assert-LiveWindow 同一条规则。
    if not _snaps_ready(status):
        return "快照不在，拒绝执行"
    if status.get("done"):
        return "看门狗已还原，之后的写入未还原"
    return _time_block(status, now)


def msgcenter_warning(opened: dict | None, closed: dict | None) -> str:
    if not opened:
        return ""
    if not closed:
        return "警告：结束时没能再次读取 MsgCenter.db"
    if _sig(opened) != _sig(closed):
        return "警告：" + conf().u8_home + "\\MessageCenter\\MsgCenter.db 发生变化（sha256、size、mtime）"
    return ""


def _sig(doc: dict) -> tuple[str, str, str]:
    return (str(doc.get("sha256") or ""), str(doc.get("size") or ""), str(doc.get("mtime") or ""))


def revert_failure_text(output: str) -> str:
    for line in output.splitlines():
        text = line.strip()
        if "看门狗已在" in text or "已无快照" in text or "之后的写入未还原" in text or "单用户模式占住" in text:
            return text[:300]
    return ""
