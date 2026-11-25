"""无来源新增、产成品入库参照生产订单的挑单与回读 SQL。只读；标识符都是常量，整数先转 int 再拼。

每条返回一行 ROW:<选项值>|<字段>|…；选项值是账套选项的原文（没有这一项时为空），用例按它决定走拒绝还是成功路径。
"""

from __future__ import annotations

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def _code(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) {_SAFE} AND LTRIM(RTRIM(ISNULL({col}, N''))) <> N'' AND LEN(LTRIM(RTRIM({col}))) <= 12"


def _opt(sys_id: str, name: str) -> str:
    return (
        "ISNULL((SELECT TOP (1) LTRIM(RTRIM(o.cValue)) FROM dbo.AccInformation AS o"
        f" WHERE o.cSysID = N'{sys_id}' AND o.cName = N'{name}'), N'')"
    )


_PLAIN_INV = (
    "ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0 AND ISNULL(i.iGroupType, 0) <> 2"
    " AND ISNULL(i.bFree1, 0) = 0 AND ISNULL(i.bFree2, 0) = 0 AND ISNULL(i.bFree3, 0) = 0 AND ISNULL(i.bFree4, 0) = 0"
    " AND ISNULL(i.bFree5, 0) = 0 AND ISNULL(i.bFree6, 0) = 0 AND ISNULL(i.bFree7, 0) = 0 AND ISNULL(i.bFree8, 0) = 0"
    " AND ISNULL(i.bFree9, 0) = 0 AND ISNULL(i.bFree10, 0) = 0"
)
_STOCKED = (
    "EXISTS (SELECT 1 FROM dbo.CurrentStock AS s WHERE s.cWhCode = {wh} AND s.cInvCode = {inv}"
    " AND s.iQuantity >= 10)"
)


def sale_pick_sql() -> str:
    # 普通销售蓝字发货单里的客户、销售类型、仓库、存货（无批次、保质期、自由项、浮动换算，现存量 >= 10，非货位仓）、含税单价、部门
    # （U8 无来源发货单、先开票要部门）。
    stocked = _STOCKED.format(wh="d.cWhCode", inv="d.cInvCode")
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + {_opt("SA", "bMustSO_ptxs")} + N'|' + LTRIM(RTRIM(h.cCusCode)) + N'|' + LTRIM(RTRIM(h.cSTCode)) + N'|'
  + LTRIM(RTRIM(d.cWhCode)) + N'|' + LTRIM(RTRIM(d.cInvCode)) + N'|'
  + CONVERT(varchar(40), CONVERT(decimal(28, 2), ISNULL(d.iTaxUnitPrice, 0))) + N'|'
  + ISNULL(LTRIM(RTRIM(h.cDepCode)), N'') AS v
FROM dbo.DispatchList AS h
JOIN dbo.DispatchLists AS d ON d.DLID = h.DLID
JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode
JOIN dbo.Warehouse AS w ON w.cWhCode = d.cWhCode
WHERE h.cVouchType = N'05' AND ISNULL(h.bReturnFlag, 0) = 0 AND h.cBusType = N'普通销售'
  AND ISNULL(h.cexch_name, N'人民币') = N'人民币' AND ISNULL(w.bWhPos, 0) = 0
  AND {_PLAIN_INV} AND {stocked}
  AND {_code("h.cCusCode")} AND {_code("h.cSTCode")} AND {_code("d.cWhCode")} AND {_code("d.cInvCode")}
ORDER BY h.DLID DESC, d.iDLsID;
"""


def arrival_pick_sql() -> str:
    # 普通采购到货单里的本币供应商和存货（外购、免检、非浮动换算），另挑部门和采购类型（U8 无来源到货单要部门；
    # 账套可能没设默认采购类型）。部门：末级、未停用，优先该到货单的部门；采购类型优先默认的。
    # 选项|供应商|存货|仓库|部门|采购类型。
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + {_opt("PU", "bPTHavePO")} + N'|' + LTRIM(RTRIM(h.cVenCode)) + N'|' + LTRIM(RTRIM(d.cInvCode)) + N'|'
  + LTRIM(RTRIM(ISNULL(d.cWhCode, N''))) + N'|' + LTRIM(RTRIM(dep.cDepCode)) + N'|' + LTRIM(RTRIM(pt.cPTCode)) AS v
FROM dbo.PU_ArrivalVouch AS h
JOIN dbo.PU_ArrivalVouchs AS d ON d.ID = h.ID
JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode
JOIN dbo.Vendor AS n ON n.cVenCode = h.cVenCode
CROSS APPLY (
  SELECT TOP (1) p.cDepCode FROM dbo.Department AS p
  WHERE p.bDepEnd = 1 AND p.dDepEndDate IS NULL AND {_code("p.cDepCode")}
  ORDER BY CASE WHEN p.cDepCode = h.cDepCode THEN 0 ELSE 1 END, p.cDepCode
) AS dep
CROSS APPLY (
  SELECT TOP (1) t.cPTCode FROM dbo.PurchaseType AS t WHERE {_code("t.cPTCode")}
  ORDER BY ISNULL(t.bDefault, 0) DESC, t.cPTCode
) AS pt
WHERE h.cBusType = N'普通采购' AND ISNULL(h.iBillType, 0) = 0 AND ISNULL(n.cVenExch_name, N'人民币') = N'人民币'
  AND ISNULL(i.bPurchase, 0) = 1 AND ISNULL(i.bPropertyCheck, 0) = 0 AND ISNULL(i.iGroupType, 0) <> 2
  AND {_code("h.cVenCode")} AND {_code("d.cInvCode")}
  AND LTRIM(RTRIM(ISNULL(d.cWhCode, N''))) {_SAFE}
ORDER BY h.ID DESC, d.Autoid;
"""


def material_pick_sql() -> str:
    # 材料出库用过的存货：普通存货、现存量 >= 10 的非货位仓；部门取那张出库单的（实测 U8 要部门）。
    stocked = _STOCKED.format(wh="w.cWhCode", inv="i.cInvCode")
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + {_opt("ST", "ballowAddnewVouch")} + N'|' + LTRIM(RTRIM(w.cWhCode)) + N'|' + LTRIM(RTRIM(i.cInvCode))
  + N'|' + ISNULL(LTRIM(RTRIM(r.cDepCode)), N'') AS v
FROM dbo.rdrecords11 AS b
JOIN dbo.rdrecord11 AS r ON r.ID = b.ID
JOIN dbo.Inventory AS i ON i.cInvCode = b.cInvCode
JOIN dbo.Warehouse AS w ON w.cWhCode = r.cWhCode
WHERE ISNULL(w.bWhPos, 0) = 0 AND {_PLAIN_INV} AND {stocked}
  AND {_code("w.cWhCode")} AND {_code("i.cInvCode")}
ORDER BY r.ID DESC, b.AutoID;
"""


def _mo_where(inspect: bool) -> str:
    # inspect=True：要检验的行（QcFlag=1 且 ST.bQuality 打开）；False：不要检验的行，剩余 >= 2（入 1 个不会满额自动关闭）。
    quality = f"UPPER({_opt('ST', 'bQuality')}) IN (N'TRUE', N'1')"
    if inspect:
        return f"ISNULL(d.QcFlag, 0) = 1 AND {quality} AND d.Qty - ISNULL(d.QualifiedInQty, 0) >= 1"
    return f"(ISNULL(d.QcFlag, 0) = 0 OR NOT ({quality})) AND d.Qty - ISNULL(d.QualifiedInQty, 0) >= 2"


def mo_pick_sql(inspect: bool) -> str:
    # 已审核未关闭、不走审批流的生产订单行：MoId|MoDId|入库仓|合格入库数量。
    # 入库仓：订单行仓库，否则存货默认仓，否则该存货最近一张产成品入库的仓库；只要非货位仓。
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + CONVERT(varchar(20), d.MoId) + N'|' + CONVERT(varchar(20), d.MoDId) + N'|' + LTRIM(RTRIM(w.cWhCode)) + N'|'
  + CONVERT(varchar(40), CONVERT(decimal(28, 6), ISNULL(d.QualifiedInQty, 0))) AS v
FROM dbo.mom_orderdetail AS d
JOIN dbo.Inventory AS i ON i.cInvCode = d.InvCode
OUTER APPLY (
  SELECT TOP (1) r.cWhCode FROM dbo.rdrecords10 AS b JOIN dbo.rdrecord10 AS r ON r.ID = b.ID
  WHERE b.cInvCode = d.InvCode ORDER BY r.ID DESC
) AS last
JOIN dbo.Warehouse AS w
  ON w.cWhCode = COALESCE(NULLIF(LTRIM(RTRIM(d.WhCode)), N''), NULLIF(LTRIM(RTRIM(i.cDefWareHouse)), N''), last.cWhCode)
WHERE d.Status = 3 AND ISNULL(d.IsWFControlled, 0) = 0 AND ISNULL(w.bWhPos, 0) = 0
  AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0
  AND {_mo_where(inspect)} AND {_code("w.cWhCode")}
ORDER BY d.MoDId DESC;
"""


def mo_qty_sql(mod_id: int) -> str:
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(40), CONVERT(decimal(28, 6), ISNULL(d.QualifiedInQty, 0))) AS v
FROM dbo.mom_orderdetail AS d WHERE d.MoDId = {int(mod_id)};
"""


def made_dispatch_sql(invoice_id: int) -> str:
    # 先开票：U8 按发票生成的发货单（SBVID 指回发票）。
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(20), h.DLID) AS v FROM dbo.DispatchList AS h WHERE h.SBVID = {int(invoice_id)};
"""
