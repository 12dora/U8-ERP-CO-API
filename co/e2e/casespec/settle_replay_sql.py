"""采购手工结算重做用例（settle_replay）的挑单与快照 SQL。

只读。id 只接受正整数（safe_id），日期只接受 yyyy-mm-dd，形态只接受登记过的四种，再拼进 SQL。
挑单输出一行 ROW:<值>，快照每个对象一行 ROW:<值>（多个值用 | 分隔），调用方排序后比对。
"""

from __future__ import annotations

from collections.abc import Sequence

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.arap_proc_sql import _HOME, _day

# 形态 → 条件（s 是按结算单汇总的行数：np 配对、nr 只有入库行、ni 只有发票行、inv 发票张数）。
SHAPES = {
    "pair": "s.nr = 0 AND s.ni = 0 AND s.np >= 1 AND s.inv = 1",
    "rd": "s.np = 0 AND s.ni = 0 AND s.nr >= 2",
    "inv": "s.np = 0 AND s.nr = 0 AND s.ni >= 2",
    "multi": "s.nr = 0 AND s.ni = 0 AND s.np >= 2 AND s.inv >= 2",
}
LINE_COLS = 6
CANDIDATES = 20
_QTY = "CONVERT(varchar(40), CONVERT(decimal(28, 6), ISNULL({0}, 0)))"
_AMT = "CONVERT(varchar(40), CONVERT(decimal(28, 2), ISNULL({0}, 0)))"


def _ids(values: Sequence[int]) -> str:
    if not values or len(values) > 400:
        raise ValueError("主键清单不合法")
    return ", ".join(safe_id(str(value)) for value in values)


def settle_pick_sql(shape: str, today: str) -> str:
    """每个候选一行（最多 CANDIDATES 行）：<PSVID>|<结算日期>|<行数>|<结算月采购已结账 1/0>|<已应付审核的发票张数>。
    该形态里可以删了重做的手工结算单：
    普通采购、cSettleType 01、每行都没经存货核算处理（bAccount）、发票都是本位币且除审核登记外没有核销等处理（应付审核可由
    准备步骤取消）、结算日期不晚于 today 且与它同一年度、此前各月采购都已结账（U8 只许在这种月份结算；结算月及以后已结账的
    由准备步骤取消结账），行数不超过 400，入库行、发票行都不在别的结算单里（避免顺序影响改价）。
    要准备得少的优先：结算月未结账、发票未审核的在前，再按 PSVID 从新到旧。"""
    cond = SHAPES.get(shape)
    if cond is None:
        raise ValueError("结算形态不合法")
    text, year, _month = _day(today)
    month = "YEAR(h.dSVDate) * 100 + MONTH(h.dSVDate)"
    closed = (
        "CASE WHEN EXISTS (SELECT 1 FROM dbo.GL_mend AS m WHERE m.iyear * 100 + m.iperiod = " + month
        + " AND ISNULL(m.bflag_PU, 0) <> 0) THEN 1 ELSE 0 END"
    )
    audited = (
        "(SELECT COUNT(DISTINCT p.PBVID) FROM dbo.PurSettleVouchs AS l INNER JOIN dbo.PurBillVouchs AS b ON b.ID = l.iBsID"
        " INNER JOIN dbo.PurBillVouch AS p ON p.PBVID = b.PBVID"
        " WHERE l.PSVID = h.PSVID AND LTRIM(RTRIM(ISNULL(p.cPBVVerifier, N''))) <> N'')"
    )
    return f"""SET NOCOUNT ON;
WITH s AS (
  SELECT l.PSVID AS id, COUNT(*) AS n,
    SUM(CASE WHEN ISNULL(l.iRdsID, 0) <> 0 AND ISNULL(l.iBsID, 0) <> 0 THEN 1 ELSE 0 END) AS np,
    SUM(CASE WHEN ISNULL(l.iRdsID, 0) <> 0 AND ISNULL(l.iBsID, 0) = 0 THEN 1 ELSE 0 END) AS nr,
    SUM(CASE WHEN ISNULL(l.iRdsID, 0) = 0 AND ISNULL(l.iBsID, 0) <> 0 THEN 1 ELSE 0 END) AS ni,
    SUM(CASE WHEN ISNULL(l.bAccount, 0) <> 0 THEN 1 ELSE 0 END) AS acc,
    COUNT(DISTINCT b.PBVID) AS inv
  FROM dbo.PurSettleVouchs AS l LEFT JOIN dbo.PurBillVouchs AS b ON b.ID = l.iBsID AND ISNULL(l.iBsID, 0) <> 0
  GROUP BY l.PSVID
)
SELECT TOP ({CANDIDATES}) N'ROW:' + CONVERT(varchar(20), h.PSVID) + N'|' + CONVERT(varchar(10), h.dSVDate, 23)
  + N'|' + CONVERT(varchar(10), s.n) + N'|' + CONVERT(varchar(1), {closed}) + N'|' + CONVERT(varchar(10), {audited}) AS v
FROM dbo.PurSettleVouch AS h INNER JOIN s ON s.id = h.PSVID
WHERE h.cSettleType = N'01' AND h.cBusType = N'普通采购' AND s.acc = 0 AND s.n <= 400 AND {cond}
  AND h.dSVDate <= '{text}' AND YEAR(h.dSVDate) = {year}
  AND NOT EXISTS (SELECT 1 FROM dbo.GL_mend AS g WHERE ISNULL(g.bflag_PU, 0) = 0 AND g.iperiod BETWEEN 1 AND 12
    AND g.iyear * 100 + g.iperiod < {month})
  AND NOT EXISTS (SELECT 1 FROM dbo.PurSettleVouchs AS l INNER JOIN dbo.PurBillVouchs AS b ON b.ID = l.iBsID
    INNER JOIN dbo.PurBillVouch AS p ON p.PBVID = b.PBVID
    WHERE l.PSVID = h.PSVID AND (ISNULL(p.cexch_name, {_HOME}) <> {_HOME}
      OR EXISTS (SELECT 1 FROM dbo.Ap_Detail AS d WHERE d.cFlag = N'AP' AND d.cCoVouchType = p.cPBVBillType
        AND d.cCoVouchID = p.cPBVCode AND ISNULL(d.cProcStyle, N'') <> ISNULL(d.cVouchType, N''))))
  AND NOT EXISTS (SELECT 1 FROM dbo.PurSettleVouchs AS l INNER JOIN dbo.PurSettleVouchs AS o ON o.PSVID <> l.PSVID
    AND ((ISNULL(l.iRdsID, 0) <> 0 AND o.iRdsID = l.iRdsID) OR (ISNULL(l.iBsID, 0) <> 0 AND o.iBsID = l.iBsID))
    WHERE l.PSVID = h.PSVID)
ORDER BY {closed}, {audited}, h.PSVID DESC;
"""


def settle_head_sql(psvid: int) -> str:
    """快照：一行 <结算类型>|<业务类型>|<采购类型>|<供应商>|<结算日期>|<行数>。单号、制单人不比。"""
    doc = safe_id(str(psvid))
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + ISNULL(h.cSettleType, N'') + N'|' + ISNULL(h.cBusType, N'') + N'|' + ISNULL(h.cPTCode, N'')
  + N'|' + LTRIM(RTRIM(ISNULL(h.cVenCode, N''))) + N'|' + CONVERT(varchar(10), h.dSVDate, 23)
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.PurSettleVouchs AS l WHERE l.PSVID = h.PSVID)) AS v
FROM dbo.PurSettleVouch AS h WHERE h.PSVID = {doc};
"""


def settle_lines_sql(psvid: int) -> str:
    """快照：每行 <入库行>|<发票行>|<数量>|<结算金额 iSVPrice>|<税额 iTax>|<cUpSoType>。也是重做请求的来源。"""
    doc = safe_id(str(psvid))
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(20), ISNULL(l.iRdsID, 0)) + N'|' + CONVERT(varchar(20), ISNULL(l.iBsID, 0))
  + N'|' + {_QTY.format("l.iSVQuantity")} + N'|' + {_AMT.format("l.iSVPrice")} + N'|' + {_AMT.format("l.iTax")}
  + N'|' + ISNULL(l.cUpSoType, N'') AS v
FROM dbo.PurSettleVouchs AS l WHERE l.PSVID = {doc};
"""


def settle_rd_sql(lines: Sequence[int]) -> str:
    """快照：每个入库行 <AutoID>|<已结算数量 iSQuantity>|<金额 iPrice>|<单价 iUnitCost>。"""
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(20), b.AutoID) + N'|' + {_QTY.format("b.iSQuantity")} + N'|' + {_AMT.format("b.iPrice")}
  + N'|' + {_QTY.format("b.iUnitCost")} AS v
FROM dbo.rdrecords01 AS b WHERE b.AutoID IN ({_ids(lines)});
"""


def settle_bill_sql(lines: Sequence[int]) -> str:
    """快照：每个发票行 <ID>|<行结算日期 dSDate>|<入库日期 dInDate>|<表头结算日期>。"""
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(20), b.ID) + N'|' + ISNULL(CONVERT(varchar(10), b.dSDate, 23), N'')
  + N'|' + ISNULL(CONVERT(varchar(10), b.dInDate, 23), N'') + N'|' + ISNULL(CONVERT(varchar(10), h.dSDate, 23), N'') AS v
FROM dbo.PurBillVouchs AS b INNER JOIN dbo.PurBillVouch AS h ON h.PBVID = b.PBVID WHERE b.ID IN ({_ids(lines)});
"""


def settle_replay_samples() -> list[tuple[str, str]]:
    rows = [("settle-replay-pick-" + shape, settle_pick_sql(shape, "2026-10-03")) for shape in SHAPES]
    return rows + [
        ("settle-replay-head", settle_head_sql(461)),
        ("settle-replay-lines", settle_lines_sql(461)),
        ("settle-replay-rd", settle_rd_sql([101, 102])),
        ("settle-replay-bill", settle_bill_sql([201, 202])),
    ]
