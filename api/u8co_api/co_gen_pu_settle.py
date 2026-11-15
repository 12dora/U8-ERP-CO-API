"""采购结算（vouchers/generate，type=purchase_settle，source_type=purchase_invoice）的请求校验，与桥 PuSettleReq 一致。

按整张发票自动结算：不收 lines（v1 不支持按行结算）；表头只收 settle_date（yyyy-MM-dd，可省），
给了就作为这一笔的 U8 登录日期（覆盖请求的 date），不能晚于今天。不与 year 比：year 是账套的年度库，
有的单位多年的业务都在同一个年度库里。结算日期所在月以前的月份采购须已结账，由 U8 判断。
"""

from __future__ import annotations

import datetime as dt
import re

from u8co_api.co_clock import login_defaults
from u8co_api.co_doctext import section

SETTLE_KIND = "purchase_settle"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SETTLE_GEN_HELP = section(
    "采购结算单（type=purchase_settle，source_type=purchase_invoice，id 是 PBVID）",
    (
        "按整张发票自动结算，不能带 lines",
        "表头只收 settle_date（yyyy-MM-dd，可省），它就是这一笔的 U8 登录日期（覆盖 date）",
        "settle_date 不能晚于今天，不能早于发票日期和入库日期",
        "所在期间采购未结账，且之前各月采购已结账（U8 判断，否则 409 原文）",
    ),
)


def _settle_date(head: dict) -> str:
    found = ""
    seen: set[str] = set()
    for key, value in head.items():
        low = str(key).lower()
        if low != "settle_date":
            raise ValueError("不能设置字段 " + str(key))
        if low in seen:
            raise ValueError("字段重复 " + str(key))
        seen.add(low)
        if not isinstance(value, str) or _DATE.fullmatch(value) is None:
            raise ValueError("settle_date 必须是 yyyy-MM-dd")
        try:
            dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("settle_date 必须是 yyyy-MM-dd") from exc
        found = value
    return found


def check_settle_gen(head: dict | None, lines: list | None, year: str | None = None, date: str | None = None) -> None:
    """year、date 是请求的同名字段（可省，省略时同服务填的缺省：今天、今天的年份）。"""
    if lines is not None:
        raise ValueError("采购结算按整张发票结算，不能指定 lines")
    day = _settle_date(head or {})
    if not day:
        return
    today, _ = login_defaults()
    if day > today:
        raise ValueError("settle_date 不能晚于今天")
