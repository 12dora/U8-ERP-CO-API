"""造单据前的采购期初记账（pu_opening）：新建账套的采购管理没有做期初记账时，U8 拒绝保存采购发票
（「系统尚未做期初记账…」）。这一步在采购单据之前经桥的 openings/post 记账。

幂等：先按 SQL 看采购管理启用年度 GL_mend 第 0 期的 bflag_PU（同桥 ReportsOpening.Posted），已记账就跳过；
桥回 409「采购期初已记账」（并行或上次半途）也算已存在。记账后再按 SQL 回读确认。
采购管理没有启用（AccInformation 没有 dPUStartDate）时失败：先在 U8 里启用采购管理。
SQL 里没有拼进任何调用方的值。
"""

from __future__ import annotations

import re

from co.e2e.seed.base import Probe, Step, fail_text
from co.e2e.seed.context import SeedContext, SeedError

NAME = "pu_opening"
ROUTE = "v1/openings/post"
BODY = {"module": "pu", "action": "post"}
_POSTED_TEXT = "采购期初已记账"
_YMD = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")

# 一行：start（采购管理启用日期，前 10 个字符）、posted（启用年度第 0 期的 bflag_PU，没有这一行为 0）。
STATE_SQL = (
    "SELECT s.v AS start, ISNULL((SELECT TOP 1 CONVERT(int, ISNULL(m.bflag_PU, 0)) FROM GL_mend m"
    " WHERE m.iperiod = 0 AND m.iyear = CASE WHEN s.v LIKE N'[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'"
    " THEN CONVERT(int, LEFT(s.v, 4)) END), 0) AS posted"
    " FROM (SELECT MAX(LEFT(LTRIM(CONVERT(nvarchar(40), cValue)), 10)) AS v FROM AccInformation"
    " WHERE cSysID = N'PU' AND cName = N'dPUStartDate') s"
)


def pu_state(ctx: SeedContext) -> tuple[str, bool]:
    """(启用日期, 是否已期初记账)；没有启用时启用日期为空串。"""
    rows = ctx.rows(STATE_SQL)
    row = rows[0] if rows else {}
    start = row.get("start", "").strip()
    return (start if _YMD.fullmatch(start) else ""), row.get("posted", "").strip() == "1"


def _label(start: str) -> str:
    return NAME + ":" + (start[:4] if start else "未启用")


def probe(ctx: SeedContext) -> Probe:
    start, posted = pu_state(ctx)
    if start and posted:
        return Probe((_label(start),), ())
    return Probe((), (_label(start),))


def _already(response: dict) -> bool:
    return response.get("status") == 409 and _POSTED_TEXT in str(response.get("message") or "")


def apply(ctx: SeedContext) -> tuple[int, int]:
    start, posted = pu_state(ctx)
    if not start:
        raise SeedError("采购管理未启用（AccInformation 没有 dPUStartDate）：先在 U8 里启用采购管理")
    if posted:
        return 0, 1
    response = ctx.call(ROUTE, dict(BODY))
    if _already(response):
        return 0, 1
    if response.get("ok") is not True:
        raise SeedError("采购期初记账失败：" + fail_text(response), response, ctx.last_call)
    if not pu_state(ctx)[1]:
        raise SeedError(start[:4] + " 年采购期初记账后回读仍未记账", response, ctx.last_call)
    return 1, 0


STEPS = (Step(NAME, "采购期初记账（采购单据之前）", probe, apply),)
