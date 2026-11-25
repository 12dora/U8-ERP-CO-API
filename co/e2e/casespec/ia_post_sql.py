"""存货核算用例（ia_post）的回读 SQL。只读；标识符是常量，拼进去的只有校验过范围的年度、月份整数。

ia_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

# 一个月的存货核算状态，一行：明细账行数|汇总表行数|已期末处理的汇总行数|结账标志（0 / 1，GL_mend 没有这一行按 0）。
# 同桥 sql/ia/*.sql 末尾诊断计数用的条件（iYear、iMonth；期末处理看 IA_Summary.iPeriod=1）。
_MONTH = (
    "SET NOCOUNT ON;\n"
    "SELECT N'ROW:'"
    " + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Subsidiary s WHERE s.iYear = {y} AND s.iMonth = {m}))"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Summary u WHERE u.iYear = {y} AND u.iMonth = {m}))"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Summary u WHERE u.iYear = {y} AND u.iMonth = {m}"
    " AND ISNULL(u.iPeriod, 0) = 1))"
    " + N'|' + CONVERT(nvarchar(1), ISNULL((SELECT TOP 1 CONVERT(int, ISNULL(g.bflag_IA, 0)) FROM dbo.GL_mend g"
    " WHERE g.iyear = {y} AND g.iperiod = {m}), 0)) AS v;\n"
)


def ia_month_sql(year: int, month: int) -> str:
    """一行：<明细账行数>|<汇总表行数>|<已期末处理的汇总行数>|<结账标志>，例如 120|40|0|0。"""
    if type(year) is not int or type(month) is not int or not 1000 <= year <= 9999 or not 1 <= month <= 12:
        raise ValueError("年度、月份不对")
    return _MONTH.format(y=year, m=month)


def ia_samples() -> list[tuple[str, str]]:
    return [("ia-month", ia_month_sql(2026, 9))]
