"""业务时间护栏：回归和 session open 在业务时间内拒绝启动，看门狗时限内会跨进业务时间的也拒绝。

回归要登录 U8 很多次，会和同事争用 U8 许可点数（许可按工作站和模块计点，点数满了同事就登录不了）。
配置里必须写 business_hours；写 null 才是不设护栏。--dry-run 不登录 U8，永远不拦；确实要白天跑就加 --allow-daytime。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from co.e2e import settings_parse as sp
from co.e2e.settings_parse import ConfigError

SECTION = "business_hours"
FLAG = "--allow-daytime"
_KEYS = frozenset(("days", "start", "end", "tz"))
_DAYS = re.compile(r"^[1-7](?:-[1-7])?(?:,[1-7](?:-[1-7])?)*\Z")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)\Z")


@dataclass(frozen=True)
class BusinessHours:
    days: frozenset[int]
    start: time
    end: time
    tz: str
    days_text: str

    def label(self) -> str:
        return f"星期 {self.days_text} {self.start:%H:%M}-{self.end:%H:%M} {self.tz}"


def parse(data: dict, default_tz: str) -> BusinessHours | None:
    """读 e2e 配置顶层的 business_hours。必须写；显式 null 才不设护栏。"""
    if SECTION not in data:
        raise ConfigError(
            f"e2e 配置缺少 {SECTION}：写上业务时间，例如 "
            '{"days": "1-6", "start": "08:00", "end": "17:30"}（tz 缺省取 server.timezone），'
            "确实不要护栏就写 null"
        )
    if data[SECTION] is None:
        return None
    sec = sp.section(data, SECTION, _KEYS)
    days_text = sp.required(sec, "days", SECTION)
    start = _clock(sp.required(sec, "start", SECTION), "start")
    end = _clock(sp.required(sec, "end", SECTION), "end")
    if end <= start:
        raise ConfigError(f"配置项 {SECTION}.end 必须晚于 start（不支持跨午夜）")
    return BusinessHours(_days(days_text), start, end, _zone(sp.text(sec, "tz", default_tz, SECTION)), days_text)


def _days(text: str) -> frozenset[int]:
    # ISO 星期：1 是星期一，7 是星期日。写法 "1-5"、"1,3,5" 或 "1-3,6"。
    if _DAYS.fullmatch(text) is None:
        raise ConfigError(f"配置项 {SECTION}.days 不合法：{text!r}（写成 1-5 或 1,3,5）")
    found: set[int] = set()
    for part in text.split(","):
        low, _, high = part.partition("-")
        first, last = int(low), int(high or low)
        if last < first:
            raise ConfigError(f"配置项 {SECTION}.days 的范围 {part!r} 反了")
        found.update(range(first, last + 1))
    return frozenset(found)


def _clock(text: str, key: str) -> time:
    found = _CLOCK.fullmatch(text)
    if found is None:
        raise ConfigError(f"配置项 {SECTION}.{key} 必须是 HH:MM：{text!r}")
    return time(int(found.group(1)), int(found.group(2)))


def _zone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"配置项 {SECTION}.tz 不是有效时区：{name!r}") from None
    return name


def in_hours(hours: BusinessHours, now: datetime) -> bool:
    local = now.astimezone(ZoneInfo(hours.tz))
    clock = time(local.hour, local.minute, local.second)
    return local.isoweekday() in hours.days and hours.start <= clock < hours.end


def overlaps(hours: BusinessHours, now: datetime, minutes: int) -> bool:
    """[now, now + minutes] 与任一天的业务时间 [start, end) 有交集。minutes 为 0 时等同 in_hours。"""
    zone = ZoneInfo(hours.tz)
    first = now.astimezone(zone)
    last = first + timedelta(minutes=max(minutes, 0))
    day = first.date()
    while day <= last.date():
        if day.isoweekday() in hours.days:
            opens = datetime.combine(day, hours.start, zone)
            closes = datetime.combine(day, hours.end, zone)
            if opens <= last and closes > first:
                return True
        day += timedelta(days=1)
    return False


def current_time() -> datetime:
    # 单元测试替换这个函数来固定时刻。
    return datetime.now(timezone.utc)


def refusal(
    hours: BusinessHours | None, allow: bool, dry_run: bool, now: datetime | None = None, minutes: int = 0
) -> str:
    """要拒绝就返回中文说明，否则返回空串。minutes 是本次看门狗分钟数（最长占用时间）。

    now 缺省取当前时间，单元测试注入固定时刻。
    """
    if hours is None or allow or dry_run:
        return ""
    moment = now or current_time()
    if not overlaps(hours, moment, minutes):
        return ""
    local = moment.astimezone(ZoneInfo(hours.tz))
    tail = f"，回归会和同事争用 U8 许可；下班后再跑，或加 {FLAG}"
    if in_hours(hours, moment):
        return f"现在是业务时间（{hours.label()}，当前 {local:%Y-%m-%d %H:%M}）" + tail
    return (
        f"看门狗 {minutes} 分钟内会进入业务时间（{hours.label()}，当前 {local:%Y-%m-%d %H:%M}）"
        + tail + "；也可以用 --watchdog-min 缩短"
    )
