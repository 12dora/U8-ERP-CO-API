"""存货核算与月末结账真实写入用例（live_close）的回读 SQL。只读；标识符是常量，拼进去的只有校验过范围的
年度、月份整数。

live_close_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

# 一行：已记账的出入库单据行和销售发票行（记账人非空，全账套）|该月明细账出库行数|其中没有出库成本的行数。
# 出库成本的条件同桥 sql/ia/period_end.sql 末尾的 out_null_cost（非金额行、iOutCost 为空）。
_IA_EXTRA = (
    "SET NOCOUNT ON;\n"
    "SELECT N'ROW:' + CONVERT(nvarchar(12),"
    " (SELECT COUNT(*) FROM dbo.rdrecords01 r WHERE ISNULL(r.cbaccounter, N'') <> N'')"
    " + (SELECT COUNT(*) FROM dbo.rdrecords08 r WHERE ISNULL(r.cbaccounter, N'') <> N'')"
    " + (SELECT COUNT(*) FROM dbo.rdrecords09 r WHERE ISNULL(r.cbaccounter, N'') <> N'')"
    " + (SELECT COUNT(*) FROM dbo.rdrecords10 r WHERE ISNULL(r.cbaccounter, N'') <> N'')"
    " + (SELECT COUNT(*) FROM dbo.rdrecords11 r WHERE ISNULL(r.cbaccounter, N'') <> N'')"
    " + (SELECT COUNT(*) FROM dbo.rdrecords32 r WHERE ISNULL(r.cbaccounter, N'') <> N'')"
    " + (SELECT COUNT(*) FROM dbo.SaleBillVouchs b WHERE ISNULL(b.cbAccounter, N'') <> N''))"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Subsidiary s WHERE s.iYear = {y} AND s.iMonth = {m}"
    " AND s.bRdFlag = 0))"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Subsidiary s WHERE s.iYear = {y} AND s.iMonth = {m}"
    " AND s.bRdFlag = 0 AND ISNULL(s.bMoneyFlag, 0) = 0 AND s.iOutCost IS NULL)) AS v;\n"
)

# 一行：该年该月库存月结快照五张表的行数，ST_MonthAccount|ST_MonthAccounts|ST_MonthAccountV|ST_MonthAccountVs|
# ST_MonthAccountCheck（同桥库存结账写、取消结账删的五张表）。
_STOCK = (
    "SET NOCOUNT ON;\n"
    "SELECT N'ROW:'"
    " + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.ST_MonthAccount t WHERE t.iYear = {y} AND t.iMonth = {m}))"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.ST_MonthAccounts t WHERE t.iYear = {y}"
    " AND t.iMonth = {m}))"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.ST_MonthAccountV t WHERE t.iYear = {y}"
    " AND t.iMonth = {m}))"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.ST_MonthAccountVs t WHERE t.iYear = {y}"
    " AND t.iMonth = {m}))"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.ST_MonthAccountCheck t WHERE t.iYear = {y}"
    " AND t.iMonth = {m})) AS v;\n"
)

# 一行：该月汇总表行数|其中已期末处理的|下月（12 月为 13）汇总表行数|下月明细账里结转行（cVouType 24）数。
# 同桥 sql/ia/close.sql、unclose.sql 末尾的诊断计数：IA_Close 把结存结转到下月，IA_UnClose 删掉。
_IA_CLOSE = (
    "SET NOCOUNT ON;\n"
    "SELECT N'ROW:'"
    " + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Summary u WHERE u.iYear = {y} AND u.iMonth = {m}))"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Summary u WHERE u.iYear = {y} AND u.iMonth = {m}"
    " AND ISNULL(u.iPeriod, 0) = 1))"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Summary u WHERE u.iYear = {y} AND u.iMonth = {n}))"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Subsidiary s WHERE s.iYear = {y} AND s.iMonth = {n}"
    " AND s.cVouType = N'24')) AS v;\n"
)


def _checked(year: int, month: int) -> None:
    if type(year) is not int or type(month) is not int or not 1000 <= year <= 9999 or not 1 <= month <= 12:
        raise ValueError("年度、月份不对")


def ia_extra_sql(year: int, month: int) -> str:
    """一行：<已记账单据行数>|<该月出库明细行数>|<没有出库成本的行数>，例如 340|12|0。"""
    _checked(year, month)
    return _IA_EXTRA.format(y=year, m=month)


def stock_month_sql(year: int, month: int) -> str:
    """一行：五张库存月结快照表该月的行数，例如 120|120|118|118|560。"""
    _checked(year, month)
    return _STOCK.format(y=year, m=month)


def ia_close_sql(year: int, month: int) -> str:
    """一行：<该月汇总行>|<已期末处理的>|<下月汇总行>|<下月结转明细行>，例如 40|40|38|38。"""
    _checked(year, month)
    return _IA_CLOSE.format(y=year, m=month, n=month + 1)


def live_close_samples() -> list[tuple[str, str]]:
    return [
        ("live-ia-extra", ia_extra_sql(2026, 10)),
        ("live-stock-month", stock_month_sql(2026, 9)),
        ("live-ia-close", ia_close_sql(2026, 9)),
    ]
