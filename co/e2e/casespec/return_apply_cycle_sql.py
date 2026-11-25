"""退货申请单写入（return_apply_cycle）的挑单和回读 SQL。

只读；标识符是常量，id 只接受正整数（safe_id），再拼进 SQL。挑单返回一行 ROW:a|b|…，挑不到返回空。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.flow_pick_sql import no_gathering_plan

# 申请 2、改成 1、再退 1：未开票可退和可申请数量都至少 2。
_ROOM_MIN = 2


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def pick_sql() -> str:
    """已审核、未关闭、非期初、不受审批流控制的蓝字发货单行，仓库非空，可申请数量和未开票可退数量都 >= 2。

    可申请 = 数量 − |累计退货| − 其他申请行未退的数量；未开票可退 = 数量 − 开票数量 − 未开票退货。一行：DLID|iDLsID|iSOsID。
    """
    pending = (
        "ISNULL((SELECT SUM(ABS(ISNULL(a.iQuantity, 0)) - ABS(ISNULL(a.fretqty, 0))) FROM dbo.SA_ReturnsApplyDetail AS a"
        " WHERE a.iDLsID = d.iDLsID), 0)"
    )
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), d.DLID) + N'|' + CONVERT(varchar(20), d.iDLsID) + N'|'
  + CONVERT(varchar(20), ISNULL(d.iSOsID, 0)) AS v
FROM dbo.DispatchLists AS d
JOIN dbo.DispatchList AS h ON h.DLID = d.DLID
WHERE ISNULL(h.bReturnFlag, 0) = 0 AND h.cVouchType = N'05' AND ISNULL(h.bFirst, 0) = 0
  AND ISNULL(h.cVerifier, N'') <> N'' AND ISNULL(h.cCloser, N'') = N'' AND ISNULL(d.cSCloser, N'') = N''
  AND ISNULL(h.iswfcontrolled, 0) = 0 AND ISNULL(d.cWhCode, N'') <> N''
  AND {no_gathering_plan("h")}
  AND ISNULL(d.iQuantity, 0) - ABS(ISNULL(d.iRetQuantity, 0)) - {pending} >= {_ROOM_MIN}
  AND ISNULL(d.iQuantity, 0) - ISNULL(d.iSettleQuantity, 0) - ISNULL(d.fretqtywkp, 0) >= {_ROOM_MIN}
ORDER BY h.DLID DESC, d.iDLsID;
"""


def cum_sql(dl_line: str) -> str:
    """发货行与订单行的累计列：iRetQuantity|fretqtywkp|fretqtyykp|订单 fretquantity|订单 iFHQuantity（没有订单行为 -）。"""
    so = "ISNULL(" + _qty("s.fretquantity") + " + N'|' + " + _qty("s.iFHQuantity") + ", N'-|-')"
    expr = _qty("d.iRetQuantity") + " + N'|' + " + _qty("d.fretqtywkp") + " + N'|' + " + _qty("d.fretqtyykp")
    frm = f"FROM dbo.DispatchLists AS d LEFT JOIN dbo.SO_SODetails AS s ON s.iSOsID = d.iSOsID WHERE d.iDLsID = {safe_id(dl_line)}"
    return _one(expr + " + N'|' + " + so, frm)


def apply_lines_sql(apply_id: str) -> str:
    """退货申请单各行：AutoID|iQuantity|fretqty|iDLsID。"""
    expr = (
        "CONVERT(varchar(20), a.AutoID) + N'|' + " + _qty("a.iQuantity") + " + N'|' + " + _qty("a.fretqty")
        + " + N'|' + CONVERT(varchar(20), ISNULL(a.iDLsID, 0))"
    )
    return _one(expr, f"FROM dbo.SA_ReturnsApplyDetail AS a WHERE a.ID = {safe_id(apply_id)} ORDER BY a.AutoID")


def link_sql(return_id: str, apply_id: str) -> str:
    """退货单上指回该申请单的行数（irtnappid = 申请行 AutoID、crtnappcode = 申请单号）。"""
    frm = (
        "FROM dbo.DispatchLists AS x JOIN dbo.SA_ReturnsApplyDetail AS a ON a.AutoID = x.irtnappid"
        " JOIN dbo.SA_ReturnsApplyMain AS m ON m.ID = a.ID AND m.cCode = x.crtnappcode"
        f" WHERE x.DLID = {safe_id(return_id)} AND m.ID = {safe_id(apply_id)}"
    )
    return _one("CONVERT(varchar(20), COUNT(*))", frm)


def ret_line_sql(return_id: str, apply_id: str) -> str:
    """退货单上第一条指回该申请单的行：iDLsID（irtnappid > 0 的退货行，用于验证改数量、删行 409）。"""
    frm = (
        "FROM dbo.DispatchLists AS x JOIN dbo.SA_ReturnsApplyDetail AS a ON a.AutoID = x.irtnappid"
        f" WHERE x.DLID = {safe_id(return_id)} AND a.ID = {safe_id(apply_id)} ORDER BY x.iDLsID"
    )
    return _one("CONVERT(varchar(20), x.iDLsID)", frm)


def return_apply_cycle_samples() -> list[tuple[str, str]]:
    return [
        ("k12-pick", pick_sql()),
        ("k12-cum", cum_sql("100")),
        ("k12-lines", apply_lines_sql("100")),
        ("k12-link", link_sql("100", "101")),
        ("k12-ret-line", ret_line_sql("100", "101")),
    ]
