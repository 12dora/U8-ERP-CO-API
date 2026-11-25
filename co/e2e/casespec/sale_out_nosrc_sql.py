"""无来源销售出库单新增（sale_out_nosrc）的账套选项、挑单和回读 SQL。

只读；标识符都是常量，单据 id 只接受正整数（safe_id），再拼进 SQL。每条返回一行 ROW:a|b|…，挑不到返回空。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.srcless_sql import _code, _opt
from co.e2e.casespec.stock_plus_sql import _avail, _sale_inv

# 新建 1 行、数量 1：该仓库不分批的可用量至少 1。
NEED = 1


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _latest(col: str) -> str:
    # 账套里最近一张销售出库单上的该列（客户、部门、收发类别、销售类型），挑档案时优先它。
    return f"(SELECT TOP (1) r.{col} FROM dbo.rdrecord32 AS r WHERE r.{col} IS NOT NULL ORDER BY r.ID DESC)"


def gate_sql() -> str:
    """销售管理启用日期|ST.bSAcreat：前者非空或后者为真时桥 409（与 StockSaleOut.RefuseOption 同口径）。"""
    return _one(_opt("SA", "dSaleStartDate") + " + N'|' + " + _opt("ST", "bSAcreat"), "")


def pick_sql() -> str:
    """仓库|存货|客户|部门|收发类别|销售类型（后两项可为空）。

    仓库不管货位、未冻结；存货可销售、不管批次 / 保质期 / 序列号、无自由项，该仓库不分批的可用量 >= NEED。
    客户、部门（末级、未停用）、收发类别（出库末级）、销售类型优先最近一张销售出库单上的。
    """
    stock = _avail("c.cWhCode = w.cWhCode AND c.cInvCode = i.cInvCode AND ISNULL(c.cBatch, N'') = N''")
    expr = (
        "LTRIM(RTRIM(w.cWhCode)) + N'|' + LTRIM(RTRIM(i.cInvCode)) + N'|' + LTRIM(RTRIM(cu.cCusCode)) + N'|'"
        " + LTRIM(RTRIM(dp.cDepCode)) + N'|' + ISNULL(LTRIM(RTRIM(rd.cRdCode)), N'') + N'|'"
        " + ISNULL(LTRIM(RTRIM(st.cSTCode)), N'')"
    )
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {expr} AS v
FROM dbo.Warehouse AS w
JOIN dbo.Inventory AS i ON EXISTS (SELECT 1 FROM dbo.CurrentStock AS e WHERE e.cWhCode = w.cWhCode AND e.cInvCode = i.cInvCode)
CROSS APPLY (
  SELECT TOP (1) c.cCusCode FROM dbo.Customer AS c WHERE {_code("c.cCusCode")}
  ORDER BY CASE WHEN c.cCusCode = {_latest("cCusCode")} THEN 0 ELSE 1 END, c.cCusCode
) AS cu
CROSS APPLY (
  SELECT TOP (1) p.cDepCode FROM dbo.Department AS p
  WHERE p.bDepEnd = 1 AND p.dDepEndDate IS NULL AND {_code("p.cDepCode")}
  ORDER BY CASE WHEN p.cDepCode = {_latest("cDepCode")} THEN 0 ELSE 1 END, p.cDepCode
) AS dp
OUTER APPLY (
  SELECT TOP (1) s.cRdCode FROM dbo.Rd_Style AS s
  WHERE ISNULL(s.bRdFlag, 0) = 0 AND ISNULL(s.bRdEnd, 0) = 1 AND {_code("s.cRdCode")}
  ORDER BY CASE WHEN s.cRdCode = {_latest("cRdCode")} THEN 0 ELSE 1 END, s.cRdCode
) AS rd
OUTER APPLY (
  SELECT TOP (1) t.cSTCode FROM dbo.SaleType AS t WHERE {_code("t.cSTCode")}
  ORDER BY CASE WHEN t.cSTCode = {_latest("cSTCode")} THEN 0 ELSE 1 END, ISNULL(t.bDefault, 0) DESC, t.cSTCode
) AS st
WHERE ISNULL(w.bWhPos, 0) = 0
  AND {_sale_inv(0)}
  AND {stock} >= {NEED}
ORDER BY {stock} DESC, w.cWhCode, i.cInvCode;
"""


def head_sql(doc: str) -> str:
    """来源|业务类型|VT_ID|挂了发货单行的表体行数（iDLsID 非空）|表体行数。"""
    expr = (
        "ISNULL(h.cSource, N'') + N'|' + ISNULL(h.cBusType, N'') + N'|' + CONVERT(varchar(12), ISNULL(h.VT_ID, 0))"
        " + N'|' + CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.rdrecords32 AS b WHERE b.ID = h.ID AND b.iDLsID IS NOT NULL))"
        " + N'|' + CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.rdrecords32 AS b WHERE b.ID = h.ID))"
    )
    return _one(expr, f"FROM dbo.rdrecord32 AS h WHERE h.ID = {safe_id(doc)}")


def max_id_sql() -> str:
    """销售出库单表头的最大主键（没有单据时 0）：拒绝、预演之后不能变。"""
    return _one("CONVERT(varchar(20), ISNULL(MAX(h.ID), 0))", "FROM dbo.rdrecord32 AS h")


def sale_out_nosrc_samples() -> list[tuple[str, str]]:
    return [
        ("son-gate", gate_sql()),
        ("son-pick", pick_sql()),
        ("son-head", head_sql("100")),
        ("son-max", max_id_sql()),
    ]
