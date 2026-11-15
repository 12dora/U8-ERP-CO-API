"""登录日期的时区。U8 按本地日期记账，缺省东八区（U8CO_TIMEZONE）。

用固定偏移，不查 tzdata，slim 镜像里也能用。启动时由 main.create_app 设置一次。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_ZONE = [timezone(timedelta(hours=8))]


def set_zone(zone: timezone) -> None:
    _ZONE[0] = zone


def login_defaults(date: str | None = None) -> tuple[str, str]:
    """登录日期和年度。缺省日期是配置时区的今天，年度是该日期的前四位。"""
    if date is None:
        date = datetime.now(_ZONE[0]).date().isoformat()
    return date, date[:4]
