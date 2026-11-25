"""采购侧：参照采购入库生成采购发票、参照来料检验单生成采购入库的挑单和回读。

单据 id 和 bag 里的明细 id 只接受正整数，再拼进 SQL。数量字段一律 ISNULL(…, 0)，U8 删除后写回 0 而不是 NULL 也算回到原值。
"""

from __future__ import annotations

import re

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def _doc(doc_id: str) -> str:
    if _ID.fullmatch(doc_id or "") is None:
        raise ValueError("单据 id 不合法")
    return doc_id


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(col: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({col}, 0)))"


def pinv_src_sql() -> str:
    # 已审核、来源采购订单的普通采购入库单行：未结算、有单价、剩余可开票数量 >= 1。入库单 ID|行 AutoID。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), r.ID) + N'|' + CONVERT(varchar(20), s.AutoID) AS v
FROM dbo.RdRecord01 AS r
JOIN dbo.rdrecords01 AS s ON s.ID = r.ID
JOIN dbo.PO_Podetails AS d ON d.ID = s.iPOsID
JOIN dbo.PO_Pomain AS p ON p.POID = d.POID
WHERE LTRIM(RTRIM(ISNULL(r.cHandler, N''))) <> N''
  AND r.cSource = N'采购订单'
  AND r.cBusType = N'普通采购'
  AND ISNULL(s.iQuantity, 0) - ISNULL(s.iSumBillQuantity, 0) >= 1
  AND (CASE WHEN ISNULL(s.bTaxCost, 0) = 1 THEN s.iOriTaxCost ELSE s.iOriCost END) IS NOT NULL
  AND LTRIM(RTRIM(ISNULL(p.cPOID, N''))) <> N''
  AND NOT EXISTS (SELECT 1 FROM dbo.PurSettleVouchs AS t WHERE t.iRdsID = s.AutoID)
ORDER BY r.ID DESC, s.AutoID;
"""


def qmin_src_sql() -> str:
    # 已审核、来源到货单的来料检验单：到货行未关闭且对应采购订单行，剩余可入库 >= 1，存货无单位组。
    # 优先不批次管理的存货；批次存货只要检验单带批号且不管保质期（桥沿用检验单批号）。
    # 仓库取该存货最常用的采购入库仓库（未冻结、不管货位：货位仓必须带货位，带货位的见 qm_binned_in）。检验单 ID|仓库。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), q.ID) + N'|' + wh.code AS v
FROM dbo.QMCHECKVOUCHER AS q
JOIN dbo.PU_ArrivalVouchs AS s ON s.Autoid = q.SOURCEAUTOID
JOIN dbo.PO_Podetails AS d ON d.ID = s.iPOsID
JOIN dbo.PO_Pomain AS p ON p.POID = d.POID
JOIN dbo.Inventory AS i ON i.cInvCode = q.CINVCODE
CROSS APPLY (
  SELECT TOP (1) LTRIM(RTRIM(r.cWhCode)) AS code
  FROM dbo.RdRecord01 AS r
  JOIN dbo.rdrecords01 AS b ON b.ID = r.ID
  JOIN dbo.Warehouse AS w ON w.cWhCode = r.cWhCode
  WHERE b.cInvCode = q.CINVCODE
    AND ISNULL(w.bFreeze, 0) = 0
    AND ISNULL(w.bWhPos, 0) = 0
    AND LTRIM(RTRIM(r.cWhCode)) {_SAFE}
  GROUP BY r.cWhCode
  ORDER BY COUNT(*) DESC, r.cWhCode
) AS wh
WHERE q.CVOUCHTYPE = N'QM03'
  AND q.CSOURCE = N'到货单'
  AND LTRIM(RTRIM(ISNULL(q.CVERIFIER, N''))) <> N''
  AND ISNULL(q.BMERGECHECKFLAG, 0) = 0
  AND (
    (ISNULL(i.bInvBatch, 0) = 0 AND LTRIM(RTRIM(ISNULL(q.CBATCH, N''))) = N'')
    OR (i.bInvBatch = 1 AND ISNULL(i.bInvQuality, 0) = 0 AND LTRIM(RTRIM(ISNULL(q.CBATCH, N''))) {_SAFE})
  )
  AND ISNULL(q.FREGQUANTITY, 0) + ISNULL(q.FCONQUANTIY, 0) - ISNULL(q.FsumQuantity, 0) >= 1
  AND LTRIM(RTRIM(ISNULL(s.cbCloser, N''))) = N''
  AND LTRIM(RTRIM(ISNULL(p.cPOID, N''))) <> N''
  AND ISNULL(i.iGroupType, 0) = 0
ORDER BY ISNULL(i.bInvBatch, 0), q.ID DESC;
"""


def arr_src_sql() -> str:
    # 已审核、未关闭的普通采购订单行：有单价、剩余可到货 >= 1、存货无单位组。
    # 订单不能已有直接入库（来源采购订单的入库单）：U8 拒绝同一订单既到货又直接入库。
    # 采购订单 ID|行 ID|仓库（该存货最常用的采购入库仓库）|超出剩余 1 的数量（拒绝用例用）。
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + CONVERT(varchar(20), m.POID) + N'|' + CONVERT(varchar(20), d.ID) + N'|' + wh.code + N'|'
  + CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(d.iQuantity, 0) - ISNULL(d.iArrQTY, 0) + 1)) AS v
FROM dbo.PO_Pomain AS m
JOIN dbo.PO_Podetails AS d ON d.POID = m.POID
JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode
CROSS APPLY (
  SELECT TOP (1) LTRIM(RTRIM(r.cWhCode)) AS code
  FROM dbo.RdRecord01 AS r
  JOIN dbo.rdrecords01 AS b ON b.ID = r.ID
  JOIN dbo.Warehouse AS w ON w.cWhCode = r.cWhCode
  WHERE b.cInvCode = d.cInvCode
    AND ISNULL(w.bFreeze, 0) = 0
    AND LTRIM(RTRIM(r.cWhCode)) {_SAFE}
  GROUP BY r.cWhCode
  ORDER BY COUNT(*) DESC, r.cWhCode
) AS wh
WHERE LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(m.cCloser, N''))) = N''
  AND m.cBusType = N'普通采购'
  AND LTRIM(RTRIM(ISNULL(d.cbCloser, N''))) = N''
  AND ISNULL(d.iQuantity, 0) - ISNULL(d.iArrQTY, 0) >= 1
  AND (CASE WHEN ISNULL(d.bTaxCost, 0) = 1 THEN d.iTaxPrice ELSE d.iUnitPrice END) IS NOT NULL
  AND ISNULL(i.iGroupType, 0) = 0
  AND NOT EXISTS (SELECT 1 FROM dbo.rdrecords01 AS b
                  JOIN dbo.RdRecord01 AS r ON r.ID = b.ID
                  JOIN dbo.PO_Podetails AS x ON x.ID = b.iPOsID
                  WHERE x.POID = m.POID AND r.cSource = N'采购订单')
ORDER BY m.POID DESC, d.ID;
"""


def _arr_line(doc: str, bag: dict[str, str]) -> str:
    return f"FROM dbo.PO_Podetails AS d WHERE d.POID = {_doc(doc)} AND d.ID = {_doc(bag.get('arr_line', ''))}"


def _arr_qty(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("d.iArrQTY"), _arr_line(doc, bag))


def _arr_fpo(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("d.fPoArrQuantity"), _arr_line(doc, bag))


def _pinv_line(doc: str, bag: dict[str, str]) -> str:
    return f"WHERE s.ID = {_doc(doc)} AND s.AutoID = {_doc(bag.get('pinv_line', ''))}"


def _pinv_billed(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("s.iSumBillQuantity"), "FROM dbo.rdrecords01 AS s " + _pinv_line(doc, bag))


def _pinv_po(doc: str, bag: dict[str, str]) -> str:
    frm = "FROM dbo.rdrecords01 AS s JOIN dbo.PO_Podetails AS d ON d.ID = s.iPOsID " + _pinv_line(doc, bag)
    return _one(_qty("d.iInvQTY"), frm)


def _qm_from(doc: str) -> str:
    return (
        "FROM dbo.QMCHECKVOUCHER AS q\n"
        "JOIN dbo.PU_ArrivalVouchs AS s ON s.Autoid = q.SOURCEAUTOID\n"
        "LEFT JOIN dbo.PO_Podetails AS d ON d.ID = s.iPOsID\n"
        f"WHERE q.ID = {_doc(doc)} AND q.CVOUCHTYPE = N'QM03'"
    )


def _qmin_fsum(doc: str, _bag: dict[str, str]) -> str:
    return _one(_qty("q.FsumQuantity"), _qm_from(doc))


def _qmin_valid(doc: str, _bag: dict[str, str]) -> str:
    return _one(_qty("s.fValidInQuan"), _qm_from(doc))


def _qmin_po(doc: str, _bag: dict[str, str]) -> str:
    return _one(_qty("d.freceivedqty"), _qm_from(doc))


def _arr_binsp(doc: str, _bag: dict[str, str]) -> str:
    # 新到货单的 bInspect（已报检）必须是 0，否则报检参照（QM_QREFARR 只看 binspect = 0）看不到这张到货单。
    expr = "CONVERT(varchar(5), CONVERT(int, ISNULL(MAX(s.bInspect), 0)))"
    return _one(expr, f"FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {_doc(doc)}")


PU_READBACK = {
    "pinv_billed": _pinv_billed,
    "pinv_po": _pinv_po,
    "qmin_fsum": _qmin_fsum,
    "qmin_valid": _qmin_valid,
    "qmin_po": _qmin_po,
    "arr_qty": _arr_qty,
    "arr_fpo": _arr_fpo,
    "arr_binsp": _arr_binsp,
}
