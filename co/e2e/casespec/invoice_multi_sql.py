"""一张销售发票参照多张发货单（invoice_multi）的回读 SQL。

只读；标识符是常量，id 只接受正整数（safe_id）再拼进 SQL。挑单沿用 dispatch_add_sql.pick_sql（样例在那里登记）。
数量统一 decimal(18,4) 文本（例如 2.0000）；自定义项 cbdefine1 按库里的原值转文本（列类型由账套的自定义项决定）。
族成员列（cbdefine4 … cbdefine99 里账套有的文本列）按列目录 sys.columns 挑，不写死编号。
"""

from __future__ import annotations

import re

from co.e2e.casespec.stock_misc_sql import safe_id

# 写进族成员列的值的长度（E2EF + 4 位十六进制）。
FAMILY_WIDTH = 8
_FAMILY = re.compile(r"cbdefine([4-9]|[1-9][0-9])\Z")


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def settle_sql(line: str) -> str:
    """发货行的累计开票数量 iSettleQuantity。"""
    frm = f"FROM dbo.DispatchLists AS d WHERE d.iDLsID = {safe_id(line)}"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + _qty("d.iSettleQuantity") + " AS v\n" + frm + ";\n"


def body_define_col_sql() -> str:
    """账套有没有发票表体扩展自定义项 cbdefine1（SaleBillVouchs_extradefine）：1 有、0 没有。"""
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CASE WHEN COL_LENGTH(N'dbo.SaleBillVouchs_extradefine', N'cbdefine1') IS NULL"
        " THEN N'0' ELSE N'1' END AS v;\n"
    )


def family_col_sql() -> str:
    """表体扩展自定义项族里 cbdefine1–3 之外、账套有的一列（SaleBillVouchs_extradefine 的 cbdefine4 … cbdefine99，
    文本列且至少 FAMILY_WIDTH 个字符），取编号最小的；输出小写列名，没有时没有行。"""
    chars = "CASE WHEN TYPE_NAME(c.system_type_id) IN (N'nchar', N'nvarchar') THEN c.max_length / 2 ELSE c.max_length END"
    return (
        "SET NOCOUNT ON;\nSELECT TOP (1) N'ROW:' + LOWER(c.name) AS v\nFROM sys.columns AS c\n"
        "WHERE c.object_id = OBJECT_ID(N'dbo.SaleBillVouchs_extradefine', N'U')\n"
        "  AND (LOWER(c.name) LIKE N'cbdefine[1-9]' OR LOWER(c.name) LIKE N'cbdefine[1-9][0-9]')\n"
        "  AND LOWER(c.name) NOT IN (N'cbdefine1', N'cbdefine2', N'cbdefine3')\n"
        "  AND TYPE_NAME(c.system_type_id) IN (N'char', N'nchar', N'varchar', N'nvarchar')\n"
        f"  AND (c.max_length < 0 OR {chars} >= {FAMILY_WIDTH})\n"
        "ORDER BY LEN(c.name), c.name;\n"
    )


def family_col(cell: str) -> str:
    """family_col_sql 的结果：合规的 cbdefineN（N 为 4 到 99、无前导零）原样返回，否则空串。"""
    text = cell.strip().lower()
    return text if _FAMILY.fullmatch(text) else ""


def inv_lines_sql(inv: str, body_define: bool, family: str = "") -> str:
    """发票每一行（按 iDLsID）：iDLsID|数量|cbdlcode 等于该行发货单号为 1|cbdefine1 的值，无该列时为空
    [|family 列的值（给了 family 时才有这一段）]。"""
    ok = "CASE WHEN LTRIM(RTRIM(ISNULL(b.cbdlcode, N''))) = LTRIM(RTRIM(ISNULL(h.cDLCode, N''))) THEN N'1' ELSE N'0' END"
    sign = "ISNULL(LTRIM(RTRIM(CONVERT(nvarchar(60), x.cbdefine1))), N'')" if body_define else "N''"
    if family and family_col(family) != family:
        raise ValueError("扩展自定义项列名不合法")
    join = " LEFT JOIN dbo.SaleBillVouchs_extradefine AS x ON x.AutoID = b.AutoID" if body_define or family else ""
    expr = "CONVERT(varchar(20), b.iDLsID) + N'|' + " + _qty("b.iQuantity") + " + N'|' + " + ok + " + N'|' + " + sign
    if family:
        expr += f" + N'|' + ISNULL(LTRIM(RTRIM(CONVERT(nvarchar(60), x.{family}))), N'')"
    frm = (
        "FROM dbo.SaleBillVouchs AS b"
        " LEFT JOIN dbo.DispatchLists AS l ON l.iDLsID = b.iDLsID"
        " LEFT JOIN dbo.DispatchList AS h ON h.DLID = l.DLID" + join
        + f" WHERE b.SBVID = {safe_id(inv)} ORDER BY b.iDLsID"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def inv_count_sql(first: str, second: str) -> str:
    """指向这两条发货行的发票行数（预演前后应不变）。"""
    frm = f"FROM dbo.SaleBillVouchs AS b WHERE b.iDLsID IN ({safe_id(first)}, {safe_id(second)})"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20), COUNT(*)) AS v\n" + frm + ";\n"


def invoice_multi_samples() -> list[tuple[str, str]]:
    return [
        ("im-settle", settle_sql("100")),
        ("im-body-define-col", body_define_col_sql()),
        ("im-inv-lines", inv_lines_sql("101", True)),
        ("im-inv-lines-plain", inv_lines_sql("101", False)),
        ("im-family-col", family_col_sql()),
        ("im-inv-lines-family", inv_lines_sql("101", False, "cbdefine7")),
        ("im-inv-count", inv_count_sql("100", "102")),
    ]
