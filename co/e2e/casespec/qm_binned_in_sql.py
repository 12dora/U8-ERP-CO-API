"""参照来料检验单生成带货位的采购入库（qm_binned_in）的挑单与回读 SQL。

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


def qm_bin_pick_sql() -> str:
    # 可入库的来料检验单（同 pu_gen_sql.qmin_src_sql：已审核、来源到货单、非合并检验、到货行未关闭且有采购订单行，
    # 剩余可入库 >= 1），存货不管批次 / 保质期 / 序列号、无单位组和自由项、检验单不带批号（货位存量按无批号回读）；
    # 货位仓库（bWhPos=1、未冻结）的末级货位，优先该存货入过库的仓库。检验单 ID|仓库|货位|存货。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), q.ID) + N'|' + LTRIM(RTRIM(b.wh)) + N'|' + LTRIM(RTRIM(b.pos))
  + N'|' + LTRIM(RTRIM(q.CINVCODE)) AS v
FROM dbo.QMCHECKVOUCHER AS q
JOIN dbo.PU_ArrivalVouchs AS s ON s.Autoid = q.SOURCEAUTOID
JOIN dbo.PO_Podetails AS d ON d.ID = s.iPOsID
JOIN dbo.PO_Pomain AS m ON m.POID = d.POID
JOIN dbo.Inventory AS i ON i.cInvCode = q.CINVCODE
CROSS APPLY (
  SELECT TOP (1) w.cWhCode AS wh, p.cPosCode AS pos
  FROM dbo.Warehouse AS w
  JOIN dbo.Position AS p ON p.cWhCode = w.cWhCode AND p.bPosEnd = 1
  WHERE w.bWhPos = 1 AND ISNULL(w.bFreeze, 0) = 0
    AND LTRIM(RTRIM(w.cWhCode)) {_SAFE} AND LTRIM(RTRIM(p.cPosCode)) {_SAFE}
  ORDER BY CASE WHEN EXISTS (
      SELECT 1 FROM dbo.RdRecord01 AS r JOIN dbo.rdrecords01 AS x ON x.ID = r.ID
      WHERE r.cWhCode = w.cWhCode AND x.cInvCode = q.CINVCODE) THEN 0 ELSE 1 END,
    w.cWhCode, p.cPosCode
) AS b
WHERE q.CVOUCHTYPE = N'QM03'
  AND q.CSOURCE = N'到货单'
  AND NOT {_blank("q.CVERIFIER")}
  AND ISNULL(q.BMERGECHECKFLAG, 0) = 0
  AND {_blank("q.CBATCH")}
  AND ISNULL(q.FREGQUANTITY, 0) + ISNULL(q.FCONQUANTIY, 0) - ISNULL(q.FsumQuantity, 0) >= 1
  AND {_blank("s.cbCloser")}
  AND NOT {_blank("m.cPOID")}
  AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0 AND ISNULL(i.bSerial, 0) = 0
  AND ISNULL(i.iGroupType, 0) = 0 AND {_no_free("i")}
  AND LTRIM(RTRIM(q.CINVCODE)) {_SAFE} AND LEN(q.CINVCODE) <= 60
ORDER BY q.ID DESC;
"""


def bin_wh_sql() -> str:
    # 货位仓库（bWhPos=1、未冻结）及其一个末级货位；另给一个不存在的仓库编码（ZZNOWH 加数字，挑第一个没被占用的）。
    # 仓库|货位|不存在的仓库编码。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(w.cWhCode)) + N'|' + LTRIM(RTRIM(p.cPosCode)) + N'|' + n.code AS v
FROM dbo.Warehouse AS w
JOIN dbo.Position AS p ON p.cWhCode = w.cWhCode AND p.bPosEnd = 1
CROSS APPLY (
  SELECT TOP (1) c.code FROM (VALUES (N'ZZNOWH1'), (N'ZZNOWH2'), (N'ZZNOWH3')) AS c(code)
  WHERE NOT EXISTS (SELECT 1 FROM dbo.Warehouse AS x WHERE x.cWhCode = c.code)
  ORDER BY c.code
) AS n
WHERE w.bWhPos = 1 AND ISNULL(w.bFreeze, 0) = 0
  AND LTRIM(RTRIM(w.cWhCode)) {_SAFE} AND LTRIM(RTRIM(p.cPosCode)) {_SAFE}
ORDER BY w.cWhCode, p.cPosCode;
"""


def line_pos_sql(doc: str) -> str:
    """采购入库单表体：行数|货位 cPosition|检验单 iCheckIdBaks。"""
    expr = (
        "CONVERT(varchar(10), COUNT(*)) + N'|' + ISNULL(MAX(LTRIM(RTRIM(b.cPosition))), N'') + N'|'"
        " + CONVERT(varchar(20), ISNULL(MAX(b.iCheckIdBaks), 0))"
    )
    return _one(expr, f"FROM dbo.rdrecords01 AS b WHERE b.ID = {safe_id(doc)}")


def check_sum_sql(check: str) -> str:
    """来料检验单累计入库数 FsumQuantity。"""
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(MAX(q.FsumQuantity), 0)))"
    return _one(expr, f"FROM dbo.QMCHECKVOUCHER AS q WHERE q.ID = {safe_id(check)} AND q.CVOUCHTYPE = N'QM03'")


def qm_bin_samples() -> list[tuple[str, str]]:
    return [
        ("qm-bin-pick", qm_bin_pick_sql()),
        ("bin-wh", bin_wh_sql()),
        ("qm-bin-line", line_pos_sql("100")),
        ("qm-bin-fsum", check_sum_sql("100")),
    ]
