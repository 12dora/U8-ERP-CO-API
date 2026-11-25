"""采购结算单只读（pu_settle_read）的挑单 SQL。

只读。挑单返回一行 ROW:a|b|…，挑不到返回空。编码只收 [0-9A-Za-z._-]（在 SQL 里过滤），不接收调用方的值。
"""

from __future__ import annotations

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def settle_pick_sql() -> str:
    # 最新一张有发票行的结算单（1 到 500 行）：取它第一条带发票、上游是入库单（cUpSoType 01）的结算行，
    # 入库行和发票行都还在。结算单 PSVID|结算号|供应商|行数|已记账行数|结算行 ID|入库单 ID|发票 PBVID|存货。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.PSVID) + N'|' + LTRIM(RTRIM(h.cSVCode)) + N'|' + LTRIM(RTRIM(h.cVenCode))
  + N'|' + CONVERT(varchar(10), s.n) + N'|' + CONVERT(varchar(10), s.acc) + N'|' + CONVERT(varchar(20), d.ID)
  + N'|' + CONVERT(varchar(20), r.ID) + N'|' + CONVERT(varchar(20), b.PBVID) + N'|' + LTRIM(RTRIM(d.cInvCode)) AS v
FROM dbo.PurSettleVouch AS h
CROSS APPLY (
  SELECT COUNT(*) AS n, SUM(CASE WHEN x.bAccount = 1 THEN 1 ELSE 0 END) AS acc
  FROM dbo.PurSettleVouchs AS x WHERE x.PSVID = h.PSVID
) AS s
CROSS APPLY (
  SELECT TOP (1) y.ID, y.iRdsID, y.iBsID, y.cInvCode
  FROM dbo.PurSettleVouchs AS y
  WHERE y.PSVID = h.PSVID AND y.iBsID <> 0 AND ISNULL(y.cUpSoType, N'01') = N'01'
  ORDER BY y.ID
) AS d
JOIN dbo.rdrecords01 AS r ON r.AutoID = d.iRdsID
JOIN dbo.PurBillVouchs AS b ON b.ID = d.iBsID
WHERE s.n BETWEEN 1 AND 500
  AND LEN(h.cSVCode) <= 30 AND LTRIM(RTRIM(h.cSVCode)) {_SAFE}
  AND LEN(h.cVenCode) <= 20 AND LTRIM(RTRIM(h.cVenCode)) {_SAFE}
  AND LEN(d.cInvCode) <= 60 AND LTRIM(RTRIM(d.cInvCode)) {_SAFE}
ORDER BY h.PSVID DESC;
"""


def pu_settle_samples() -> list[tuple[str, str]]:
    return [("pu-settle-pick", settle_pick_sql())]
