"""挑单：退货来源行、不良品处理单、无来源采购入库的主数据、币种名称和物料清单母件。

标识符都是常量；返回值带 ROW: 前缀。挑不到就不写 bag，用例记 SKIP 或 N/A。
"""

from __future__ import annotations

from co.e2e.casespec.returns_misc_readback_sql import SR_ROOM, custom_read_samples
from co.e2e.casespec.flow_pick_sql import invoice_src_body, no_gathering_plan

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def _blank(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) = N''"


def _filled(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) <> N''"


def _short(col: str) -> str:
    # picks._CODE 只收 12 个字符以内的编码；超长的在 SQL 里就跳过，免得整组被丢掉。
    return f"LEN(LTRIM(RTRIM({col}))) <= 12"


def sale_return_src_sql() -> str:
    # 已审核、未关闭的蓝字发货行（非期初、挂未关闭的订单行）：可退 >= 2，订单行累计发货 >= 2。
    # 避开发票生单用例挑的那张发货单（它在本用例之前开票，会占掉未开票可退数量）。发货单 DLID|发货行 iDLsID。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.DLID) + N'|' + CONVERT(varchar(20), x.iDLsID) AS v
FROM dbo.DispatchList AS h
JOIN dbo.DispatchLists AS x ON x.DLID = h.DLID
JOIN dbo.SO_SODetails AS so ON so.iSOsID = x.iSOsID
WHERE h.cVouchType = N'05'
  AND ISNULL(h.bReturnFlag, 0) = 0
  AND ISNULL(h.bFirst, 0) = 0
  AND {_filled("h.cVerifier")}
  AND {_blank("h.cCloser")}
  AND {no_gathering_plan("h")}
  AND {_blank("x.cSCloser")}
  AND {_blank("so.cSCloser")}
  AND ISNULL(so.iFHQuantity, 0) >= 2
  AND {SR_ROOM} >= 2
  AND h.DLID <> ISNULL((
    SELECT TOP (1) d.DLID
{invoice_src_body()}    ORDER BY d.DLID DESC, l.iDLsID
  ), 0)
ORDER BY h.DLID DESC, x.iDLsID;
"""


def _no_free(alias: str) -> str:
    cols = " + ".join(f"ISNULL({alias}.cFree{i}, N'')" for i in range(1, 11))
    return f"LTRIM(RTRIM({cols})) = N''"


def purchase_return_src_sql() -> str:
    # 已审核、未关闭的蓝字普通采购到货行，挂未关闭的采购订单行：合格数量减已退 >= 1、到货减已退 >= 1、
    # 订单行净到货 >= 1。同桥的前提：有单价、无自由项、存货无单位组。到货单 ID|行 Autoid。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), a.ID) + N'|' + CONVERT(varchar(20), s.Autoid) AS v
FROM dbo.PU_ArrivalVouch AS a
JOIN dbo.PU_ArrivalVouchs AS s ON s.ID = a.ID
JOIN dbo.PO_Podetails AS d ON d.ID = s.iPOsID
JOIN dbo.PO_Pomain AS m ON m.POID = d.POID
JOIN dbo.Inventory AS i ON i.cInvCode = s.cInvCode
WHERE ISNULL(a.iBillType, 0) = 0
  AND a.cBusType = N'普通采购'
  AND {_filled("a.cverifier")}
  AND {_blank("a.ccloser")}
  AND {_blank("s.cbcloser")}
  AND {_blank("d.cbCloser")}
  AND {_blank("m.cCloser")}
  AND ISNULL(s.fValidInQuan, 0) - ISNULL(s.fRetQuantity, 0) >= 1
  AND ISNULL(s.iQuantity, 0) - ISNULL(s.fRetQuantity, 0) >= 1
  AND ISNULL(d.iArrQTY, 0) - ISNULL(d.fPoRetQuantity, 0) >= 1
  AND (CASE WHEN ISNULL(s.bTaxCost, 0) = 1 THEN s.iOriTaxCost ELSE s.iOriCost END) IS NOT NULL
  AND {_no_free("s")}
  AND ISNULL(i.iGroupType, 0) = 0
ORDER BY a.ID DESC, s.Autoid;
"""


def qm_reject_src_sql() -> str:
    # 已审核（审批流须审批完成）、来源生产订单的产品不良品处理单行：处理流程 2（入库）、未标已入库、还没有入库行，
    # 订单行仍是审核状态。处理单 ID|表体 AUTOID|仓库|全部处理后数量|部分数量（拒绝用例用）。
    # 仓库取处理后存货最常用的产成品入库仓库（不管货位、未冻结）；批次存货要求处理单带处理后批号。
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + CONVERT(varchar(20), r.ID) + N'|' + CONVERT(varchar(20), s.AUTOID) + N'|' + wh.code + N'|'
  + CONVERT(varchar(40), n.qty) + N'|'
  + CONVERT(varchar(40), CONVERT(decimal(18,4), CASE WHEN n.qty > 1 THEN n.qty - 1 ELSE n.qty / 2 END)) AS v
FROM dbo.QMREJECTVOUCHER AS r
JOIN dbo.QMREJECTVOUCHERS AS s ON s.ID = r.ID
JOIN dbo.mom_orderdetail AS d
  ON d.MoDId = CASE WHEN ISNULL(r.ISOURCEPROORDERAUTOID, 0) <> 0 THEN r.ISOURCEPROORDERAUTOID ELSE r.SOURCEAUTOID END
CROSS APPLY (
  SELECT CONVERT(decimal(18,4), COALESCE(s.FDIMQUANTITY, s.FQUANTITY, 0)) AS qty,
    COALESCE(NULLIF(LTRIM(RTRIM(s.CDIMINVCODE)), N''), r.CINVCODE) AS inv
) AS n
JOIN dbo.Inventory AS i ON i.cInvCode = n.inv
CROSS APPLY (
  SELECT TOP (1) LTRIM(RTRIM(h.cWhCode)) AS code
  FROM dbo.rdrecord10 AS h
  JOIN dbo.rdrecords10 AS b ON b.ID = h.ID
  JOIN dbo.Warehouse AS w ON w.cWhCode = h.cWhCode
  WHERE b.cInvCode = n.inv
    AND w.bWhPos = 0
    AND ISNULL(w.bFreeze, 0) = 0
    AND LTRIM(RTRIM(h.cWhCode)) {_SAFE} AND {_short("h.cWhCode")}
  GROUP BY h.cWhCode
  ORDER BY COUNT(*) DESC, h.cWhCode
) AS wh
WHERE r.CVOUCHTYPE = N'QM06'
  AND r.CSOURCE = N'生产订单'
  AND {_filled("r.CVERIFIER")}
  AND (ISNULL(r.IsWfControlled, 0) = 0 OR r.iVerifyStateNew = 2)
  AND s.IDISPOSEFLOW = 2
  AND ISNULL(s.BFLAG, 0) = 0
  AND NOT EXISTS (SELECT 1 FROM dbo.rdrecords10 AS x WHERE x.iRejectIds = s.AUTOID)
  AND d.Status = 3
  AND n.qty > 0
  AND LEN(CONVERT(varchar(40), n.qty)) <= 12
  AND (ISNULL(i.bInvBatch, 0) = 0 OR {_filled("s.CDIMBATCH")})
ORDER BY r.ID DESC, s.AUTOID;
"""


def purchase_in_nosrc_sql() -> str:
    # 只在采购选项「普通业务必有订单」打开时返回一行（否则用例记 N/A，回归不改账套选项）。
    # 取一张普通采购入库单的仓库|供应商|部门|存货，正文能过登录前的校验即可，桥在登录后先按选项拒绝。
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + LTRIM(RTRIM(r.cWhCode)) + N'|' + LTRIM(RTRIM(r.cVenCode)) + N'|' + LTRIM(RTRIM(r.cDepCode)) + N'|'
  + LTRIM(RTRIM(s.cInvCode)) AS v
FROM dbo.RdRecord01 AS r
JOIN dbo.rdrecords01 AS s ON s.ID = r.ID
WHERE EXISTS (
    SELECT 1 FROM dbo.AccInformation AS o
    WHERE o.cSysID = N'PU' AND o.cName = N'bPTHavePO' AND UPPER(LTRIM(RTRIM(o.cValue))) IN (N'TRUE', N'1')
  )
  AND r.cBusType = N'普通采购'
  AND LTRIM(RTRIM(ISNULL(r.cWhCode, N''))) {_SAFE} AND {_filled("r.cWhCode")} AND {_short("r.cWhCode")}
  AND LTRIM(RTRIM(ISNULL(r.cVenCode, N''))) {_SAFE} AND {_filled("r.cVenCode")} AND {_short("r.cVenCode")}
  AND LTRIM(RTRIM(ISNULL(r.cDepCode, N''))) {_SAFE} AND {_filled("r.cDepCode")} AND {_short("r.cDepCode")}
  AND LTRIM(RTRIM(s.cInvCode)) {_SAFE} AND {_short("s.cInvCode")}
ORDER BY r.ID DESC, s.AutoID;
"""


def currency_sql() -> str:
    # 币种档案按名称读取：取名称最小的一个。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(c.cexch_name)) AS v
FROM dbo.foreigncurrency AS c
WHERE LTRIM(RTRIM(ISNULL(c.cexch_name, N''))) <> N''
ORDER BY c.cexch_name;
"""


def bom_parent_sql() -> str:
    # 今天有效、已审核的主物料清单母件，且有子件：存货编码。母件不是模型、选项类、ATO 模型（seed 的 ato 固定数据）。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(bp.InvCode)) AS v
FROM dbo.bom_bom AS b
JOIN dbo.bom_parent AS p ON p.BomId = b.BomId
JOIN dbo.bas_part AS bp ON bp.PartId = p.ParentId
JOIN dbo.Inventory AS i ON i.cInvCode = bp.InvCode
WHERE b.Status = 3
  AND ISNULL(i.bInvModel, 0) = 0 AND ISNULL(i.bCheckItem, 0) = 0 AND ISNULL(i.bATOModel, 0) = 0
  AND b.BomType = 1
  AND CONVERT(date, GETDATE()) BETWEEN CONVERT(date, b.VersionEffDate) AND CONVERT(date, b.VersionEndDate)
  AND EXISTS (SELECT 1 FROM dbo.bom_opcomponent AS c WHERE c.BomId = b.BomId)
  AND LTRIM(RTRIM(bp.InvCode)) {_SAFE}
ORDER BY b.BomId DESC;
"""


# 挑单登记（picks.py 读取）。整段文本、以及按 | 拆成多个键的组合（标志 True 表示必须是正整数 id）。
CUSTOM_TEXTS = (
    ("currency_name", currency_sql),
    ("bom_parent", bom_parent_sql),
)
CUSTOM_PACKS = (
    ("sr_src", ("sr_src", "sr_line"), (True, True), sale_return_src_sql),
    ("pr_src", ("pr_src", "pr_line"), (True, True), purchase_return_src_sql),
    (
        "qmrej",
        ("qmrej", "qmrej_line", "qmrej_wh", "qmrej_qty", "qmrej_part"),
        (True, True, False, False, False),
        qm_reject_src_sql,
    ),
    ("pinsrc", ("pinsrc_wh", "pinsrc_ven", "pinsrc_dep", "pinsrc_inv"), (False, False, False, False), purchase_in_nosrc_sql),
)


def custom_keys() -> tuple[str, ...]:
    keys = [key for key, _sql in CUSTOM_TEXTS]
    for _lead, names, _flags, _sql in CUSTOM_PACKS:
        keys.extend(names)
    return tuple(keys)


def custom_samples() -> list[tuple[str, str]]:
    rows = [("custom-" + key.replace("_", "-"), sql()) for key, sql in CUSTOM_TEXTS]
    rows.extend(("custom-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in CUSTOM_PACKS)
    rows.extend(custom_read_samples())
    return rows
