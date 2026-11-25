"""延长调试会话的看门狗：把到点时刻往后推，会话不关。会话从打开起最长 240 分钟。

服务器上 watchdog-extend.ps1 原地改看门狗任务的触发时刻（不删不建，任何时刻都有看门狗），并按服务器会话标记
里的打开时间再核对一次 240 分钟上限。sql、call、deploy、reset 的窗口检查读的是任务的下次运行时间，改完即生效；
两边会话标记另记 deadline、extends，watchdog_min 改成从打开到新到点的分钟数（报告里看门狗时长随之更新）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from co.e2e.flow import make_remote
from co.e2e.guard import fetch_status
from co.e2e.markers import marker_owner, read_linux_marker, write_linux_marker
from co.e2e.remote import Remote, RemoteError
from co.e2e.safety import text_list
from co.e2e.settings import conf

EXTEND_DEFAULT = 60
EXTEND_FLOOR = 10
EXTEND_CEIL = 120
SESSION_MAX_MIN = 240
# 看门狗剩余不足 1 分钟就不延长：改触发时刻和到点触发会撞在一起。
_MIN_LEFT = 1
# 封顶之后可延长的分钟数不足这个数就拒绝。
_MIN_GAIN = 5
_CAP_TEXT = "会话已到 240 分钟上限，请 session close"


def _zone() -> ZoneInfo:
    return ZoneInfo(conf().timezone)


def _local_time(text: str) -> datetime | None:
    # 服务器报的下次运行时间：带偏移就换算，不带就按 server.timezone。
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_zone())
    return value.astimezone(_zone())


def opened_time(local: dict | None, status: dict) -> datetime | None:
    raw = str((local or {}).get("opened_at") or status.get("opened_at") or "")
    try:
        value = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return value.replace(tzinfo=ZoneInfo("UTC")).astimezone(_zone())


def _snaps_missing(status: dict) -> bool:
    found = set(text_list(status.get("snapshots")))
    return any(name not in found for name in conf().snapshots)


def _watchdog_refusal(status: dict, now: datetime) -> str:
    state = str(status.get("watchdog_state") or "none")
    next_run = str(status.get("next_run") or "none")
    if state in ("", "none") or next_run in ("", "none"):
        return "看门狗不在，不能 extend"
    if state == "Running" or next_run == "fired":
        return "看门狗已触发，不能 extend，请 session close"
    when = _local_time(next_run)
    if when is None:
        return "看门狗时间无法解析，不能 extend"
    if (when - now).total_seconds() < _MIN_LEFT * 60:
        return "看门狗剩余不足 1 分钟，不能 extend，请 session close"
    return ""


def extend_refusal(local: dict | None, status: dict, now: datetime) -> str:
    # 与 reset 同样的会话检查：本机标记、所有者 session、两个快照都在、没有 done；另要求看门狗在、没触发。
    if local is None:
        return "没有打开的会话，不能 extend"
    owner = marker_owner(local, status)
    if owner != "session":
        return "extend 只处理 session open 打开的会话（当前所有者：" + (owner or "无") + "）"
    if _snaps_missing(status):
        return "快照不在，不能 extend"
    if status.get("done"):
        return "看门狗已还原，不能 extend，请 session close"
    reason = _watchdog_refusal(status, now)
    if reason:
        return reason
    if opened_time(local, status) is None:
        return "会话打开时间缺失或格式不对，不能 extend"
    return ""


def plan_deadline(current: datetime, opened: datetime, minutes: int) -> tuple[datetime | None, str]:
    # 新到点 = 现在的到点 + minutes，封顶在打开后 240 分钟；精确到秒，向下取整，不越过上限。
    cap = opened + timedelta(minutes=SESSION_MAX_MIN)
    target = min(current + timedelta(minutes=minutes), cap).replace(microsecond=0)
    if (target - current).total_seconds() < _MIN_GAIN * 60:
        return None, _CAP_TEXT
    return target, ""


def minutes_refusal(minutes: int) -> str:
    if minutes < EXTEND_FLOOR or minutes > EXTEND_CEIL:
        return "--minutes 只能是 10 到 120"
    return ""


def _next_folder(report: Path) -> Path:
    for seq in range(1, 1000):
        path = report / f"extend-{seq}"
        try:
            path.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return path
    raise RuntimeError("extend 目录无法创建")


def _update_markers(remote: Remote, local: dict, target: datetime, opened: datetime) -> str:
    payload = dict(local)
    payload["deadline"] = target.isoformat()
    payload["watchdog_min"] = int((target - opened).total_seconds() // 60)
    payload["extends"] = int(local.get("extends") or 0) + 1
    write_linux_marker(payload)
    body = Path(str(local.get("report_dir"))) / "session-body.json"
    body.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        remote.upload(body, conf().remote("out", "session-body.json"))
        result = remote.run_script("session-mark.ps1", None, 60)
    except RemoteError as exc:
        return exc.output
    return "" if result.code == 0 else (result.out or "session-mark 失败")


def _write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_text(text, encoding="utf-8")


def _arm_later(remote: Remote, folder: Path, target: datetime) -> str:
    until = target.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        result = remote.run_script("watchdog-extend.ps1", {"Until": until}, 60)
    except RemoteError as exc:
        _write(folder, "watchdog-extend.log", exc.output)
        return (exc.output or "watchdog-extend 失败").strip()
    _write(folder, "watchdog-extend.log", result.out)
    if result.code == 0:
        return ""
    if "is not recognized" in result.out:
        return "服务器上没有 watchdog-extend.ps1（会话由旧版本打开），请 session close 后重新 open"
    lines = [line.strip() for line in result.out.splitlines() if line.strip()]
    return lines[-1] if lines else "watchdog-extend 失败"


def _load(remote: Remote, local: dict) -> tuple[dict, Path]:
    report = Path(str(local.get("report_dir") or ""))
    if not report.is_dir():
        raise RuntimeError("会话目录不存在")
    return fetch_status(remote, report, ensure=False), report


def _summary(folder: Path, row: dict) -> None:
    _write(folder, "extend.json", json.dumps(row, ensure_ascii=False, indent=2) + "\n")


def extend_session(minutes: int = EXTEND_DEFAULT, remote: Remote | None = None) -> int:
    # 参数不对、本机没有会话标记时不连服务器。
    local = read_linux_marker()
    reason = minutes_refusal(minutes) or ("" if local else extend_refusal(None, {}, datetime.now(_zone())))
    if reason:
        print(reason)
        return 1
    remote = remote or make_remote()
    try:
        status, report = _load(remote, local)
    except (RuntimeError, RemoteError, OSError, json.JSONDecodeError) as exc:
        print(str(exc)[:300])
        return 1
    now = datetime.now(_zone())
    reason = extend_refusal(local, status, now)
    current = _local_time(str(status.get("next_run") or ""))
    opened = opened_time(local, status)
    target = None
    if not reason:
        target, reason = plan_deadline(current, opened, minutes)
    if reason:
        print(reason)
        return 1
    return _apply(remote, local, report, (current, opened, target), minutes)


def _apply(remote: Remote, local: dict, report: Path, times: tuple, minutes: int) -> int:
    current, opened, target = times
    folder = _next_folder(report)
    row = {"at": datetime.now(_zone()).isoformat(timespec="seconds"), "minutes": minutes}
    row.update({"before": current.isoformat(), "until": target.isoformat(), "opened_at": opened.isoformat()})
    failed = _arm_later(remote, folder, target)
    row["ok"] = failed == ""
    if failed:
        _summary(folder, row)
        print(("extend 失败：" + failed + "（用 session status 核对看门狗到点时刻）")[:300])
        return 1
    warn = _update_markers(remote, local, target, opened)
    row["marker_warning"] = warn[:300]
    _summary(folder, row)
    held = int((datetime.now(_zone()) - opened).total_seconds() // 60)
    print(f"extend ok, 看门狗到点 {target.strftime('%H:%M')}（会话已开 {held} 分钟，最多 {SESSION_MAX_MIN}）")
    if warn:
        # 看门狗已延长，sql/call 的窗口检查读的是任务时间，不受影响；不要为此重试 extend。
        print("警告：会话标记没更新（" + warn.strip()[:160] + "），看门狗已按上面的时刻延长")
    return 0
