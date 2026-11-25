"""红冲蓝字销售发票（sale_blue_red）的回读 SQL。

只读；标识符是常量，id 只接受正整数（safe_id）再拼进 SQL。挑单沿用 dispatch_add_sql.pick_sql（样例在那里登记）。
数量统一 decimal(18,4) 文本（例如 -1.0000）。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def blue_line_sql(inv: str) -> str:
    """蓝字发票第一行：AutoID|发票类型 cVouchType（用例自己生成的发票只有一行）。"""
    expr = "CONVERT(varchar(20), b.AutoID) + N'|' + LTRIM(RTRIM(ISNULL(h.cVouchType, N'')))"
    frm = (
        "FROM dbo.SaleBillVouch AS h JOIN dbo.SaleBillVouchs AS b ON b.SBVID = h.SBVID"
        f" WHERE h.SBVID = {safe_id(inv)} ORDER BY b.AutoID"
    )
    return _one(expr, frm).replace("SELECT N'ROW:'", "SELECT TOP (1) N'ROW:'", 1)


def red_shape_sql(red: str, blue_line: str) -> str:
    """红字发票：bReturnFlag|cVouchType|数量合计|行数|iSBVID 指向该蓝字行的行数。红冲 1 个应是 1|类型|-1.0000|1|1。"""
    expr = (
        "CONVERT(varchar(5), ISNULL(MAX(CONVERT(int, h.bReturnFlag)), 0)) + N'|' + "
        "LTRIM(RTRIM(ISNULL(MAX(h.cVouchType), N''))) + N'|' + " + _qty("SUM(b.iQuantity)") + " + N'|' + "
        "CONVERT(varchar(20), COUNT(b.AutoID)) + N'|' + "
        f"CONVERT(varchar(20), SUM(CASE WHEN b.iSBVID = {safe_id(blue_line)} THEN 1 ELSE 0 END))"
    )
    frm = f"FROM dbo.SaleBillVouch AS h JOIN dbo.SaleBillVouchs AS b ON b.SBVID = h.SBVID WHERE h.SBVID = {safe_id(red)}"
    return _one(expr, frm)


def inv_gone_sql(inv: str) -> str:
    """发票表头加表体的行数，删除后应为 0。"""
    ref = safe_id(inv)
    expr = (
        f"CONVERT(varchar(20), (SELECT COUNT(*) FROM dbo.SaleBillVouch AS h WHERE h.SBVID = {ref})"
        f" + (SELECT COUNT(*) FROM dbo.SaleBillVouchs AS b WHERE b.SBVID = {ref}))"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


def blue_red_samples() -> list[tuple[str, str]]:
    return [
        ("br-blue-line", blue_line_sql("100")),
        ("br-red-shape", red_shape_sql("101", "102")),
        ("br-gone", inv_gone_sql("101")),
    ]
