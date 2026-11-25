"""经营管理报表（总账口径）用例（mgmt_gl）的对照 SQL：与桥的写法不同、按同一口径独立再算一遍。

只读。每行 ROW:a|b|…。拼进 SQL 的只有本模块校验过的整数年度、期间和 yyyy-MM-dd 日期，不接收别的值。
损益：损益科目（6 开头）去掉期间损益结转凭证（coutsign = N'期间损益'，或有本年利润 4103 分录、其余全是科目档案类型
code.cclass = N'损益' 的凭证），作废不计，缺省只计已记账。
"""

from __future__ import annotations

from datetime import date

_PL = "xc.cclass = N'损益'"
_PROFIT = "x.ccode LIKE '4103%'"


def _year(value: int) -> int:
    if type(value) is not int or not 2000 <= value <= 2099:
        raise ValueError("年度不能拼进 SQL")
    return value


def _period(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 12:
        raise ValueError("期间不能拼进 SQL")
    return value


def _day(text: str) -> str:
    date.fromisoformat(text)
    return "CONVERT(date, '" + text + "', 23)"


def pnl_sql(year: int, period_to: int, unposted: bool) -> str:
    """1..period_to 每期每个一级损益科目的贷减借：期间|编码前 4 位|金额。"""
    y, p = _year(year), _period(period_to)
    posted = "" if unposted else "\n  AND ISNULL(v.ibook, 0) = 1"
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(2), v.iperiod) + N'|' + LEFT(v.ccode, 4) + N'|'
  + CONVERT(varchar(30), CONVERT(decimal(18,2), SUM(ISNULL(v.mc, 0) - ISNULL(v.md, 0)))) AS v
FROM dbo.GL_accvouch AS v
JOIN (
  SELECT x.iperiod, x.csign, x.ino_id,
    MAX(CASE WHEN x.coutsign = N'期间损益' THEN 1 ELSE 0 END) AS auto_t,
    SUM(CASE WHEN {_PROFIT} THEN 1 ELSE 0 END) AS profit_lines,
    SUM(CASE WHEN {_PROFIT} OR {_PL} THEN 0 ELSE 1 END) AS other_lines
  FROM dbo.GL_accvouch AS x
  LEFT JOIN dbo.code AS xc ON xc.iyear = x.iyear AND xc.ccode = x.ccode
  WHERE x.iyear = {y} AND ISNULL(x.iflag, 0) <> 1
  GROUP BY x.iperiod, x.csign, x.ino_id
) AS k ON k.iperiod = v.iperiod AND k.csign = v.csign AND k.ino_id = v.ino_id
WHERE v.iyear = {y} AND v.iperiod BETWEEN 1 AND {p} AND v.ccode LIKE '6%' AND ISNULL(v.iflag, 0) <> 1
  AND k.auto_t = 0 AND NOT (k.profit_lines > 0 AND k.other_lines = 0){posted}
GROUP BY v.iperiod, LEFT(v.ccode, 4);
"""


def meta_sql(year: int) -> str:
    """本年度凭证行数|最大制单日期。"""
    y = _year(year)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*)) + N'|' + ISNULL(CONVERT(varchar(10), MAX(v.dbill_date), 23), N'') AS v
FROM dbo.GL_accvouch AS v WHERE v.iyear = {y};
"""


def cash_sql(year: int, period: int, start: str, end: str) -> str:
    """一行：货币资金末级合计|存货结存金额|已审核产成品入库数量|采购发票无税金额|已审核采购入库金额。"""
    y, p = _year(year), _period(period)
    lo, hi = _day(start), _day(end)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:'
  + CONVERT(varchar(30), CONVERT(decimal(18,2), ISNULL((SELECT SUM(CASE s.cendd_c WHEN N'借' THEN ISNULL(s.me, 0)
      WHEN N'贷' THEN -ISNULL(s.me, 0) ELSE 0 END) FROM dbo.GL_accsum AS s
      WHERE s.iyear = {y} AND s.iperiod = {p} AND (s.ccode LIKE '1001%' OR s.ccode LIKE '1002%' OR s.ccode LIKE '1012%')
        AND EXISTS (SELECT 1 FROM dbo.code AS c WHERE c.iyear = s.iyear AND c.ccode = s.ccode AND c.bend = 1)), 0))) + N'|'
  + CONVERT(varchar(30), CONVERT(decimal(18,2), ISNULL((SELECT SUM(a.iMoney) FROM dbo.IA_Summary AS a
      WHERE a.iYear = {y} AND a.iMonth = {p}), 0))) + N'|'
  + CONVERT(varchar(30), CONVERT(decimal(20,6), ISNULL((SELECT SUM(b.iQuantity) FROM dbo.rdrecords10 AS b
      JOIN dbo.rdrecord10 AS h ON h.ID = b.ID WHERE h.dDate >= {lo} AND h.dDate < DATEADD(day, 1, {hi})
        AND LTRIM(RTRIM(ISNULL(h.cHandler, N''))) <> N''), 0))) + N'|'
  + CONVERT(varchar(30), CONVERT(decimal(18,2), ISNULL((SELECT SUM(b.iMoney) FROM dbo.PurBillVouchs AS b
      JOIN dbo.PurBillVouch AS h ON h.PBVID = b.PBVID WHERE h.dPBVDate >= {lo} AND h.dPBVDate < DATEADD(day, 1, {hi})), 0))) + N'|'
  + CONVERT(varchar(30), CONVERT(decimal(18,2), ISNULL((SELECT SUM(b.iPrice) FROM dbo.rdrecords01 AS b
      JOIN dbo.RdRecord01 AS h ON h.ID = b.ID WHERE h.dDate >= {lo} AND h.dDate < DATEADD(day, 1, {hi})
        AND LTRIM(RTRIM(ISNULL(h.cHandler, N''))) <> N''), 0))) AS v;
"""


def mgmt_gl_samples() -> list[tuple[str, str]]:
    return [
        ("mgmt-pnl", pnl_sql(2026, 8, False)),
        ("mgmt-meta", meta_sql(2026)),
        ("mgmt-cash", cash_sql(2026, 8, "2026-08-01", "2026-08-31")),
    ]
