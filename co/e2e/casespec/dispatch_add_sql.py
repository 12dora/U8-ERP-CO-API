"""发货单修改新增行（dispatch_add）的挑单和回读 SQL。

只读；标识符是常量，编码只接受 [0-9A-Za-z._-]（safe_lot），id 只接受正整数（safe_id），再拼进 SQL。
挑单返回一行 ROW:a|b|…，挑不到返回空。订单行的 iSOsID 列表、发货单各行沿用 sale_out_trim_sql（样例也在那里登记）。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.stock_plus_sql import _avail, _sale_inv, _so_template, _template_cols

# 发货 2 + 新增 1，再留 1 的余量。
_STOCK_MIN = 4


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def pick_sql() -> str:
    """不管货位的仓库里（无批号）可用量 >= 4 的可销售简单存货，客户等取最近一张已审核的销售订单。

    一行：客户|销售类型|部门|业务员|存货|仓库。
    """
    stock = _avail("c.cWhCode = w.cWhCode AND c.cInvCode = i.cInvCode AND ISNULL(c.cBatch, N'') = N''")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {_template_cols()} AS v
FROM dbo.Warehouse AS w
JOIN dbo.Inventory AS i ON EXISTS (SELECT 1 FROM dbo.CurrentStock AS e WHERE e.cWhCode = w.cWhCode AND e.cInvCode = i.cInvCode)
{_so_template()}
WHERE ISNULL(w.bWhPos, 0) = 0
  AND {_sale_inv(0)}
  AND {stock} >= {_STOCK_MIN}
ORDER BY w.cWhCode, i.cInvCode;
"""


def fh_sql(so: str) -> str:
    """销售订单每一行：iSOsID|累计发货数量 iFHQuantity（按 iSOsID）。"""
    expr = "CONVERT(varchar(20), d.iSOsID) + N'|' + " + _qty("d.iFHQuantity")
    return _one(expr, f"FROM dbo.SO_SODetails AS d WHERE d.ID = {safe_id(so)} ORDER BY d.iSOsID")


def added_sql(dl: str, so_line: str) -> str:
    """发货单上挂该订单行的行：数量|仓库|金额对得上（数量 × 含税单价 = 价税合计、数量 × 无税单价 = 金额，差 <= 0.02 为 1）。"""
    ok = (
        "CASE WHEN ABS(ISNULL(x.iSum, 0) - ROUND(ISNULL(x.iQuantity, 0) * ISNULL(x.iTaxUnitPrice, 0), 2)) <= 0.02"
        " AND ABS(ISNULL(x.iMoney, 0) - ROUND(ISNULL(x.iQuantity, 0) * ISNULL(x.iUnitPrice, 0), 2)) <= 0.02"
        " THEN N'1' ELSE N'0' END"
    )
    expr = _qty("x.iQuantity") + " + N'|' + ISNULL(LTRIM(RTRIM(x.cWhCode)), N'') + N'|' + " + ok
    frm = f"FROM dbo.DispatchLists AS x WHERE x.DLID = {safe_id(dl)} AND x.iSOsID = {safe_id(so_line)} ORDER BY x.iDLsID"
    return _one(expr, frm)


def dispatch_add_samples() -> list[tuple[str, str]]:
    return [
        ("da-pick", pick_sql()),
        ("da-fh", fh_sql("100")),
        ("da-added", added_sql("100", "101")),
    ]
