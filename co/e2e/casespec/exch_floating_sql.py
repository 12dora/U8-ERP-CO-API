"""浮动汇率用例（exch_floating）的 SQL：账套汇率方式选项（AccInformation AA / iXchgRateStl）的读和临时改写，
外币、空期间的挑选，浮动汇率行的回读。

写语句只有选项那一行：带原值作条件，回 @@ROWCOUNT 供核对；拼进去的只有校验过的短字母数字串和范围内的整数。
币种不拼名称（多为中文），统一用子查询取「编码最小的非本位币」，挑选和回读是同一个币种。
exch_floating_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re

_VALUE = re.compile(r"^[A-Za-z0-9]{0,10}\Z")
_OPTION = "cSysID = N'AA' AND cName = N'iXchgRateStl'"
_TEXT = "ISNULL(LTRIM(RTRIM(CONVERT(nvarchar(20), cValue))), N'')"
CURRENCY = "(SELECT TOP (1) f.cexch_name FROM dbo.foreigncurrency AS f WHERE ISNULL(f.iotherused, 0) <> -1 ORDER BY f.cexch_name)"


def _value(text: str) -> str:
    if type(text) is not str or _VALUE.fullmatch(text) is None:
        raise ValueError("选项值只能是 10 个以内的字母数字")
    return text


def _year(year: int) -> int:
    if type(year) is not int or not 1000 <= year <= 9999:
        raise ValueError("年度不对")
    return year


def _period(period: int) -> int:
    if type(period) is not int or not 1 <= period <= 12:
        raise ValueError("期间不对")
    return period


def mode_sql() -> str:
    """一行：<选项行数>|<选项值（去空格）>。"""
    return (
        "SET NOCOUNT ON;\n"
        f"SELECT N'ROW:' + CONVERT(varchar(4), COUNT(*)) + N'|' + ISNULL(MAX({_TEXT}), N'') AS v\n"
        f"FROM dbo.AccInformation WHERE {_OPTION};\n"
    )


def mode_set_sql(new: str, old: str) -> str:
    """把选项从 old 改成 new（只改值等于 old 的那一行）；一行：<改动行数>。"""
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE dbo.AccInformation SET cValue = N'{_value(new)}' WHERE {_OPTION} AND {_TEXT} = N'{_value(old)}';\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def foreign_sql() -> str:
    """一行：<币种名称>；没有外币时没有行。"""
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + LTRIM(RTRIM({CURRENCY})) AS v WHERE {CURRENCY} IS NOT NULL;\n"


def periods_sql(year: int) -> str:
    """登录年度 1 到 12 期各一行：<期间>|<该币种的浮动汇率行数>|<总账结账标志>。"""
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(4), g.iperiod)"
        " + N'|' + CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.exch AS e WHERE e.cexch_name = " + CURRENCY
        + f" AND e.iYear = {_year(year)} AND e.iperiod = g.iperiod AND e.itype = 1))"
        " + N'|' + CONVERT(varchar(1), CONVERT(int, ISNULL(g.bflag, 0))) AS v\n"
        f"FROM dbo.GL_mend AS g WHERE g.iyear = {_year(year)} AND g.iperiod BETWEEN 1 AND 12 ORDER BY g.iperiod;\n"
    )


def floating_rows_sql(year: int, period: int) -> str:
    """该币种、年度、期间的浮动汇率行（itype=1），一行一个：<i_id>|<cdate 原文>|<汇率>。"""
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(20), e.i_id) + N'|' + ISNULL(LTRIM(RTRIM(e.cdate)), N'')"
        " + N'|' + CONVERT(varchar(40), CONVERT(decimal(28, 6), e.nflat)) AS v\n"
        f"FROM dbo.exch AS e WHERE e.cexch_name = {CURRENCY} AND e.iYear = {_year(year)}"
        f" AND e.iperiod = {_period(period)} AND e.itype = 1 ORDER BY e.i_id;\n"
    )


def exch_floating_samples() -> list[tuple[str, str]]:
    return [
        ("exch-mode", mode_sql()),
        ("exch-mode-set", mode_set_sql("False", "True")),
        ("exch-foreign", foreign_sql()),
        ("exch-periods", periods_sql(2026)),
        ("exch-floating-rows", floating_rows_sql(2026, 11)),
    ]
