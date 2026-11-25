"""销售出库两步生单（sale_out_trim）的挑单、回读和造数 SQL。

只读（set_pos_sql / clear_pos_sql 除外：只改本用例自建的发货单那一行的货位）；标识符是常量，编码只接受
[0-9A-Za-z._-]（safe_lot），id 只接受正整数（safe_id），再拼进 SQL。挑单返回一行 ROW:a|b|…，挑不到返回空。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.stock_plus_sql import _SAFE, _avail, _sale_inv, _so_template, _template_cols, safe_lot


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def _plain_stock(wh: str) -> str:
    return _avail(f"c.cWhCode = {wh} AND c.cInvCode = i.cInvCode AND ISNULL(c.cBatch, N'') = N''")


def multi_pick_sql() -> str:
    # 跨仓库多张出库单：同一存货（不管批次 / 保质期 / 序列号、无自由项）在两个不管货位的仓库里，仓库 1 可用量 >= 3
    # （发货行 A 2 + 行 B 1），仓库 2 可用量 >= 1（发货行 C 1）。客户|销售类型|部门|业务员|存货|仓库 1|仓库 2。
    second = f"""SELECT TOP (1) x.cWhCode FROM dbo.Warehouse AS x
    WHERE x.cWhCode <> w.cWhCode AND ISNULL(x.bWhPos, 0) = 0 AND ISNULL(x.bFreeze, 0) = 0
      AND LTRIM(RTRIM(x.cWhCode)) {_SAFE} AND {_plain_stock("x.cWhCode")} >= 1
    ORDER BY x.cWhCode"""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {_template_cols()} + N'|' + LTRIM(RTRIM(w2.cWhCode)) AS v
FROM dbo.Warehouse AS w
JOIN dbo.Inventory AS i ON EXISTS (SELECT 1 FROM dbo.CurrentStock AS e WHERE e.cWhCode = w.cWhCode AND e.cInvCode = i.cInvCode)
{_so_template()}
CROSS APPLY ({second}) AS w2
WHERE ISNULL(w.bWhPos, 0) = 0
  AND {_sale_inv(0)}
  AND {_plain_stock("w.cWhCode")} >= 3
ORDER BY w.cWhCode, i.cInvCode;
"""


def so_lines_sql(so: str) -> str:
    """销售订单的每一行 iSOsID（按 iSOsID）。"""
    return _one("CONVERT(varchar(20), d.iSOsID)", f"FROM dbo.SO_SODetails AS d WHERE d.ID = {safe_id(so)} ORDER BY d.iSOsID")


def disp_lines_sql(dl: str) -> str:
    """发货单的每一行：iDLsID|iSOsID|仓库|数量|累计出库（按 iDLsID）。"""
    expr = (
        "CONVERT(varchar(20), x.iDLsID) + N'|' + CONVERT(varchar(20), ISNULL(x.iSOsID, 0)) + N'|'"
        " + ISNULL(LTRIM(RTRIM(x.cWhCode)), N'') + N'|' + " + _qty("x.iQuantity") + " + N'|' + " + _qty("x.fOutQuantity")
    )
    return _one(expr, f"FROM dbo.DispatchLists AS x WHERE x.DLID = {safe_id(dl)} ORDER BY x.iDLsID")


def out_docs_sql(dl: str) -> str:
    """挂在该发货单任一行上的销售出库单 id（去重、升序）。"""
    frm = (
        "FROM (SELECT DISTINCT b.ID FROM dbo.rdrecords32 AS b JOIN dbo.DispatchLists AS x ON x.iDLsID = b.iDLsID"
        f" WHERE x.DLID = {safe_id(dl)}) AS t ORDER BY t.ID"
    )
    return _one("CONVERT(varchar(20), t.ID)", frm)


def doc_rows_sql(doc: str) -> str:
    """销售出库单的每一行：iDLsID|数量|仓库|InvPosition 行数（按 AutoID）。"""
    expr = (
        "CONVERT(varchar(20), ISNULL(b.iDLsID, 0)) + N'|' + " + _qty("b.iQuantity")
        + " + N'|' + ISNULL((SELECT LTRIM(RTRIM(h.cWhCode)) FROM dbo.RdRecord32 AS h WHERE h.ID = b.ID), N'')"
        " + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.InvPosition AS p"
        " WHERE p.RdID = b.ID AND p.RdsID = b.AutoID AND p.cvouchtype = N'32'))"
    )
    return _one(expr, f"FROM dbo.rdrecords32 AS b WHERE b.ID = {safe_id(doc)} ORDER BY b.AutoID")


# ---- 补偿用例：发货行带货位 ----


def pos_col_sql() -> str:
    """发货单表体有没有货位列 cPosition：1 / 0。"""
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + CASE WHEN COL_LENGTH(N'dbo.DispatchLists', N'cPosition') IS NULL THEN N'0' ELSE N'1' END AS v;\n"


def set_pos_sql(dl: str, line: str, pos: str) -> str:
    """把本用例自建、已审核的发货单那一行的货位写成 pos（原来为空才改）；一行：<改动行数>。"""
    return f"""SET NOCOUNT ON;
UPDATE dbo.DispatchLists SET cPosition = N'{safe_lot(pos)}'
WHERE DLID = {safe_id(dl)} AND iDLsID = {safe_id(line)} AND LTRIM(RTRIM(ISNULL(cPosition, N''))) = N'';
SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;
"""


def clear_pos_sql(dl: str, line: str) -> str:
    """清掉 set_pos_sql 写的货位（删发货单之前复原）；一行：<改动行数>。"""
    return f"""SET NOCOUNT ON;
UPDATE dbo.DispatchLists SET cPosition = NULL
WHERE DLID = {safe_id(dl)} AND iDLsID = {safe_id(line)} AND cPosition IS NOT NULL;
SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;
"""


def sale_out_trim_samples() -> list[tuple[str, str]]:
    return [
        ("trim-multi-pick", multi_pick_sql()),
        ("trim-so-lines", so_lines_sql("100")),
        ("trim-disp-lines", disp_lines_sql("100")),
        ("trim-out-docs", out_docs_sql("100")),
        ("trim-doc-rows", doc_rows_sql("100")),
        ("trim-pos-col", pos_col_sql()),
        ("trim-set-pos", set_pos_sql("100", "101", "A01")),
        ("trim-clear-pos", clear_pos_sql("100", "101")),
    ]
