"""采购入库参照到货单用例（pu_arrival_in）的回读 SQL。

只读。都返回一行 ROW:a|b|…，读不到返回空。id 只接受正整数（safe_id），再拼进 SQL。
挑单沿用 pu_settle_manual_sql.pick_sql（不批次、不保质期、不需来料检验的外购存货）。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id

# rd_link_sql 的列数。
LINK_COLS = 4


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def arr_valid_sql(line: int) -> str:
    """到货行累计合格入库数量 fValidInQuan（U8 在生成、删除采购入库时回写）。"""
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(s.fValidInQuan, 0)))"
    return _one(expr, f"FROM dbo.PU_ArrivalVouchs AS s WHERE s.Autoid = {safe_id(str(line))}")


def rd_link_sql(doc: int, line: int) -> str:
    """采购入库单：来源 cSource|表头 ipurarriveid|指向该到货行的行数|这些行的数量合计。"""
    rd, arr = safe_id(str(doc)), safe_id(str(line))
    used = f"(SELECT {{0}} FROM dbo.rdrecords01 AS b WHERE b.ID = h.ID AND b.iArrsId = {arr})"
    expr = (
        "ISNULL(h.cSource, N'') + N'|' + ISNULL(CONVERT(varchar(20), h.ipurarriveid), N'') + N'|'"
        " + CONVERT(varchar(10), " + used.format("COUNT(*)") + ") + N'|'"
        " + CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(" + used.format("SUM(b.iQuantity)") + ", 0)))"
    )
    return _one(expr, f"FROM dbo.RdRecord01 AS h WHERE h.ID = {rd}")


def pu_arr_in_samples() -> list[tuple[str, str]]:
    return [
        ("pu-arr-in-valid", arr_valid_sql(100)),
        ("pu-arr-in-link", rd_link_sql(100, 200)),
    ]
