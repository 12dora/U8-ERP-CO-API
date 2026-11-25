"""销售订单和发货单的挑单 SQL。谓词与 pick-docs.ps1 一致，并带 ROW: 前缀。"""

from __future__ import annotations


def sale_open_sql() -> str:
    # 未审核、未关闭（表头 cCloser、各行 cSCloser 都为空）的销售订单：已关闭的订单 U8 拒绝审核。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.ID) AS v
FROM dbo.SO_SOMain AS m
WHERE (m.cVerifier IS NULL OR LTRIM(RTRIM(m.cVerifier)) = N'')
  AND m.dverifydate IS NULL
  AND (m.iswfcontrolled = 0 OR m.iswfcontrolled IS NULL)
  AND (m.bReturnFlag = 0 OR m.bReturnFlag IS NULL)
  AND LTRIM(RTRIM(ISNULL(m.cCloser, N''))) = N''
  AND NOT EXISTS (
    SELECT 1 FROM dbo.SO_SODetails AS d
    WHERE d.ID = m.ID AND LTRIM(RTRIM(ISNULL(d.cSCloser, N''))) <> N''
  )
ORDER BY m.ID;
"""


def dispatch_open_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), DLID) AS v
FROM dbo.DispatchList
WHERE cVouchType = N'05'
  AND bFirst = 0
  AND bReturnFlag = 0
  AND (cVerifier IS NULL OR LTRIM(RTRIM(cVerifier)) = N'')
  AND dverifydate IS NULL
  AND (iswfcontrolled = 0 OR iswfcontrolled IS NULL)
ORDER BY DLID;
"""


def dispatch_verified_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), d.DLID) AS v
FROM dbo.DispatchList AS d
WHERE d.cVouchType = N'05'
  AND d.bFirst = 0
  AND d.bReturnFlag = 0
  AND LTRIM(RTRIM(ISNULL(d.cVerifier, N''))) <> N''
  AND d.dverifydate IS NOT NULL
  AND (d.iswfcontrolled = 0 OR d.iswfcontrolled IS NULL)
  AND NOT EXISTS (
    SELECT 1 FROM dbo.DispatchLists AS l
    WHERE l.DLID = d.DLID
      AND (ISNULL(l.fOutQuantity, 0) <> 0 OR ISNULL(l.iSettleQuantity, 0) <> 0
           OR ISNULL(l.iRetQuantity, 0) <> 0 OR ISNULL(l.fVeriBillQty, 0) <> 0)
  )
ORDER BY d.DLID DESC;
"""


def dispatch_down_sql() -> str:
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), d.DLID) AS v
FROM dbo.DispatchList AS d
WHERE d.cVouchType = N'05' AND d.bFirst = 0 AND d.bReturnFlag = 0
  AND LTRIM(RTRIM(ISNULL(d.cVerifier, N''))) <> N'' AND d.dverifydate IS NOT NULL
  AND (d.iswfcontrolled = 0 OR d.iswfcontrolled IS NULL)
  AND EXISTS (
    SELECT 1 FROM dbo.DispatchLists AS l
    WHERE l.DLID = d.DLID AND ISNULL(l.fOutQuantity, 0) <> 0
  )
ORDER BY d.DLID DESC;
"""


def sale_master_sql() -> str:
    # 首行存货不是 ATO 模型：挑出的存货要拿去新建、发货、出库，ATO 模型没有库存（seed 的 ato 固定数据）。
    return """SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + m.cCusCode + N'|' + m.cSTCode + N'|' + m.cDepCode + N'|'
  + LTRIM(RTRIM(m.cPersonCode)) + N'|' + d.cInvCode + N'|'
  + ISNULL(CONVERT(varchar(40), d.iInvExchRate), N'') + N'|'
  + ISNULL(CONVERT(varchar(40), d.iTaxUnitPrice), N'1') + N'|'
  + ISNULL(CONVERT(varchar(40), d.iTaxRate), N'13') + N'|'
  + ISNULL(CONVERT(varchar(10), d.dPreDate, 23), N'') AS v
FROM dbo.SO_SOMain AS m
JOIN dbo.SO_SODetails AS d ON d.ID = m.ID
WHERE LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) <> N''
  AND m.dverifydate IS NOT NULL
  AND LTRIM(RTRIM(ISNULL(m.cCusCode, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(m.cSTCode, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(m.cDepCode, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(m.cPersonCode, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(d.cInvCode, N''))) <> N''
  AND d.AutoID = (SELECT MIN(x.AutoID) FROM dbo.SO_SODetails AS x WHERE x.ID = m.ID)
  AND NOT EXISTS (SELECT 1 FROM dbo.Inventory AS ai WHERE ai.cInvCode = d.cInvCode AND ISNULL(ai.bATOModel, 0) = 1)
ORDER BY m.ID DESC;
"""
