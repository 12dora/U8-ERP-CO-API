"""库存期初结存单用例（stock_opening）的状态和挑数 SQL。只读；标识符是常量，没有拼进去的值。

stock_opening_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

_YMD = "N'[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'"
_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"
_YEAR = "CASE WHEN s.v LIKE " + _YMD + " THEN CONVERT(int, LEFT(s.v, 4)) END"

# 一行：库存启用日期（AccInformation 的 dSTStartDate，取前 10 个字符）|启用年度起有没有库存已月结的月份（GL_mend
# iperiod > 0 且 bflag_ST=1，0 / 1）|存货核算是否已期初记账（启用年度第 0 期 bflag_IA=1，同桥 StockOpening.GuardIa）
# |期初结存单张数（rdrecord34，类型 34）。没有启用日期时第一段为空。
_STATE = (
    "SET NOCOUNT ON;\n"
    "SELECT N'ROW:' + ISNULL(s.v, N'')"
    " + N'|' + CASE WHEN EXISTS (SELECT 1 FROM dbo.GL_mend AS m WHERE m.iperiod > 0 AND ISNULL(m.bflag_ST, 0) = 1"
    " AND m.iyear >= " + _YEAR + ") THEN N'1' ELSE N'0' END"
    " + N'|' + CASE WHEN EXISTS (SELECT 1 FROM dbo.GL_mend AS m WHERE m.iperiod = 0 AND ISNULL(m.bflag_IA, 0) = 1"
    " AND m.iyear = " + _YEAR + ") THEN N'1' ELSE N'0' END"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.rdrecord34 AS r WHERE r.cVouchType = N'34')) AS v\n"
    "FROM (SELECT MAX(LEFT(LTRIM(CONVERT(nvarchar(40), cValue)), 10)) AS v FROM dbo.AccInformation"
    " WHERE cSysID = N'ST' AND cName = N'dSTStartDate') AS s;\n"
)

# 一行：仓库编码|存货编码。未停用、不管货位的仓库；未停用、不是应税劳务、不管批次和保质期、单计量的存货
# （期初单只给仓库、存货、数量就能导入）。挑不到时没有行。
_PICK = (
    "SET NOCOUNT ON;\n"
    "SELECT TOP (1) N'ROW:' + w.code + N'|' + i.code AS v\n"
    "FROM (\n"
    "  SELECT TOP (1) LTRIM(RTRIM(h.cWhCode)) AS code FROM dbo.Warehouse AS h\n"
    "  WHERE h.dWhEndDate IS NULL AND ISNULL(h.bWhPos, 0) = 0 AND LTRIM(RTRIM(h.cWhCode)) " + _SAFE + "\n"
    "  ORDER BY h.cWhCode\n"
    ") AS w\n"
    "CROSS JOIN (\n"
    "  SELECT TOP (1) LTRIM(RTRIM(n.cInvCode)) AS code FROM dbo.Inventory AS n\n"
    "  WHERE n.dEDate IS NULL AND ISNULL(n.bService, 0) = 0 AND ISNULL(n.bInvBatch, 0) = 0\n"
    "    AND ISNULL(n.bInvQuality, 0) = 0 AND ISNULL(n.iGroupType, 0) = 0\n"
    "    AND LTRIM(RTRIM(n.cInvCode)) " + _SAFE + " AND LEN(LTRIM(RTRIM(n.cInvCode))) <= 60\n"
    "  ORDER BY n.cInvCode\n"
    ") AS i;\n"
)


def stock_state_sql() -> str:
    """一行：<启用日期>|<有已月结月份>|<存货核算已期初记账>|<期初结存单张数>，例如 2026-01-01|1|0|12。"""
    return _STATE


def stock_pick_sql() -> str:
    """一行：<仓库编码>|<存货编码>；挑不到时没有行。"""
    return _PICK


def stock_opening_samples() -> list[tuple[str, str]]:
    return [
        ("stock-opening-state", stock_state_sql()),
        ("stock-opening-pick", stock_pick_sql()),
    ]
