"""货位调整单用例（position_adjust）的挑单与回读 SQL。

只读；标识符是常量，编码只接受 [0-9A-Za-z._-]（safe_lot），id 只接受正整数（safe_id），再拼进 SQL。
挑单返回一行 ROW:a|b|…，挑不到返回空。结存只看无批号、自由项为空的货位结存（挑的是简单存货）。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.stock_plus_sql import _no_free, safe_lot

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _bin_sum(wh: str, pos: str, inv: str) -> str:
    """该仓库、货位、存货无批号无自由项的货位结存合计。"""
    return (
        f"(SELECT ISNULL(SUM(s.iQuantity), 0) FROM dbo.InvPositionSum AS s WHERE s.cWhCode = {wh}"
        f" AND s.cPosCode = {pos} AND s.cInvCode = {inv} AND ISNULL(s.cBatch, N'') = N'' AND {_free_blank('s')})"
    )


def _free_blank(alias: str) -> str:
    return " AND ".join(f"ISNULL({alias}.cFree{i}, N'') = N''" for i in range(1, 11))


def pick_sql() -> str:
    """货位管理仓库（未冻结）里一个简单存货（非批次、非保质期、非序列号、无自由项）在末级货位上结存 >= 1，作调出货位；
    同仓库另一个末级货位作调入货位（优先已有该存货结存的）。先挑调出的一组再配调入货位，免得货位两两组合。
    仓库|存货|调出货位|调入货位|调出结存|调入结存。"""
    into = _bin_sum("c.cWhCode", "t.cPosCode", "c.cInvCode")
    has = f"EXISTS (SELECT 1 FROM dbo.InvPositionSum AS e WHERE e.cWhCode = c.cWhCode AND e.cPosCode = q.cPosCode" \
        " AND e.cInvCode = c.cInvCode)"
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + LTRIM(RTRIM(c.cWhCode)) + N'|' + LTRIM(RTRIM(c.cInvCode)) + N'|' + LTRIM(RTRIM(c.cPosCode))
  + N'|' + LTRIM(RTRIM(t.cPosCode)) + N'|' + {_qty("c.qty")} + N'|' + {_qty(into)} AS v
FROM (
  SELECT TOP (1) k.cWhCode, k.cPosCode, k.cInvCode, k.qty
  FROM (SELECT s.cWhCode, s.cPosCode, s.cInvCode, SUM(s.iQuantity) AS qty FROM dbo.InvPositionSum AS s
        WHERE ISNULL(s.cBatch, N'') = N'' AND {_free_blank("s")}
        GROUP BY s.cWhCode, s.cPosCode, s.cInvCode HAVING SUM(s.iQuantity) >= 1) AS k
  JOIN dbo.Warehouse AS w ON w.cWhCode = k.cWhCode
  JOIN dbo.Position AS p ON p.cWhCode = k.cWhCode AND p.cPosCode = k.cPosCode AND p.bPosEnd = 1
  JOIN dbo.Inventory AS i ON i.cInvCode = k.cInvCode
  WHERE w.bWhPos = 1 AND ISNULL(w.bFreeze, 0) = 0 AND LTRIM(RTRIM(w.cWhCode)) {_SAFE} AND LEN(w.cWhCode) <= 10
    AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0 AND ISNULL(i.bSerial, 0) = 0
    AND ISNULL(i.iGroupType, 0) = 0 AND ISNULL(i.bService, 0) = 0 AND {_no_free("i")}
    AND LTRIM(RTRIM(i.cInvCode)) {_SAFE} AND LEN(i.cInvCode) <= 60 AND LTRIM(RTRIM(k.cPosCode)) {_SAFE}
  ORDER BY k.cWhCode, k.cInvCode, k.cPosCode
) AS c
CROSS APPLY (
  SELECT TOP (1) q.cPosCode FROM dbo.Position AS q
  WHERE q.cWhCode = c.cWhCode AND q.bPosEnd = 1 AND q.cPosCode <> c.cPosCode AND LTRIM(RTRIM(q.cPosCode)) {_SAFE}
  ORDER BY CASE WHEN {has} THEN 0 ELSE 1 END, q.cPosCode
) AS t;
"""


def bin_sums_sql(wh: str, inv: str, src: str, dst: str) -> str:
    """调出货位结存|调入货位结存。"""
    w, i = f"N'{safe_lot(wh)}'", f"N'{safe_lot(inv)}'"
    a, b = f"N'{safe_lot(src)}'", f"N'{safe_lot(dst)}'"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + _qty(_bin_sum(w, a, i)) + " + N'|' + " + _qty(_bin_sum(w, b, i)) + " AS v;\n"


def ledger_sql(doc: str) -> str:
    """本单货位台账：行数|出库行数|入库行数（cvouchtype=19，RdID = 表头 Id）。"""
    expr = (
        "CONVERT(varchar(10), COUNT(*)) + N'|' + CONVERT(varchar(10), SUM(CASE WHEN p.bRdFlag = 0 THEN 1 ELSE 0 END))"
        " + N'|' + CONVERT(varchar(10), SUM(CASE WHEN p.bRdFlag = 1 THEN 1 ELSE 0 END))"
    )
    return _one(
        "ISNULL(" + expr + ", N'0|0|0')",
        f"FROM dbo.InvPosition AS p WHERE p.RdID = {safe_id(doc)} AND p.cvouchtype = N'19'",
    )


def head_sql(doc: str) -> str:
    """表头：单号|审核人|行数（不存在时没有行）。"""
    rows = "(SELECT COUNT(*) FROM dbo.AdjustPVouchs AS b WHERE b.ID = h.Id)"
    expr = "ISNULL(h.cVouchCode, N'') + N'|' + ISNULL(h.chandler, N'') + N'|' + CONVERT(varchar(10), " + rows + ")"
    return _one(expr, f"FROM dbo.AdjustPVouch AS h WHERE h.Id = {safe_id(doc)}")


def edit_sql(doc: str) -> str:
    """修改回读（首行按 irowno、autoID 排）：表头备注|行数|首行 autoID|首行备注|首行单号|首行行号|首行数量|首行件数|表头单号
    （不存在时没有行）。"""
    rows = "(SELECT COUNT(*) FROM dbo.AdjustPVouchs AS b WHERE b.ID = h.Id)"
    first = (
        "OUTER APPLY (SELECT TOP (1) b.autoID, b.cbMemo, b.cVouchCode, b.irowno, b.iQuantity, b.iNum"
        " FROM dbo.AdjustPVouchs AS b WHERE b.ID = h.Id ORDER BY b.irowno, b.autoID) AS f"
    )
    parts = (
        "ISNULL(h.cMemo, N'')", "CONVERT(varchar(10), " + rows + ")", "ISNULL(CONVERT(varchar(20), f.autoID), N'')",
        "ISNULL(f.cbMemo, N'')", "ISNULL(f.cVouchCode, N'')", "ISNULL(CONVERT(varchar(10), f.irowno), N'')",
        "ISNULL(CONVERT(varchar(40), CONVERT(decimal(18,4), f.iQuantity)), N'')",
        "ISNULL(CONVERT(varchar(40), CONVERT(decimal(18,4), f.iNum)), N'')", "ISNULL(h.cVouchCode, N'')",
    )
    return _one(" + N'|' + ".join(parts), f"FROM dbo.AdjustPVouch AS h {first} WHERE h.Id = {safe_id(doc)}")


def gone_sql(doc: str) -> str:
    return _one("CONVERT(varchar(10), COUNT(*))", f"FROM dbo.AdjustPVouch AS h WHERE h.Id = {safe_id(doc)}")


def position_adjust_samples() -> list[tuple[str, str]]:
    return [
        ("pa-pick", pick_sql()),
        ("pa-bin-sums", bin_sums_sql("05", "0001", "5A010101", "5A010102")),
        ("pa-ledger", ledger_sql("100")),
        ("pa-head", head_sql("100")),
        ("pa-edit", edit_sql("100")),
        ("pa-gone", gone_sql("100")),
    ]
