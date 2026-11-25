"""U8TaskConfig 的调度记账判别。

RESTORE 会把库带回快照当时的 MULTI_USER。还原批必须马上再设 SINGLE_USER，
记下 restore_started，先导出任务表，再算指纹，最后才放开多用户。
只放行调度列，而且新的 PreTime / NextTime 必须落在还原开始之后的这一分钟里。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from co.e2e.settings import conf

_TABLE = "dbo.U8TaskConfig"
_KEYS = ("next", "pre", "ts", "app")


def _parse_dt(text: str) -> datetime:
    raw = text.strip().replace("T", " ")
    if "." in raw:
        raw = raw.split(".", 1)[0]
    return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")


def _floor_bound(started: datetime) -> datetime:
    return started.replace(second=0, microsecond=0) - timedelta(minutes=1)


def _same(old: dict, new: dict) -> bool:
    for key in _KEYS:
        if old.get(key) != new.get(key):
            return False
    return True


def _clocks(old: dict, new: dict) -> tuple[datetime, datetime, datetime, datetime] | None:
    try:
        return (
            _parse_dt(str(old["pre"])),
            _parse_dt(str(old["next"])),
            _parse_dt(str(new["pre"])),
            _parse_dt(str(new["next"])),
        )
    except (KeyError, ValueError):
        return None


def _order_problem(old_pre: datetime, old_next: datetime, new_pre: datetime, new_next: datetime) -> str:
    if new_pre < old_pre or new_next < old_next:
        return "调度时间倒退"
    if new_next <= new_pre:
        return "NextTime 不晚于 PreTime"
    return ""


# U8 的调度服务按分钟轮询，到点后可能晚几秒到几十秒才执行并改写 NextTime；还原开始前已到点、还没执行的任务，
# NextTime 会略早于还原开始。放宽 5 分钟（实测：每分钟一次的任务 NextTime 19:47:00，还原开始 19:47:12）。
_OVERDUE = timedelta(minutes=5)


def _window_problem(new_pre: datetime, new_next: datetime, started: datetime) -> str:
    if new_next <= started - _OVERDUE:
        return "NextTime 不晚于还原开始"
    if new_pre < _floor_bound(started):
        return "PreTime 早于还原开始所在分钟"
    return ""


def _time_problem(old: dict, new: dict, started: datetime) -> str:
    clocks = _clocks(old, new)
    if clocks is None:
        return "时间无法解析"
    order = _order_problem(clocks[0], clocks[1], clocks[2], clocks[3])
    if order:
        return order
    return _window_problem(clocks[2], clocks[3], started)


def _row_problem(old: dict, new: dict, started: datetime) -> str:
    if old.get("rest") != new.get("rest"):
        return "调度记账以外的列变化"
    if _same(old, new):
        return ""
    return _time_problem(old, new, started)


def _note(name: str, old: dict, new: dict) -> str:
    return f"{name}:PreTime {old['pre']} → {new['pre']},NextTime {old['next']} → {new['next']}"


def _rows(doc: dict) -> dict[str, dict]:
    found = {}
    for row in doc.get("rows") or []:
        if isinstance(row, dict) and row.get("name"):
            found[str(row["name"])] = row
    return found


def classify(snap: dict, live: dict, revert_started: str) -> tuple[bool, list[str]]:
    before = _rows(snap)
    after = _rows(live)
    if set(before) != set(after):
        return False, ["U8TaskConfig 行集合不同"]
    try:
        started = _parse_dt(revert_started)
    except ValueError:
        return False, ["还原开始时间无法解析"]
    notes: list[str] = []
    for name, old in sorted(before.items()):
        problem = _row_problem(old, after[name], started)
        if problem:
            return False, [f"U8TaskConfig {name} {problem}"]
        if not _same(old, after[name]):
            notes.append(_note(name, old, after[name]))
    return True, notes


def only_taskcfg(diff: list[str]) -> bool:
    if not diff:
        return False
    data = conf().data_db
    for line in diff:
        if line.startswith(f"{data} {_TABLE} "):
            continue
        if line == f"{data} 合计不一致":
            continue
        return False
    return True
