"""参照合并检验的产品检验单生成产成品入库（qm_merged_in）的挑单与回读 SQL。

只读；编码只接受 [0-9A-Za-z._-]，id 只接受正整数（safe_id），再拼进 SQL。挑单返回一行 ROW:a|b|…，挑不到返回空。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def _blank(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) = N''"


def _no_free(alias: str) -> str:
    return " AND ".join(f"ISNULL({alias}.bFree{i}, 0) = 0" for i in range(1, 11))


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def merged_pick_sql() -> str:
    # 已审核、来源生产订单、未入库完毕的合并检验产品检验单（QM04，BMERGECHECKFLAG=1）里一个来源：来源未入库完毕、
    # 剩余可入库 >= 1，来源订单行已审核（Status=3）、存货同检验单、入库 1 个后仍不满额（不会被 U8 自动关闭，删除才不被拦）；
    # 存货不管批次 / 保质期 / 序列号、无自由项；仓库不管货位、未冻结，优先该存货入过库的。
    # 另挑一个别的检验单的合并来源 AUTOID（没有就用本检验单 ID，同样不是本单的来源）做拒绝步骤。
    # 检验单 ID|来源 AUTOID|订单行 MoDId|订单号|仓库|别的来源 AUTOID。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), q.ID) + N'|' + CONVERT(varchar(20), m.AUTOID)
  + N'|' + CONVERT(varchar(20), d.MoDId) + N'|' + LTRIM(RTRIM(o.MoCode)) + N'|' + LTRIM(RTRIM(w.wh))
  + N'|' + CONVERT(varchar(20), ISNULL(x.AUTOID, q.ID)) AS v
FROM dbo.QMCHECKVOUCHER AS q
JOIN dbo.QMMergeCheckDetail AS m ON m.ID = q.ID
JOIN dbo.mom_orderdetail AS d ON d.MoDId = m.SOURCEAUTOID
JOIN dbo.mom_order AS o ON o.MoId = d.MoId
JOIN dbo.Inventory AS i ON i.cInvCode = q.CINVCODE
CROSS APPLY (
  SELECT TOP (1) h.cWhCode AS wh
  FROM dbo.Warehouse AS h
  WHERE ISNULL(h.bWhPos, 0) = 0 AND ISNULL(h.bFreeze, 0) = 0 AND LTRIM(RTRIM(h.cWhCode)) {_SAFE}
  ORDER BY CASE WHEN EXISTS (
      SELECT 1 FROM dbo.RdRecord10 AS r JOIN dbo.rdrecords10 AS s ON s.ID = r.ID
      WHERE r.cWhCode = h.cWhCode AND s.cInvCode = q.CINVCODE) THEN 0 ELSE 1 END,
    h.cWhCode
) AS w
OUTER APPLY (
  SELECT TOP (1) z.AUTOID FROM dbo.QMMergeCheckDetail AS z WHERE z.ID <> q.ID ORDER BY z.AUTOID DESC
) AS x
WHERE q.CVOUCHTYPE = N'QM04'
  AND ISNULL(q.BMERGECHECKFLAG, 0) = 1
  AND NOT {_blank("q.CVERIFIER")}
  AND q.CSOURCE = N'生产订单'
  AND ISNULL(q.BPROINFLAG, 0) = 0
  AND ISNULL(m.BPROINFLAG, 0) = 0
  AND ISNULL(m.FREGQUANTITY, 0) + ISNULL(m.FCONQUANTIY, 0) - ISNULL(m.FSUMQUANTITY, 0) >= 1
  AND d.Status = 3 AND d.InvCode = q.CINVCODE AND d.Qty - ISNULL(d.QualifiedInQty, 0) > 1
  AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0 AND ISNULL(i.bSerial, 0) = 0 AND {_no_free("i")}
  AND LTRIM(RTRIM(o.MoCode)) {_SAFE}
ORDER BY q.ID DESC, m.AUTOID;
"""


def levels_sql(check: str, autoid: str, modid: str) -> str:
    """来源累计入库 FSUMQUANTITY|检验单累计入库 FsumQuantity|订单行合格入库 QualifiedInQty。"""
    expr = (
        _qty(f"(SELECT MAX(m.FSUMQUANTITY) FROM dbo.QMMergeCheckDetail AS m WHERE m.AUTOID = {safe_id(autoid)})")
        + " + N'|' + "
        + _qty(f"(SELECT MAX(q.FsumQuantity) FROM dbo.QMCHECKVOUCHER AS q WHERE q.ID = {safe_id(check)})")
        + " + N'|' + "
        + _qty(f"(SELECT MAX(d.QualifiedInQty) FROM dbo.mom_orderdetail AS d WHERE d.MoDId = {safe_id(modid)})")
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


def line_sql(doc: str) -> str:
    """产成品入库单：行数|imergecheckautoid|iMPoIds|cmocode|iCheckIdBaks|表头 cMPoCode。"""
    expr = (
        "CONVERT(varchar(10), COUNT(*)) + N'|' + CONVERT(varchar(20), ISNULL(MAX(b.imergecheckautoid), 0))"
        " + N'|' + CONVERT(varchar(20), ISNULL(MAX(b.iMPoIds), 0)) + N'|' + ISNULL(MAX(LTRIM(RTRIM(b.cmocode))), N'')"
        " + N'|' + CONVERT(varchar(20), ISNULL(MAX(b.iCheckIdBaks), 0)) + N'|' + ISNULL(MAX(LTRIM(RTRIM(h.cMPoCode))), N'')"
    )
    return _one(expr, f"FROM dbo.rdrecords10 AS b JOIN dbo.RdRecord10 AS h ON h.ID = b.ID WHERE b.ID = {safe_id(doc)}")


def gone_sql(doc: str) -> str:
    """产成品入库单表头还在几张（删除后应为 0）。"""
    return _one("CONVERT(varchar(10), COUNT(*))", f"FROM dbo.RdRecord10 AS h WHERE h.ID = {safe_id(doc)}")


def qm_merged_samples() -> list[tuple[str, str]]:
    return [
        ("qm-merged-pick", merged_pick_sql()),
        ("qm-merged-levels", levels_sql("100", "200", "300")),
        ("qm-merged-line", line_sql("100")),
        ("qm-merged-gone", gone_sql("100")),
    ]
