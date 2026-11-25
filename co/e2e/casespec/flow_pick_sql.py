"""挑单。标识符是常量，返回值带 ROW: 前缀。"""

from __future__ import annotations


def sale_verified_sql() -> str:
    # 已审核、未关闭、未走审批流的蓝字订单，至少两行且没有任何已关闭的行。
    # 只有一行时，关闭这一行 U8 会连表头一起关，测不到「只关一行」。
    # 整单打开会把关闭前就关掉的行也打开，所以这种订单不能入选。取最早的，避开生单用的最新一张。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.ID) AS v
FROM dbo.SO_SOMain AS m
WHERE LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) <> N''
  AND m.dverifydate IS NOT NULL
  AND LTRIM(RTRIM(ISNULL(m.cCloser, N''))) = N''
  AND (m.iswfcontrolled = 0 OR m.iswfcontrolled IS NULL)
  AND (m.bReturnFlag = 0 OR m.bReturnFlag IS NULL)
  AND EXISTS (
    SELECT 1 FROM dbo.SO_SODetails AS d
    WHERE d.ID = m.ID AND LTRIM(RTRIM(ISNULL(d.cSCloser, N''))) = N''
  )
  AND NOT EXISTS (
    SELECT 1 FROM dbo.SO_SODetails AS x
    WHERE x.ID = m.ID AND LTRIM(RTRIM(ISNULL(x.cSCloser, N''))) <> N''
  )
  AND (SELECT COUNT(*) FROM dbo.SO_SODetails AS c WHERE c.ID = m.ID) >= 2
ORDER BY m.ID;
"""


def so_ship_sql() -> str:
    # 已审核未关闭，行剩余可发 >= 1，存货不批次管理，且某个不管货位的仓库有现存量。
    return """SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + CONVERT(varchar(20), m.ID) + N'|'
  + CONVERT(varchar(20), d.iSOsID) + N'|' + LTRIM(RTRIM(cs.cWhCode)) AS v
FROM dbo.SO_SOMain AS m
JOIN dbo.SO_SODetails AS d ON d.ID = m.ID
JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode
JOIN dbo.CurrentStock AS cs ON cs.cInvCode = d.cInvCode
  AND cs.iQuantity >= 1
  AND (cs.cBatch IS NULL OR LTRIM(RTRIM(cs.cBatch)) = N'')
JOIN dbo.Warehouse AS w ON w.cWhCode = cs.cWhCode
WHERE LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) <> N''
  AND m.dverifydate IS NOT NULL
  AND LTRIM(RTRIM(ISNULL(m.cCloser, N''))) = N''
  AND (m.iswfcontrolled = 0 OR m.iswfcontrolled IS NULL)
  AND (m.bReturnFlag = 0 OR m.bReturnFlag IS NULL)
  AND LTRIM(RTRIM(ISNULL(d.cSCloser, N''))) = N''
  AND ISNULL(d.iQuantity, 0) - ISNULL(d.iFHQuantity, 0) >= 1
  AND w.bWhPos = 0
  AND ISNULL(w.bFreeze, 0) = 0
  AND ISNULL(i.bInvBatch, 0) = 0
  AND LTRIM(RTRIM(cs.cWhCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
ORDER BY m.ID DESC, d.iSOsID, cs.cWhCode;
"""


def po_master_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + LTRIM(RTRIM(m.cVenCode)) + N'|' + LTRIM(RTRIM(m.cDepCode)) + N'|'
  + LTRIM(RTRIM(ISNULL(m.cPersonCode, N''))) + N'|'
  + LTRIM(RTRIM(ISNULL(m.cPTCode, N''))) + N'|'
  + LTRIM(RTRIM(ISNULL(m.cBusType, N''))) + N'|'
  + LTRIM(RTRIM(ISNULL(m.cexch_name, N''))) + N'|'
  + ISNULL(CONVERT(varchar(40), CONVERT(decimal(18,6), m.nflat)), N'1') + N'|'
  + ISNULL(CONVERT(varchar(40), CONVERT(decimal(18,6), m.iTaxRate)), N'13') + N'|'
  + LTRIM(RTRIM(d.cInvCode)) + N'|'
  + CASE WHEN ISNULL(d.bTaxCost, 0) = 1
      THEN ISNULL(CONVERT(varchar(40), CONVERT(decimal(18,6), d.iTaxPrice)), N'')
      ELSE ISNULL(CONVERT(varchar(40), CONVERT(decimal(18,6), d.iUnitPrice)), N'')
    END + N'|'
  + CASE WHEN ISNULL(d.bTaxCost, 0) = 1 THEN N'T' ELSE N'U' END AS v
FROM dbo.PO_Pomain AS m
JOIN dbo.PO_Podetails AS d ON d.POID = m.POID
JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode
WHERE LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) <> N''
  AND m.cAuditDate IS NOT NULL
  AND LTRIM(RTRIM(ISNULL(m.cVenCode, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(m.cDepCode, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(m.cPTCode, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(m.cBusType, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(m.cexch_name, N''))) <> N''
  AND ISNULL(m.nflat, 0) > 0
  AND LTRIM(RTRIM(ISNULL(d.cInvCode, N''))) <> N''
  AND (
    (ISNULL(d.bTaxCost, 0) = 1 AND ISNULL(d.iTaxPrice, 0) > 0)
    OR (ISNULL(d.bTaxCost, 0) = 0 AND ISNULL(d.iUnitPrice, 0) > 0)
  )
  AND ISNULL(i.bInvBatch, 0) = 0
  AND ISNULL(i.iGroupType, 0) = 0
  AND d.ID = (SELECT MIN(x.ID) FROM dbo.PO_Podetails AS x WHERE x.POID = m.POID)
ORDER BY m.POID DESC;
"""


def po_lines_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.POID) AS v
FROM dbo.PO_Pomain AS m
WHERE LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) <> N''
  AND m.cAuditDate IS NOT NULL
  AND LTRIM(RTRIM(ISNULL(m.cCloser, N''))) = N''
  AND (
    SELECT COUNT(d.ID) FROM dbo.PO_Podetails AS d
    WHERE d.POID = m.POID AND LTRIM(RTRIM(ISNULL(d.cbCloser, N''))) = N''
  ) >= 2
ORDER BY m.POID;
"""


def po_recv_sql() -> str:
    # 已审核未关闭，行剩余可入库 >= 1，存货不质检、不批次、无计量单位组，仓库不管货位。
    # 订单不能已有到货：U8 拒绝同一订单既到货又直接入库（409「该操作会造成订单到货和入库同时存在」）。
    return """SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + CONVERT(varchar(20), m.POID) + N'|'
  + CONVERT(varchar(20), d.ID) + N'|' + LTRIM(RTRIM(w.cWhCode)) AS v
FROM dbo.PO_Pomain AS m
JOIN dbo.PO_Podetails AS d ON d.POID = m.POID
JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode
JOIN dbo.Warehouse AS w ON w.bWhPos = 0 AND ISNULL(w.bFreeze, 0) = 0
WHERE LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) <> N''
  AND m.cAuditDate IS NOT NULL
  AND LTRIM(RTRIM(ISNULL(m.cCloser, N''))) = N''
  AND LTRIM(RTRIM(ISNULL(d.cbCloser, N''))) = N''
  AND ISNULL(d.iQuantity, 0) - ISNULL(d.iReceivedQTY, 0) >= 1
  AND ISNULL(i.bPropertyCheck, 0) = 0
  AND ISNULL(i.bInvBatch, 0) = 0
  AND ISNULL(i.iGroupType, 0) = 0
  AND NOT EXISTS (SELECT 1 FROM dbo.PU_ArrivalVouchs AS a
                  JOIN dbo.PO_Podetails AS x ON x.ID = a.iPOsID
                  WHERE x.POID = m.POID)
  AND LTRIM(RTRIM(w.cWhCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
ORDER BY m.POID DESC, d.ID, w.cWhCode;
"""


def transfer_master_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + LTRIM(RTRIM(wo.cWhCode)) + N'|' + LTRIM(RTRIM(wi.cWhCode)) + N'|'
  + LTRIM(RTRIM(cs.cInvCode)) + N'|'
  + LTRIM(RTRIM(dep.cODepCode)) + N'|' + LTRIM(RTRIM(dep.cIDepCode)) AS v
FROM dbo.Warehouse AS wo
JOIN dbo.Warehouse AS wi
  ON wi.cWhCode <> wo.cWhCode AND wi.bWhPos = 0 AND ISNULL(wi.bFreeze, 0) = 0
JOIN dbo.CurrentStock AS cs
  ON cs.cWhCode = wo.cWhCode AND cs.iQuantity >= 2
  AND (cs.cBatch IS NULL OR LTRIM(RTRIM(cs.cBatch)) = N'')
JOIN dbo.Inventory AS i
  ON i.cInvCode = cs.cInvCode AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.iGroupType, 0) = 0
CROSS JOIN (
  SELECT TOP (1) t.cODepCode, t.cIDepCode
  FROM dbo.TransVouch AS t
  JOIN dbo.Department AS a ON a.cDepCode = t.cODepCode AND a.bDepEnd = 1
  JOIN dbo.Department AS b ON b.cDepCode = t.cIDepCode AND b.bDepEnd = 1
  WHERE LTRIM(RTRIM(ISNULL(t.cODepCode, N''))) <> N''
    AND LTRIM(RTRIM(ISNULL(t.cIDepCode, N''))) <> N''
    AND LTRIM(RTRIM(t.cODepCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
    AND LTRIM(RTRIM(t.cIDepCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
  GROUP BY t.cODepCode, t.cIDepCode
  ORDER BY COUNT(t.ID) DESC
) AS dep
WHERE wo.bWhPos = 0 AND ISNULL(wo.bFreeze, 0) = 0
  AND LTRIM(RTRIM(wo.cWhCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
  AND LTRIM(RTRIM(wi.cWhCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
  AND LTRIM(RTRIM(cs.cInvCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
ORDER BY wo.cWhCode, wi.cWhCode, cs.cInvCode;
"""


def no_gathering_plan(alias: str) -> str:
    # 到期日按协议从发货日起算，会早于今天的单据日期
    return f"LTRIM(RTRIM(ISNULL({alias}.cGatheringPlan, N''))) = N''"


def invoice_src_body() -> str:
    # 发票生单挑单的 FROM/WHERE 块（别名 d、l、cu）。销售退货挑单用它避开同一张发货单。
    # 已部分开票（发票未复核）的行也可以挑：已开票数量在 iSettleQuantity 里（保存发票时就回写），剩余按它算。
    # 已复核开票数量必须为 0（复核再弃复可能残留），也不能挂已复核的发票（U8 有时没回写 fVeriBillQty），也不能有未开票退货（fretqtywkp 占可开票数量，
    # 生单数量取 iQuantity - iSettleQuantity，有退货时会超）。
    return f"""FROM dbo.DispatchList AS d
JOIN dbo.DispatchLists AS l ON l.DLID = d.DLID
JOIN dbo.Customer AS cu ON cu.cCusCode = d.cCusCode
WHERE d.cVouchType = N'05'
  AND LTRIM(RTRIM(ISNULL(cu.cCusRegCode, N''))) <> N''
  AND d.bFirst = 0
  AND d.bReturnFlag = 0
  AND LTRIM(RTRIM(ISNULL(d.cVerifier, N''))) <> N''
  AND d.dverifydate IS NOT NULL
  AND LTRIM(RTRIM(ISNULL(d.cCloser, N''))) = N''
  AND {no_gathering_plan("d")}
  AND (d.iswfcontrolled = 0 OR d.iswfcontrolled IS NULL)
  AND l.iDLsID > 0
  AND ISNULL(l.fVeriBillQty,0) = 0
  AND ISNULL(l.fretqtywkp, 0) = 0
  AND ISNULL(l.iQuantity, 0) - ISNULL(l.iSettleQuantity, 0) > 0
  AND ROUND(ISNULL(l.iQuantity, 0) - ISNULL(l.iSettleQuantity, 0), 4, 1) > 0
  AND EXISTS (
    SELECT 1 FROM dbo.rdrecords32 AS r
    WHERE r.iDLsID = l.iDLsID
  )
  AND NOT EXISTS (
    SELECT 1 FROM dbo.SaleBillVouchs AS b
    JOIN dbo.SaleBillVouch AS s ON s.SBVID = b.SBVID
    WHERE b.iDLsID = l.iDLsID AND LTRIM(RTRIM(ISNULL(s.cChecker, N''))) <> N''
  )
"""


def invoice_src_sql() -> str:
    # 已审核未关闭的蓝字发货单，客户有税号（U8 不给无税号客户开专用发票）。行上已有销售出库，已复核开票数量、未开票退货为 0，
    # 剩余可开票数量按 4 位截断后仍大于 0（可以已有未复核的部分发票）。
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1)\n"
        "  N'ROW:' + CONVERT(varchar(20), d.DLID) + N'|' + CONVERT(varchar(20), l.iDLsID) AS v\n"
        + invoice_src_body()
        + "ORDER BY d.DLID DESC, l.iDLsID;\n"
    )


def sale_invoice_ar_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), SBVID) AS v
FROM dbo.SaleBillVouch
WHERE LTRIM(RTRIM(ISNULL(cVerifier, N''))) <> N''
ORDER BY SBVID DESC;
"""


def pick_samples() -> tuple[tuple[str, str], ...]:
    return (
        ("flow-sale-verified", sale_verified_sql()),
        ("flow-so-ship", so_ship_sql()),
        ("flow-po-master", po_master_sql()),
        ("flow-po-lines", po_lines_sql()),
        ("flow-po-recv", po_recv_sql()),
        ("flow-transfer", transfer_master_sql()),
        ("flow-sale-invoice", sale_invoice_ar_sql()),
        ("flow-invoice-src", invoice_src_sql()),
    )
