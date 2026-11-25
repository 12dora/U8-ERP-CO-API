"""采购期初取消记账用例（openings_pu_unpost）的 SQL。只读；标识符是常量，没有拼进调用方的值。

pu_unpost_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

_DATE = "N'[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'"

# 一行：<启用日期>|<第 0 期 bflag_PU>|<启用年度启用月份起已结账的期间数>|<第 0 期 bflag_IA>|<采购发票张数>|<启用月份之前已置标志的期间数>。
# 前四项是桥 OpeningPost 取消记账时依次查的拒绝条件（期初未记账、已有月份结账、存货核算已期初记账、已有任何采购发票，
# 发票不分年度，同桥的 SELECT TOP 1 FROM PurBillVouch）；最后一项给记账后回读（记账把启用月份之前各期都置 1）。
_STATE = (
    "SET NOCOUNT ON;\n"
    "WITH s AS (SELECT MAX(LEFT(LTRIM(CONVERT(nvarchar(40), cValue)), 10)) AS v FROM dbo.AccInformation"
    " WHERE cSysID = N'PU' AND cName = N'dPUStartDate'),\n"
    "d AS (SELECT s.v, CASE WHEN s.v LIKE " + _DATE + " THEN CONVERT(int, LEFT(s.v, 4)) END AS y,"
    " CASE WHEN s.v LIKE " + _DATE + " THEN CONVERT(int, SUBSTRING(s.v, 6, 2)) END AS m FROM s)\n"
    "SELECT N'ROW:' + ISNULL(d.v, N'')"
    " + N'|' + ISNULL((SELECT TOP (1) CONVERT(varchar(1), CONVERT(int, ISNULL(g.bflag_PU, 0))) FROM dbo.GL_mend AS g"
    " WHERE g.iyear = d.y AND g.iperiod = 0), N'0')"
    " + N'|' + CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.GL_mend AS g WHERE g.iyear = d.y AND g.iperiod >= 1"
    " AND g.iperiod >= d.m AND ISNULL(g.bflag_PU, 0) = 1))"
    " + N'|' + ISNULL((SELECT TOP (1) CONVERT(varchar(1), CONVERT(int, ISNULL(g.bflag_IA, 0))) FROM dbo.GL_mend AS g"
    " WHERE g.iyear = d.y AND g.iperiod = 0), N'0')"
    " + N'|' + CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.PurBillVouch))"
    " + N'|' + CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.GL_mend AS g WHERE g.iyear = d.y AND g.iperiod < d.m"
    " AND ISNULL(g.bflag_PU, 0) = 1)) AS v\n"
    "FROM d;\n"
)


def pu_unpost_state_sql() -> str:
    return _STATE


def pu_unpost_samples() -> list[tuple[str, str]]:
    return [("pu-unpost-state", pu_unpost_state_sql())]
