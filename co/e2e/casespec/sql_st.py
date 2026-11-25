"""库存、采购挑单，以及单据编号规则。"""

from __future__ import annotations


def other_out_open_sql() -> str:
    # 未审核的其他出库（只用于审核与弃审）。不管货位的仓库优先；货位仓库的单据要每行都已指定货位（U8 审核时拒绝没指定货位的行）。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), r.ID) AS v
FROM dbo.RdRecord09 AS r
JOIN dbo.Warehouse AS w ON w.cWhCode = r.cWhCode
WHERE ISNULL(w.bFreeze, 0) = 0
  AND (r.cHandler IS NULL OR LTRIM(RTRIM(r.cHandler)) = N'')
  AND r.dVeriDate IS NULL
  AND (ISNULL(w.bWhPos, 0) = 0 OR NOT EXISTS (SELECT 1 FROM dbo.rdrecords09 AS b
       WHERE b.ID = r.ID AND ISNULL(LTRIM(RTRIM(b.cPosition)), N'') = N''))
ORDER BY ISNULL(w.bWhPos, 0), r.ID DESC;
"""


def other_out_done_sql() -> str:
    # 已审、库存来源、非调拨、表体未记账，仓库不管货位。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), r.ID) AS v
FROM dbo.RdRecord09 AS r
JOIN dbo.Warehouse AS w ON w.cWhCode = r.cWhCode
WHERE w.bWhPos = 0
  AND ISNULL(w.bFreeze, 0) = 0
  AND ISNULL(LTRIM(RTRIM(r.cHandler)), N'') <> N''
  AND ISNULL(r.cSource, N'') = N'库存'
  AND ISNULL(r.cBusType, N'') NOT LIKE N'%调拨%'
  AND NOT EXISTS (SELECT 1 FROM dbo.rdrecords09 AS b
                  WHERE b.ID = r.ID AND ISNULL(LTRIM(RTRIM(b.cbaccounter)), N'') <> N'')
ORDER BY r.ID DESC;
"""


def po_open_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), POID) AS v
FROM dbo.PO_Pomain
WHERE (cVerifier IS NULL OR LTRIM(RTRIM(cVerifier)) = N'')
  AND cAuditDate IS NULL
ORDER BY POID DESC;
"""


def arrival_open_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), ID) AS v
FROM dbo.PU_ArrivalVouch
WHERE (cverifier IS NULL OR LTRIM(RTRIM(cverifier)) = N'')
  AND cAuditDate IS NULL
ORDER BY ID DESC;
"""


def other_in_master_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + h.cWhCode + N'|' + h.cRdCode + N'|' + h.cDepCode + N'|' + b.cInvCode AS v
FROM dbo.RdRecord08 AS h
JOIN dbo.Warehouse AS w ON w.cWhCode = h.cWhCode
JOIN dbo.rdrecords08 AS b ON b.ID = h.ID
JOIN dbo.Inventory AS i ON i.cInvCode = b.cInvCode
JOIN dbo.Rd_Style AS s ON s.cRdCode = h.cRdCode
WHERE w.bWhPos = 0
  AND ISNULL(w.bFreeze, 0) = 0
  AND s.bRdFlag = 1
  AND s.bRdEnd = 1
  AND ISNULL(i.bInvBatch, 0) = 0
  AND LTRIM(RTRIM(ISNULL(h.cDepCode, N''))) <> N''
  AND LTRIM(RTRIM(b.cInvCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
ORDER BY h.ID DESC;
"""


def other_out_master_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + h.cWhCode + N'|' + s.cRdCode + N'|' + h.cDepCode + N'|' + cs.cInvCode AS v
FROM dbo.RdRecord09 AS h
JOIN dbo.Warehouse AS w ON w.cWhCode = h.cWhCode
JOIN dbo.Rd_Style AS s ON s.bRdFlag = 0 AND s.bRdEnd = 1
JOIN dbo.CurrentStock AS cs ON cs.cWhCode = h.cWhCode
  AND cs.iQuantity >= 2
  AND (cs.cBatch IS NULL OR LTRIM(RTRIM(cs.cBatch)) = N'')
JOIN dbo.Inventory AS i ON i.cInvCode = cs.cInvCode AND ISNULL(i.bInvBatch, 0) = 0
WHERE w.bWhPos = 0
  AND ISNULL(w.bFreeze, 0) = 0
  AND LTRIM(RTRIM(ISNULL(h.cDepCode, N''))) <> N''
ORDER BY h.ID DESC;
"""


def voucher_prefix_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:P:' + ISNULL(Prefix1Rule, N'') AS v
FROM dbo.VoucherNumber
WHERE CardNumber = N'0301';
"""
