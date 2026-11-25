"""期初记账用例（openings_pu）的挑数 SQL。只读；标识符是常量，没有拼进调用方的值。

openings_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

# 采购管理启用日期（AccInformation 的 dPUStartDate，取前 10 个字符）| 启用年度 GL_mend 第 0 期的 bflag_PU（0 / 1）。
# 没有启用日期时第一段为空；GL_mend 没有这一行按未记账（0），同桥 ReportsOpening.Posted。
_PU_STATE = (
    "SET NOCOUNT ON;\n"
    "SELECT N'ROW:' + ISNULL(s.v, N'') + N'|' + ISNULL((SELECT TOP 1 CONVERT(varchar(1), CONVERT(int,"
    " ISNULL(m.bflag_PU, 0))) FROM dbo.GL_mend m WHERE m.iperiod = 0 AND m.iyear = CASE WHEN s.v LIKE"
    " N'[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]' THEN CONVERT(int, LEFT(s.v, 4)) END), N'0') AS v\n"
    "FROM (SELECT MAX(LEFT(LTRIM(CONVERT(nvarchar(40), cValue)), 10)) AS v FROM dbo.AccInformation"
    " WHERE cSysID = N'PU' AND cName = N'dPUStartDate') s;\n"
)


def pu_opening_sql() -> str:
    """一行：<启用日期>|<期初记账标志>，例如 2024-01-01|0。"""
    return _PU_STATE


def openings_samples() -> list[tuple[str, str]]:
    return [("openings-pu-state", pu_opening_sql())]
