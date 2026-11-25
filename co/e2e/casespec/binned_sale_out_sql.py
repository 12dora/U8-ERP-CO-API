"""B4 有货位记录的销售出库删除（binned_sale_out）的回读 SQL。挑数沿用 stock_plus_sql.bin_split_sql。

只读；标识符是常量，编码只接受 [0-9A-Za-z._-]（safe_lot），id 只接受正整数（safe_id），再拼进 SQL。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.stock_plus_sql import safe_lot


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def pos_sum_sql(wh: str, pos: str, inv: str) -> str:
    """该仓库、货位、存货（无批号）的货位存量 InvPositionSum.iQuantity 合计。"""
    frm = (
        f"FROM dbo.InvPositionSum AS p WHERE p.cWhCode = N'{safe_lot(wh)}' AND p.cPosCode = N'{safe_lot(pos)}'"
        f" AND p.cInvCode = N'{safe_lot(inv)}' AND ISNULL(p.cBatch, N'') = N''"
    )
    return _one(_qty("SUM(p.iQuantity)"), frm)


def stock_sum_sql(wh: str, inv: str) -> str:
    """该仓库、存货（无批号）的现存量 CurrentStock.iQuantity 合计。"""
    frm = (
        f"FROM dbo.CurrentStock AS c WHERE c.cWhCode = N'{safe_lot(wh)}' AND c.cInvCode = N'{safe_lot(inv)}'"
        " AND ISNULL(c.cBatch, N'') = N''"
    )
    return _one(_qty("SUM(c.iQuantity)"), frm)


def out_head_sql(doc: str) -> str:
    """销售出库单表头行数（删除后应为 0）。"""
    return _one("CONVERT(varchar(10), COUNT(*))", f"FROM dbo.RdRecord32 AS h WHERE h.ID = {safe_id(doc)}")


def binned_sale_out_samples() -> list[tuple[str, str]]:
    return [
        ("b4-pos-sum", pos_sum_sql("01", "A01", "0001")),
        ("b4-stock-sum", stock_sum_sql("01", "0001")),
        ("b4-out-head", out_head_sql("100")),
    ]
