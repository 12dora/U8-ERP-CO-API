"""存货核算期初记账用例（ia_opening）的只读 SQL：账套状态、已审核期初结存单的取数口径、记账后的期初汇总。

取数口径与桥 sql/ia/qc_keep.sql 一致：已审核（cHandler 非空）的期初结存单行，存货不是应税劳务、业务类型不是直运；
金额取 iPrice，为空时取 round(数量 × iUnitCost, 2)。拼进 SQL 的只有校验过的整数和存货编码。
走用例的 SQL 通道（sqlexec.run_guarded，过 sqlguard）。ia_opening_samples() 进样例 SQL（samples.sample_sql）。
"""

from __future__ import annotations

import re

_CODE = re.compile(r"^[0-9A-Za-z._-]{1,60}$")
_TRUE = "(N'True', N'TRUE', N'true', N'1')"
_VERIFIED = (
    "FROM dbo.rdrecord34 AS h JOIN dbo.rdrecords34 AS b ON b.id = h.id"
    " JOIN dbo.Inventory AS i ON i.cInvCode = b.cInvCode"
    " WHERE ISNULL(h.cHandler, N'') <> N'' AND ISNULL(i.bService, 0) = 0"
    " AND LEFT(ISNULL(h.cBusType, N''), 2) <> N'直运'"
)

# 一行：<存货核算启用日期>|<库存启用日期>|<第 0 期已记账 0/1>|<启用年度已结账月数>|<启用年度日常明细与汇总行数>
# |<启用年度期初（iMonth=0）明细与汇总行数>|<已有期初记账痕迹 0/1>|<取数口径的已审核期初结存单行数>。
# 期初记账痕迹：bQCInput 为真、年度开账标志、历史设置表有启用年度的行（取消期初记账会把它们一并删掉）。
_STATE = (
    "SET NOCOUNT ON;\n"
    "WITH s AS (SELECT MAX(LEFT(LTRIM(CONVERT(nvarchar(40), cValue)), 10)) AS d FROM dbo.AccInformation"
    " WHERE cSysID = N'IA' AND cName = N'dIAStartDate'),\n"
    "t AS (SELECT MAX(LEFT(LTRIM(CONVERT(nvarchar(40), cValue)), 10)) AS d FROM dbo.AccInformation"
    " WHERE cSysID = N'ST' AND cName = N'dSTStartDate'),\n"
    "y AS (SELECT YEAR(TRY_CONVERT(date, s.d, 23)) AS v FROM s)\n"
    "SELECT N'ROW:' + ISNULL(s.d, N'') + N'|' + ISNULL(t.d, N'')\n"
    " + N'|' + CASE WHEN EXISTS (SELECT 1 FROM dbo.GL_mend AS m WHERE m.iyear = y.v AND m.iperiod = 0"
    " AND ISNULL(m.bflag_IA, 0) = 1) THEN N'1' ELSE N'0' END\n"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.GL_mend AS m WHERE m.iyear = y.v"
    " AND m.iperiod BETWEEN 1 AND 12 AND ISNULL(m.bflag_IA, 0) = 1))\n"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Summary AS a WHERE a.iYear = y.v AND a.iMonth <> 0)"
    " + (SELECT COUNT(*) FROM dbo.IA_Subsidiary AS a WHERE a.iYear = y.v AND a.iMonth <> 0))\n"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Summary AS a WHERE a.iYear = y.v AND a.iMonth = 0)"
    " + (SELECT COUNT(*) FROM dbo.IA_Subsidiary AS a WHERE a.iYear = y.v AND a.iMonth = 0))\n"
    " + N'|' + CASE WHEN EXISTS (SELECT 1 FROM dbo.AccInformation AS c WHERE c.cSysID = N'IA' AND c.cName = N'bQCInput'"
    " AND c.cValue IN " + _TRUE + ")\n"
    "   OR EXISTS (SELECT 1 FROM dbo.AccInformation_Year AS c WHERE c.cSysID = N'IA'"
    " AND c.cName IN (N'OpenAccFlag', N'IsFxAccount') AND c.iYear = y.v)\n"
    "   OR EXISTS (SELECT 1 FROM dbo.IA_HisOption AS c WHERE c.iYear = y.v)\n"
    "   OR EXISTS (SELECT 1 FROM dbo.IA_HisWarehouse AS c WHERE c.iYear = y.v)\n"
    "   OR EXISTS (SELECT 1 FROM dbo.IA_HisInventory AS c WHERE c.iYear = y.v)\n"
    "   OR EXISTS (SELECT 1 FROM dbo.IA_HisInvCheckFree AS c WHERE c.iYear = y.v) THEN N'1' ELSE N'0' END\n"
    " + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) " + _VERIFIED + ")) AS v\n"
    "FROM s CROSS JOIN t CROSS JOIN y;\n"
)


def ia_state_sql() -> str:
    return _STATE


def _inv(code: str) -> str:
    if type(code) is not str or not _CODE.match(code):
        raise ValueError("存货编码不对")
    return code


def _year(year: int) -> int:
    if type(year) is not int or not 1000 <= year <= 9999:
        raise ValueError("年度不对")
    return year


def verified_inv_sql(inv: str) -> str:
    """一行：<行数>|<数量合计>|<金额合计>（两位小数），按取数口径统计该存货已审核的期初结存单行。"""
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(nvarchar(12), COUNT(*))"
        " + N'|' + ISNULL(CONVERT(nvarchar(40), CONVERT(decimal(38, 2), SUM(b.iQuantity))), N'')"
        " + N'|' + ISNULL(CONVERT(nvarchar(40), CONVERT(decimal(38, 2), SUM(CASE WHEN b.iPrice IS NOT NULL THEN b.iPrice"
        " ELSE ROUND(CAST(ISNULL(b.iQuantity, 0) AS float) * CAST(ISNULL(b.iUnitCost, 0) AS float), 2) END))), N'') AS v\n"
        + _VERIFIED + f" AND b.cInvCode = N'{_inv(inv)}';\n"
    )


def summary_inv_sql(year: int, inv: str) -> str:
    """一行：<该存货期初汇总行数>|<数量合计>|<金额合计>|<第 0 期 bflag_IA>|<启用年度期初汇总总行数>。"""
    y = _year(year)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(nvarchar(12), COUNT(*))"
        " + N'|' + ISNULL(CONVERT(nvarchar(40), CONVERT(decimal(38, 2), SUM(a.iNum))), N'')"
        " + N'|' + ISNULL(CONVERT(nvarchar(40), CONVERT(decimal(38, 2), SUM(a.iMoney))), N'')"
        " + N'|' + CONVERT(nvarchar(1), ISNULL((SELECT TOP (1) CONVERT(int, ISNULL(m.bflag_IA, 0)) FROM dbo.GL_mend AS m"
        f" WHERE m.iyear = {y} AND m.iperiod = 0), 0))"
        f" + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM dbo.IA_Summary AS z WHERE z.iYear = {y} AND z.iMonth = 0)) AS v\n"
        f"FROM dbo.IA_Summary AS a WHERE a.iYear = {y} AND a.iMonth = 0 AND a.cInvCode = N'{_inv(inv)}';\n"
    )


def ia_opening_samples() -> list[tuple[str, str]]:
    return [
        ("ia-opening-state", ia_state_sql()),
        ("ia-opening-verified", verified_inv_sql("INV-001")),
        ("ia-opening-summary", summary_inv_sql(2024, "INV-001")),
    ]
