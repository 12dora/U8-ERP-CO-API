"""采购结算单写入用例（pu_settle_write）的挑单与回读 SQL。

只读。挑单返回一行 ROW:a|b|…，挑不到返回空。发票号只收 [0-9A-Za-z._-]（在 SQL 里过滤）；回读只拼整数 PBVID。
"""

from __future__ import annotations

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"
# 回读的列：结算单张数|最大 PSVID|这些结算单的行数|配对一致的行数|发票行数|表头 dSDate|已写 dSDate 的行数|应付审核人。
STATE_COLS = 8


def settle_pick_write_sql() -> str:
    # 结算日期：GL_mend 里第一个采购未结账的期间（之前各月都已结账）的最后一天（按自然月），晚于今天时取今天（桥不收未来日期）。
    # 发票：最新一张未结算、已采购复核、未应付审核、普通采购、蓝字、非期初、非现付、不受审批流控制的 01 / 02 发票，
    # 1 到 5 行，每行参照已审核的采购入库行（UpSoType 为 rd），发票日期和入库日期都不晚于结算日期，没有任何结算行。
    # 发票 PBVID|发票号|行数|结算日期。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.PBVID) + N'|' + LTRIM(RTRIM(h.cPBVCode))
  + N'|' + CONVERT(varchar(10), n.lines) + N'|' + CONVERT(varchar(10), p.sd, 23) AS v
FROM dbo.PurBillVouch AS h
CROSS JOIN (
  SELECT TOP (1) CASE WHEN m.e > CONVERT(date, GETDATE()) THEN CONVERT(datetime, CONVERT(date, GETDATE())) ELSE m.e END AS sd
  FROM dbo.GL_mend AS g
  CROSS APPLY (SELECT DATEADD(day, -1, DATEADD(month, g.iperiod,
    CONVERT(datetime, CONVERT(date, CONVERT(varchar(4), g.iyear) + '-01-01', 23)))) AS e) AS m
  WHERE ISNULL(g.bflag_PU, 0) = 0 AND g.iperiod BETWEEN 1 AND 12
  ORDER BY g.iyear, g.iperiod
) AS p
CROSS APPLY (SELECT COUNT(*) AS lines FROM dbo.PurBillVouchs AS c WHERE c.PBVID = h.PBVID) AS n
WHERE h.cBusType = N'普通采购' AND ISNULL(h.cVerifier, N'') <> N'' AND ISNULL(h.cPBVVerifier, N'') = N''
  AND h.dSDate IS NULL AND ISNULL(h.bFirst, 0) = 0 AND ISNULL(h.bOriginal, 0) = 0 AND ISNULL(h.bNegative, 0) = 0
  AND ISNULL(h.bPayment, 0) = 0 AND ISNULL(h.IsWfControlled, 0) = 0 AND h.cPBVBillType IN (N'01', N'02')
  AND h.dPBVDate <= p.sd AND n.lines BETWEEN 1 AND 5
  AND LEN(h.cPBVCode) <= 30 AND LTRIM(RTRIM(h.cPBVCode)) {_SAFE}
  AND NOT EXISTS (
    SELECT 1 FROM dbo.PurBillVouchs AS b
    LEFT JOIN dbo.rdrecords01 AS r ON r.AutoID = b.RdsId
    LEFT JOIN dbo.RdRecord01 AS rh ON rh.ID = r.ID
    WHERE b.PBVID = h.PBVID AND (r.AutoID IS NULL OR ISNULL(rh.cHandler, N'') = N'' OR rh.dDate > p.sd
      OR UPPER(ISNULL(b.UpSoType, N'rd')) <> N'RD' OR ISNULL(b.iPBVQuantity, 0) <= 0 OR b.dSDate IS NOT NULL
      OR EXISTS (SELECT 1 FROM dbo.PurSettleVouchs AS s WHERE s.iBsID = b.ID))
  )
ORDER BY h.PBVID DESC;
"""


def settle_state_sql(pbvid: int) -> str:
    # 一张发票的结算状态（STATE_COLS 列）。配对一致：结算行的入库行、发票行、数量与发票行（RdsId、ID、iPBVQuantity）相同。
    doc = int(pbvid)
    of_bill = f"SELECT s2.PSVID FROM dbo.PurSettleVouchs AS s2 JOIN dbo.PurBillVouchs AS b2 ON b2.ID = s2.iBsID WHERE b2.PBVID = {doc}"
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(10), (SELECT COUNT(DISTINCT x.PSVID) FROM ({of_bill}) AS x))
  + N'|' + ISNULL(CONVERT(varchar(20), (SELECT MAX(x.PSVID) FROM ({of_bill}) AS x)), N'')
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.PurSettleVouchs AS s WHERE s.PSVID IN ({of_bill})))
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.PurSettleVouchs AS s
      JOIN dbo.PurBillVouchs AS b ON b.ID = s.iBsID AND b.RdsId = s.iRdsID
        AND ABS(ISNULL(s.iSVQuantity, 0) - ISNULL(b.iPBVQuantity, 0)) < 0.000001
      WHERE b.PBVID = {doc}))
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.PurBillVouchs AS c WHERE c.PBVID = {doc}))
  + N'|' + ISNULL(CONVERT(varchar(10), h.dSDate, 23), N'')
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.PurBillVouchs AS c WHERE c.PBVID = {doc} AND c.dSDate IS NOT NULL))
  + N'|' + ISNULL(LTRIM(RTRIM(h.cPBVVerifier)), N'') AS v
FROM dbo.PurBillVouch AS h WHERE h.PBVID = {doc};
"""


def pu_settle_write_samples() -> list[tuple[str, str]]:
    return [("pu-settle-write-pick", settle_pick_write_sql()), ("pu-settle-state", settle_state_sql(1))]
