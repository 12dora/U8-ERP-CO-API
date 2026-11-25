"""经营管理报表（业务口径）用例（mgmt_sa）的对照 SQL：与桥的写法不同、按同一口径独立再算一遍。

只读。每行 ROW:a|b|…。拼进 SQL 的只有本模块校验过的整数年度、期间、yyyy-MM-dd 日期和固定的往来方向，不接收别的值。
销售：已复核（cChecker）、非期初销售发票的无税本币金额合计（发票日期在窗口内），存货核算销售成本行（bSale=1、出库方向）的金额合计。
往来：截至 as_of 的往来余额（应收借减贷、应付贷减借，按科目前缀），未结票据余额（iRAmount 不为 0、签发日期不晚于 as_of）。
"""

from __future__ import annotations

from datetime import date

_SIDES = {
    "ar": ("Ar_Detail", "ISNULL(d.iDAmount, 0) - ISNULL(d.iCAmount, 0)", "AR", "1122", "dARStartDate"),
    "ap": ("Ap_Detail", "ISNULL(d.iCAmount, 0) - ISNULL(d.iDAmount, 0)", "AP", "2202", "dAPStartDate"),
}


def _int(value: int, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("整数不能拼进 SQL")
    return value


def _day(text: str) -> str:
    date.fromisoformat(text)
    return "CONVERT(date, '" + text + "', 23)"


def sales_sql(window: tuple[str, str], year: int, periods: tuple[int, int]) -> str:
    """收入|成本：window 是发票日期 [起, 止)（止为次日），periods 是存货核算的起止会计月份。"""
    start, stop = _day(window[0]), _day(window[1])
    y, low, high = _int(year, 2000, 2099), _int(periods[0], 1, 12), _int(periods[1], 1, 12)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(30), CONVERT(decimal(18,2), ISNULL((
  SELECT SUM(b.iNatMoney) FROM dbo.SaleBillVouchs AS b
  WHERE b.SBVID IN (
    SELECT h.SBVID FROM dbo.SaleBillVouch AS h
    WHERE h.dDate >= {start} AND h.dDate < {stop} AND ISNULL(h.bFirst, 0) = 0 AND LTRIM(RTRIM(ISNULL(h.cChecker, N''))) <> N''
  )), 0))) + N'|' + CONVERT(varchar(30), CONVERT(decimal(18,2), ISNULL((
  SELECT SUM(s.iAOutPrice) FROM dbo.IA_Subsidiary AS s
  WHERE s.iYear = {y} AND s.iMonth >= {low} AND s.iMonth <= {high} AND s.bSale = 1 AND s.bRdFlag = 0
  ), 0))) AS v;
"""


def arap_sql(side: str, as_of: str) -> str:
    """启用日期|余额|未结票据：按缺省科目前缀（应收 1122、应付 2202）。"""
    table, amount, flag, prefix, start = _SIDES[side]
    day = _day(as_of)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + ISNULL((SELECT TOP (1) a.cValue FROM dbo.AccInformation AS a
    WHERE a.cSysID = N'{flag}' AND a.cName = N'{start}'), N'')
  + N'|' + CONVERT(varchar(30), CONVERT(decimal(18,2), ISNULL((
    SELECT SUM({amount}) FROM dbo.{table} AS d
    WHERE d.iFlag < 3 AND d.dRegDate < DATEADD(day, 1, {day}) AND d.cCode LIKE N'{prefix}%'), 0)))
  + N'|' + CONVERT(varchar(30), CONVERT(decimal(18,2), ISNULL((
    SELECT SUM(CASE WHEN ISNULL(n.iRAmount_Local, 0) <> 0 THEN n.iRAmount_Local ELSE n.iRAmount END)
    FROM dbo.AP_Note AS n
    WHERE n.cFlag = N'{flag}' AND ISNULL(n.iRAmount, 0) <> 0 AND n.dSignDate < DATEADD(day, 1, {day})), 0))) AS v;
"""


def mgmt_sa_samples() -> list[tuple[str, str]]:
    return [
        ("mgmt-sales", sales_sql(("2026-01-01", "2026-09-01"), 2026, (1, 8))),
        ("mgmt-arap-ar", arap_sql("ar", "2026-08-31")),
        ("mgmt-arap-ap", arap_sql("ap", "2026-08-31")),
    ]
