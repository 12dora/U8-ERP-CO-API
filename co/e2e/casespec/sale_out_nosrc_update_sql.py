"""来源库存的销售出库单修改（sale_out_nosrc_update）的账套选项、挑单和回读 SQL。

只读；标识符都是常量，单据 id 只接受正整数（safe_id），再拼进 SQL。挑单返回一行 ROW:a|b|…，挑不到返回空。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.srcless_sql import _code, _opt
from co.e2e.casespec.stock_plus_sql import _SAFE, _avail, _sale_inv

# 新建 2、改成 1、再加 1 行 1：仓库里同一存货的可用量至少 3。
NEED = 3


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _latest(col: str) -> str:
    # 最近一张销售出库单上的该列（客户、部门、收发类别），挑档案时优先它。
    return f"(SELECT TOP (1) r.{col} FROM dbo.rdrecord32 AS r WHERE r.{col} IS NOT NULL ORDER BY r.ID DESC)"


def gate_sql() -> str:
    """销售管理启用日期|库存选项「销售出库单由销售系统生成」：前者非空或后者为真时桥不收无来源销售出库单。"""
    return _one(_opt("SA", "dSaleStartDate") + " + N'|' + " + _opt("ST", "bSAcreat"), "")


def pick_sql() -> str:
    """仓库|存货|客户|部门|收发类别|销售类型|另一个仓库（后三项可为空）。

    仓库不管货位、未冻结；存货可销售、不管批次 / 保质期 / 序列号、无自由项、单计量，该仓库不分批的可用量 >= NEED。
    客户、部门（末级、未停用）、收发类别（出库末级）优先最近一张销售出库单上的；销售类型优先缺省的。
    """
    stock = _avail("c.cWhCode = w.cWhCode AND c.cInvCode = i.cInvCode AND ISNULL(c.cBatch, N'') = N''")
    expr = (
        "LTRIM(RTRIM(w.cWhCode)) + N'|' + LTRIM(RTRIM(i.cInvCode)) + N'|' + LTRIM(RTRIM(cu.cCusCode)) + N'|'"
        " + LTRIM(RTRIM(dp.cDepCode)) + N'|' + ISNULL(LTRIM(RTRIM(rd.cRdCode)), N'') + N'|'"
        " + ISNULL(LTRIM(RTRIM(st.cSTCode)), N'') + N'|' + ISNULL(LTRIM(RTRIM(w2.cWhCode)), N'')"
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
  ORDER BY ISNULL(t.bDefault, 0) DESC, t.cSTCode
) AS st
OUTER APPLY (
  SELECT TOP (1) x.cWhCode FROM dbo.Warehouse AS x
  WHERE x.cWhCode <> w.cWhCode AND ISNULL(x.bWhPos, 0) = 0 AND LTRIM(RTRIM(x.cWhCode)) {_SAFE}
  ORDER BY x.cWhCode
) AS w2
WHERE ISNULL(w.bWhPos, 0) = 0
  AND {_sale_inv(0)}
  AND {stock} >= {NEED}
ORDER BY w.cWhCode, i.cInvCode;
"""


def head_sql(doc: str) -> str:
    """来源|业务类型|VT_ID|表头备注|仓库。"""
    expr = (
        "ISNULL(h.cSource, N'') + N'|' + ISNULL(h.cBusType, N'') + N'|' + CONVERT(varchar(12), ISNULL(h.VT_ID, 0))"
        " + N'|' + ISNULL(h.cMemo, N'') + N'|' + ISNULL(LTRIM(RTRIM(h.cWhCode)), N'')"
    )
    return _one(expr, f"FROM dbo.rdrecord32 AS h WHERE h.ID = {safe_id(doc)}")


def lines_sql(doc: str) -> str:
    """每一行：AutoID|数量|行备注|是否挂发货单行（iDLsID 非空为 1），按 AutoID。"""
    expr = (
        "CONVERT(varchar(20), b.AutoID) + N'|' + CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(b.iQuantity, 0)))"
        " + N'|' + ISNULL(b.cbMemo, N'') + N'|' + CASE WHEN b.iDLsID IS NULL THEN N'0' ELSE N'1' END"
    )
    return _one(expr, f"FROM dbo.rdrecords32 AS b WHERE b.ID = {safe_id(doc)} ORDER BY b.AutoID")


def sale_out_nosrc_update_samples() -> list[tuple[str, str]]:
    return [
        ("sou-gate", gate_sql()),
        ("sou-pick", pick_sql()),
        ("sou-head", head_sql("100")),
        ("sou-lines", lines_sql("100")),
    ]
