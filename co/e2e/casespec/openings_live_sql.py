"""期初单据「临时打开已结账月份」用例（openings_live）的 SQL：GL_mend 结账标志的临时清除与恢复、期初结存单的挑数和回读。

写语句只动 GL_mend 的一个结账标志列，并且只动调用方点名的（年度, 期间）行：清除前先读出要动的行，恢复时按同一张清单改回，
两条语句都回 @@ROWCOUNT 供核对。标识符是常量，拼进去的只有校验过范围的整数。走用例的 SQL 通道（sqlexec.run_guarded，过 sqlguard），
只在测试账套的会话快照里执行，快照还原兜底。

openings_live_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

from co.e2e.casespec.openings_arap_sql import SIDES

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"
# 一次最多动的期间行数（清单整段拼进 VALUES）；多出来说明账套形态异常，调用方记 N/A，不动标志。
MAX_PERIODS = 240


def _year(year: int) -> int:
    if type(year) is not int or not 1000 <= year <= 9999:
        raise ValueError("年度不对")
    return year


def _period(period: int) -> int:
    if type(period) is not int or not 0 <= period <= 255:
        raise ValueError("期间不对")
    return period


def _bit(value: int) -> int:
    if type(value) is not int or value not in (0, 1):
        raise ValueError("标志只能是 0 或 1")
    return value


def arap_flag_sql(side: str, year: int, period: int, value: int) -> str:
    """把应收 / 应付启用月份的结账标志从 1 - value 改成 value；一行：<改动行数>（0 或 1）。"""
    if side not in SIDES:
        raise ValueError("side 只能是 ar 或 ap")
    col = SIDES[side][2]
    to = _bit(value)
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE dbo.GL_mend SET {col} = {to}"
        f" WHERE iyear = {_year(year)} AND iperiod = {_period(period)} AND ISNULL({col}, 0) = {1 - to};\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


# 库存已月结的期间（GL_mend iperiod > 0 且 bflag_ST=1），一行一个：<年度>|<期间>。U8 新增、删除期初结存单时查的
# 「库存启用的第一个月已经月结」看的是任一已月结的库存期间（实测），所以清单不按启用年度过滤。
_ST_CLOSED = (
    "SET NOCOUNT ON;\n"
    "SELECT N'ROW:' + CONVERT(varchar(6), m.iyear) + N'|' + CONVERT(varchar(4), m.iperiod) AS v\n"
    "FROM dbo.GL_mend AS m WHERE m.iperiod > 0 AND ISNULL(m.bflag_ST, 0) = 1\n"
    "ORDER BY m.iyear, m.iperiod;\n"
)


def st_closed_sql() -> str:
    return _ST_CLOSED


def st_flag_sql(periods: list[tuple[int, int]], value: int) -> str:
    """把清单里各（年度, 期间）的 bflag_ST 从 1 - value 改成 value；一行：<改动行数>。清单不能为空。"""
    if not periods or len(periods) > MAX_PERIODS:
        raise ValueError("期间清单为空或太长")
    rows = ", ".join(f"({_year(year)}, {_period(period)})" for year, period in periods)
    to = _bit(value)
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE m SET m.bflag_ST = {to}\n"
        "FROM dbo.GL_mend AS m\n"
        f"JOIN (VALUES {rows}) AS v (y, p) ON m.iyear = v.y AND m.iperiod = v.p\n"
        f"WHERE m.iperiod > 0 AND ISNULL(m.bflag_ST, 0) = {1 - to};\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def parse_periods(cells: list[str]) -> list[tuple[int, int]]:
    """st_closed_sql 的结果行 → [(年度, 期间)]；有一行认不出就抛 ValueError（不动标志）。"""
    found = []
    for cell in cells:
        year, _sep, period = cell.strip().partition("|")
        if not (year.isdigit() and period.isdigit()):
            raise ValueError("读不懂已月结期间：" + cell[:40])
        found.append((_year(int(year)), _period(int(period))))
    return found


# 两行：仓库编码|存货编码。同一个未停用、不管货位的仓库；两种未停用、不是应税劳务、不管批次和保质期、单计量的存货
# （期初单只给仓库、存货、数量就能导入，同 stock_opening_sql 的挑数）。挑不到两种时少于两行。
_PICK2 = (
    "SET NOCOUNT ON;\n"
    "SELECT TOP (2) N'ROW:' + w.code + N'|' + i.code AS v\n"
    "FROM (\n"
    "  SELECT TOP (1) LTRIM(RTRIM(h.cWhCode)) AS code FROM dbo.Warehouse AS h\n"
    "  WHERE h.dWhEndDate IS NULL AND ISNULL(h.bWhPos, 0) = 0 AND LTRIM(RTRIM(h.cWhCode)) " + _SAFE + "\n"
    "  ORDER BY h.cWhCode\n"
    ") AS w\n"
    "CROSS JOIN (\n"
    "  SELECT TOP (2) LTRIM(RTRIM(n.cInvCode)) AS code FROM dbo.Inventory AS n\n"
    "  WHERE n.dEDate IS NULL AND ISNULL(n.bService, 0) = 0 AND ISNULL(n.bInvBatch, 0) = 0\n"
    "    AND ISNULL(n.bInvQuality, 0) = 0 AND ISNULL(n.iGroupType, 0) = 0\n"
    "    AND LTRIM(RTRIM(n.cInvCode)) " + _SAFE + " AND LEN(LTRIM(RTRIM(n.cInvCode))) <= 60\n"
    "  ORDER BY n.cInvCode\n"
    ") AS i\n"
    "ORDER BY i.code;\n"
)


def stock_pick2_sql() -> str:
    return _PICK2


def opening_doc_sql(doc_id: int) -> str:
    """一张期初结存单：<ID>|<单号>|<单据日期>|<审核日期>|<期初标志>；不存在时没有行。"""
    if type(doc_id) is not int or not 1 <= doc_id <= 2147483647:
        raise ValueError("单据主键不对")
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(20), h.ID) + N'|' + LTRIM(RTRIM(ISNULL(h.cCode, N'')))"
        " + N'|' + ISNULL(CONVERT(nvarchar(10), h.dDate, 23), N'')"
        " + N'|' + ISNULL(CONVERT(nvarchar(10), h.dVeriDate, 23), N'')"
        " + N'|' + CONVERT(nvarchar(1), CONVERT(int, ISNULL(h.bIsSTQc, 0))) AS v\n"
        f"FROM dbo.rdrecord34 AS h WHERE h.ID = {doc_id} AND h.cVouchType = N'34';\n"
    )


def openings_live_samples() -> list[tuple[str, str]]:
    return [
        ("openings-live-ar-open", arap_flag_sql("ar", 2026, 1, 0)),
        ("openings-live-ap-restore", arap_flag_sql("ap", 2026, 1, 1)),
        ("openings-live-st-closed", st_closed_sql()),
        ("openings-live-st-open", st_flag_sql([(2026, 1), (2026, 2)], 0)),
        ("openings-live-st-restore", st_flag_sql([(2026, 1)], 1)),
        ("openings-live-pick2", stock_pick2_sql()),
        ("openings-live-doc34", opening_doc_sql(900000041)),
    ]
