"""存货核算制单用例（ia_voucher）的挑单据、回读 SQL。

只读；表名、列名是常量。拼进 SQL 的年度、月份只接受范围内的整数，外部业务号只接受「4 位年度 + IA + 13 位数字」。
每行输出 ROW:<值>，多个值用 | 分隔。ia_voucher_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re

_PZ = re.compile(r"^[0-9]{4}IA[0-9]{13}\Z")
# 入库类单据：记账时就有成本（出库要期末处理后才有），挑这几类做制单。表 → 存货核算单据类型。
_IN_TYPES = (("01", "01"), ("08", "08"), ("10", "10"))
# 成对业务（桥按 cBusType 前两个字成对制单），主流程不挑。
_PAIR = "N'调拨', N'转换', N'组装', N'拆卸'"
BILL_COLS = 2
# 制单后表头写凭证号的来源单据表（同桥 IaVoucherBack.HeadTables）。
HEAD_TABLES = ("01", "08", "09", "10", "11", "32")


def _month(year: object, month: object) -> tuple[int, int]:
    if type(year) is not int or type(month) is not int or not 2000 <= year <= 2099 or not 1 <= month <= 12:
        raise ValueError("年度、月份不对")
    return year, month


def pz_id(value: object) -> str:
    text = str(value or "")
    if _PZ.fullmatch(text) is None:
        raise ValueError("外部业务号不对")
    return text


def _bills(table: str, kind: str, y: int, m: int) -> str:
    return (
        f"SELECT DISTINCT N'{kind}' AS t, h.ID AS id FROM dbo.IA_Subsidiary AS s"
        f" JOIN dbo.RdRecords{table} AS b ON b.AutoID = s.ID JOIN dbo.RdRecord{table} AS h ON h.ID = b.ID"
        f" WHERE s.cVouType = N'{kind}' AND s.iYear = {y} AND s.iMonth = {m} AND s.cPZID IS NULL"
        f" AND LEFT(ISNULL(s.cBusType, N''), 2) NOT IN ({_PAIR}) AND s.iAInPrice IS NOT NULL"
        " AND ISNULL(s.iDebitDifCost, 0) = 0 AND ISNULL(s.iCreditDifCost, 0) = 0"
        " AND NOT EXISTS (SELECT 1 FROM dbo.IA_Subsidiary AS o WHERE o.iYear = s.iYear AND o.cVouType = s.cVouType"
        f" AND o.ID IN (SELECT r.AutoID FROM dbo.RdRecords{table} AS r WHERE r.ID = h.ID) AND o.cPZID IS NOT NULL)"
    )


def _sale_out(y: int, m: int) -> str:
    """销售成本按销售出库单核算（bSaleType）时，一张全部行有成本、未制单的销售出库单（32）。"""
    return (
        "SELECT TOP (1) N'32' AS t, h.ID AS id FROM dbo.IA_Subsidiary AS s"
        " JOIN dbo.RdRecords32 AS b ON b.AutoID = s.ID JOIN dbo.RdRecord32 AS h ON h.ID = b.ID"
        f" WHERE s.cVouType = N'32' AND s.iYear = {y} AND s.iMonth = {m} AND s.cPZID IS NULL"
        " AND EXISTS (SELECT 1 FROM dbo.AccInformation AS a WHERE a.cSysID = N'IA' AND a.cName = N'bSaleType'"
        " AND ISNULL(a.cValue, a.cDefault) = N'销售出库单')"
        " AND NOT EXISTS (SELECT 1 FROM dbo.IA_Subsidiary AS o WHERE o.iYear = s.iYear AND o.cVouType = s.cVouType"
        " AND o.ID IN (SELECT r.AutoID FROM dbo.RdRecords32 AS r WHERE r.ID = h.ID)"
        " AND (o.cPZID IS NOT NULL OR o.iAOutPrice IS NULL OR ISNULL(o.iDebitDifCost, 0) <> 0 OR ISNULL(o.iCreditDifCost, 0) <> 0))"
        " ORDER BY h.ID"
    )


def _pair(y: int, m: int) -> str:
    """一对调拨 / 形态转换（同业务单号的 08、09）的其他出库单：两边都有、全部行有成本、未制单、无差异、金额相等。"""
    same = (
        "FROM dbo.IA_Subsidiary AS o WHERE o.iYear = s.iYear AND o.cVouType IN (N'08', N'09')"
        " AND o.cBusCode = s.cBusCode AND LEFT(ISNULL(o.cBusType, N''), 2) = LEFT(s.cBusType, 2)"
    )
    return (
        "SELECT TOP (1) N'09' AS t, h.ID AS id FROM dbo.IA_Subsidiary AS s"
        " JOIN dbo.RdRecords09 AS b ON b.AutoID = s.ID JOIN dbo.RdRecord09 AS h ON h.ID = b.ID"
        f" WHERE s.cVouType = N'09' AND s.iYear = {y} AND s.iMonth = {m} AND s.cPZID IS NULL"
        " AND LEFT(ISNULL(s.cBusType, N''), 2) IN (N'调拨', N'转换') AND ISNULL(s.cBusCode, N'') <> N''"
        f" AND EXISTS (SELECT 1 {same} AND o.cVouType = N'08')"
        f" AND NOT EXISTS (SELECT 1 {same} AND (o.cPZID IS NOT NULL OR ISNULL(o.iDebitDifCost, 0) <> 0"
        " OR ISNULL(o.iCreditDifCost, 0) <> 0 OR (o.cVouType = N'08' AND o.iAInPrice IS NULL)"
        " OR (o.cVouType = N'09' AND o.iAOutPrice IS NULL)))"
        f" AND (SELECT SUM(ISNULL(o.iAInPrice, 0) - ISNULL(o.iAOutPrice, 0)) {same}) = 0"
        " ORDER BY h.ID"
    )


def bills_sql(year: int, month: int) -> str:
    """该月已记账、未制单、有成本的单据，每行 <单据类型>|<表头 ID>：入库单据最多 3 张（按类型、ID），
    再加最多一张销售出库单 32、一对调拨 / 形态转换的其他出库单 09（没有就不出）。"""
    y, m = _month(year, month)
    union = " UNION ".join(_bills(table, kind, y, m) for table, kind in _IN_TYPES)
    picks = (
        f"SELECT TOP (3) x.t, x.id FROM ({union}) AS x ORDER BY x.t, x.id",
        _sale_out(y, m),
        _pair(y, m),
    )
    body = " UNION ALL ".join(f"SELECT p{i}.t, p{i}.id FROM ({sql}) AS p{i}" for i, sql in enumerate(picks))
    return "SET NOCOUNT ON;\n" f"SELECT N'ROW:' + z.t + N'|' + CONVERT(varchar(12), z.id) AS v FROM ({body}) AS z;\n"


def _heads(no: str) -> str:
    return " + ".join(f"(SELECT COUNT(*) FROM dbo.RdRecord{t} WHERE cPZID = N'{no}')" for t in HEAD_TABLES)


def marks_sql(pz: str) -> str:
    """一行 <凭证行数>|<制单系统 IA 且外部业务类型「存货核算」的行数>|<借方合计>|<贷方合计>|<不同凭证键个数>
    |<存货明细账带本号的行数>|<未记账行数>|<来源单据表头带本号的张数>。"""
    no = pz_id(pz)
    where = f"g.coutno_id = N'{no}'"
    parts = (
        "COUNT(*)",
        "SUM(CASE WHEN g.coutsysname = N'IA' AND g.coutsign = N'存货核算' THEN 1 ELSE 0 END)",
        "CONVERT(decimal(18, 2), ISNULL(SUM(g.md), 0))",
        "CONVERT(decimal(18, 2), ISNULL(SUM(g.mc), 0))",
        "COUNT(DISTINCT CONVERT(varchar(12), g.iperiod) + g.csign + CONVERT(varchar(12), g.ino_id))",
        f"(SELECT COUNT(*) FROM dbo.IA_Subsidiary AS s WHERE s.cPZID = N'{no}')",
        "SUM(CASE WHEN ISNULL(CONVERT(int, g.ibook), 0) = 0 THEN 1 ELSE 0 END)",
        f"({_heads(no)})",
    )
    expr = " + N'|' + ".join(f"CONVERT(varchar(40), {part})" for part in parts)
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v FROM dbo.GL_accvouch AS g WHERE {where};\n"


def left_sql(pz: str) -> str:
    """一行 <总账、存货明细账、发出商品明细、来源单据表头里仍引用该外部业务号的行数合计>。"""
    no = pz_id(pz)
    parts = (
        f"(SELECT COUNT(*) FROM dbo.GL_accvouch WHERE coutno_id = N'{no}')",
        f"(SELECT COUNT(*) FROM dbo.IA_Subsidiary WHERE cPZID = N'{no}')",
        f"(SELECT COUNT(*) FROM dbo.IA_EnSubsidiary WHERE cPZID = N'{no}')",
        _heads(no),
    )
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(12), {' + '.join(parts)}) AS v;\n"


def ia_voucher_samples() -> list[tuple[str, str]]:
    return [
        ("ia-voucher-bills", bills_sql(2026, 9)),
        ("ia-voucher-marks", marks_sql("2026IA0000000000001")),
        ("ia-voucher-left", left_sql("2026IA0000000000001")),
    ]
