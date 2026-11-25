"""应收应付期初单据用例（openings_arap、openings_live）的挑数和回读 SQL。只读；标识符取自按 side 登记的常量，
拼进去的只有校验过范围的整数。

openings_arap_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

_YMD = "N'[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'"
_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"

# side → (系统 cSysID / Ap_Vouch.cFlag, 启用日期参数名, GL_mend 结账标志列, 往来单位表, 单位编码列, 科目辅助核算列,
#          受控科目前缀, 往来明细表)。同桥 ReportsOpening、ArapRefs。
SIDES = {
    "ar": ("AR", "dARStartDate", "bflag_AR", "dbo.Customer", "cCusCode", "bcus", "1122", "dbo.Ar_Detail"),
    "ap": ("AP", "dAPStartDate", "bflag_AP", "dbo.Vendor", "cVenCode", "bsup", "2202", "dbo.Ap_Detail"),
}


def _side(side: str) -> tuple[str, ...]:
    found = SIDES.get(side)
    if found is None:
        raise ValueError("side 只能是 ar 或 ap")
    return found


def _year(year: int) -> int:
    if type(year) is not int or not 1000 <= year <= 9999:
        raise ValueError("年度不对")
    return year


def state_sql(side: str) -> str:
    """一行：<启用日期>|<启用月份结账标志>，例如 2026-01-01|0。

    启用日期取 AccInformation 的 d{AR,AP}StartDate 前 10 个字符；启用年度、启用月份 GL_mend 的结账标志（0 / 1）。
    没有启用日期时第一段为空；GL_mend 没有这一行按未结账（0），同桥 OpeningsArapGuard。
    """
    sys_id, name, flag = _side(side)[:3]
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + ISNULL(s.v, N'') + N'|' + ISNULL((SELECT TOP 1 CONVERT(varchar(1), CONVERT(int,"
        f" ISNULL(m.{flag}, 0))) FROM dbo.GL_mend m"
        " WHERE m.iyear = CASE WHEN s.v LIKE " + _YMD + " THEN CONVERT(int, LEFT(s.v, 4)) END"
        " AND m.iperiod = CASE WHEN s.v LIKE " + _YMD + " THEN CONVERT(int, SUBSTRING(s.v, 6, 2)) END), N'0') AS v\n"
        "FROM (SELECT MAX(LEFT(LTRIM(CONVERT(nvarchar(40), cValue)), 10)) AS v FROM dbo.AccInformation"
        f" WHERE cSysID = N'{sys_id}' AND cName = N'{name}') s;\n"
    )


def pick_sql(side: str, year: int) -> str:
    """一行：<往来单位编码>|<受控末级科目>；没有可用的单位或科目时没有行。

    启用年度受控前缀（1122 / 2202）下带往来辅助核算（bcus / bsup=1）、本系统受控（cother=AR / AP）的末级科目，
    和编码最小的未停用客户 / 供应商（同桥 ArapRefs.Party、ArapRefs.Subject 的检查）。
    """
    sys_id, _name, _flag, table, col, aux, prefix = _side(side)[:7]
    return (
        "SET NOCOUNT ON;\n"
        f"SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(c.{col})) + N'|' + km.code AS v\n"
        f"FROM {table} AS c\n"
        "CROSS JOIN (\n"
        "  SELECT TOP (1) LTRIM(RTRIM(k.ccode)) AS code FROM dbo.code AS k\n"
        f"  WHERE k.iyear = {_year(year)} AND k.bend = 1 AND k.{aux} = 1 AND UPPER(ISNULL(k.cother, N'')) = N'{sys_id}'\n"
        f"    AND k.ccode LIKE N'{prefix}%' AND LTRIM(RTRIM(k.ccode)) " + _SAFE + "\n"
        "  ORDER BY k.ccode\n"
        ") AS km\n"
        f"WHERE c.dEndDate IS NULL AND LTRIM(RTRIM(c.{col})) " + _SAFE + f" AND LEN(LTRIM(RTRIM(c.{col}))) <= 20\n"
        f"ORDER BY c.{col};\n"
    )


def doc_sql(side: str, doc_id: int) -> str:
    """一行：<期初标志>|<单据日期>|<审核日期>|<往来明细行数>|<第 0 期行数>，例如 1|2025-12-31|2025-12-31|1|1。

    第 0 期行数只数登记日期等于单据日期的往来明细。单据不存在时没有行（删除后的回读）。
    """
    if type(doc_id) is not int or not 1 <= doc_id <= 2147483647:
        raise ValueError("单据主键不对")
    parts = _side(side)
    sys_id, detail = parts[0], parts[7]
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(nvarchar(1), CONVERT(int, ISNULL(h.bStartFlag, 0)))"
        " + N'|' + ISNULL(CONVERT(nvarchar(10), h.dVouchDate, 23), N'')"
        " + N'|' + ISNULL(CONVERT(nvarchar(10), h.dVerifyDate, 23), N'')"
        f" + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM {detail} d"
        " WHERE d.cVouchType = h.cVouchType AND d.cVouchID = h.cVouchID))"
        f" + N'|' + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM {detail} d"
        " WHERE d.cVouchType = h.cVouchType AND d.cVouchID = h.cVouchID AND d.iPeriod = 0"
        " AND CONVERT(date, d.dRegDate) = CONVERT(date, h.dVouchDate))) AS v\n"
        f"FROM dbo.Ap_Vouch AS h WHERE h.Auto_ID = {doc_id} AND h.cFlag = N'{sys_id}';\n"
    )


def ar_state_sql() -> str:
    return state_sql("ar")


def ar_pick_sql(year: int) -> str:
    return pick_sql("ar", year)


def ar_doc_sql(doc_id: int) -> str:
    return doc_sql("ar", doc_id)


def openings_arap_samples() -> list[tuple[str, str]]:
    return [
        ("openings-arap-state", ar_state_sql()),
        ("openings-arap-pick", ar_pick_sql(2026)),
        ("openings-arap-doc", ar_doc_sql(41)),
        ("openings-arap-state-ap", state_sql("ap")),
        ("openings-arap-pick-ap", pick_sql("ap", 2026)),
        ("openings-arap-doc-ap", doc_sql("ap", 41)),
    ]
