"""库存补全用例（stock_plus）的挑单与回读 SQL：销售出库生单带批号 / 货位 / 拆行、外币采购入库、无来源红字采购入库。

标识符都是常量；id 只接受正整数，编码只接受 [0-9A-Za-z._-]，再拼进 SQL。挑单返回一行 ROW:a|b|…，挑不到返回空。
"""

from __future__ import annotations

import re

from co.e2e.casespec.stock_misc_sql import safe_id

_LOT = re.compile(r"^[0-9A-Za-z._-]{1,60}\Z")
_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def safe_lot(raw: str) -> str:
    """仓库、存货、货位、批号编码（最长 60）。"""
    if _LOT.fullmatch(raw or "") is None:
        raise ValueError("编码不合法")
    return raw


def _blank(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) = N''"


def _left(alias: str) -> str:
    return f"ISNULL({alias}.iQuantity, 0) - ISNULL({alias}.fOutQuantity, 0)"


def _avail(where: str) -> str:
    # U8 的可用量公式（u8-notes §13）：冻结行为 0，否则 iQuantity − fStopQuantity。
    expr = "CASE WHEN ISNULL(c.bStopFlag, 0) = 1 OR ISNULL(c.bGSPStop, 0) = 1 THEN 0"
    expr += " ELSE ISNULL(c.iQuantity, 0) - ISNULL(c.fStopQuantity, 0) END"
    return f"ISNULL((SELECT SUM({expr}) FROM dbo.CurrentStock AS c WHERE {where}), 0)"


def _no_free(alias: str) -> str:
    return " AND ".join(f"ISNULL({alias}.bFree{i}, 0) = 0" for i in range(1, 11))


def _open_dispatch() -> str:
    # 已审核、未关闭、非期初、不在审批流的蓝字发货单上一行未关闭、剩余 >= 1。
    return f"""h.cVouchType = N'05'
  AND ISNULL(h.bReturnFlag, 0) = 0
  AND ISNULL(h.bFirst, 0) = 0
  AND ISNULL(h.iswfcontrolled, 0) = 0
  AND NOT {_blank("h.cVerifier")}
  AND {_blank("h.cCloser")}
  AND {_blank("x.cSCloser")}
  AND {_left("x")} >= 1"""


def _so_template() -> str:
    # 客户、销售类型、部门、业务员取最近一张已审核的销售订单（同 sale_master），用例自己新建销售订单。
    return f"""CROSS APPLY (
  SELECT TOP (1) m.cCusCode, m.cSTCode, m.cDepCode, m.cPersonCode FROM dbo.SO_SOMain AS m
  WHERE NOT {_blank("m.cVerifier")} AND m.dverifydate IS NOT NULL
    AND NOT {_blank("m.cCusCode")} AND NOT {_blank("m.cSTCode")}
    AND NOT {_blank("m.cDepCode")} AND NOT {_blank("m.cPersonCode")}
    AND LTRIM(RTRIM(m.cCusCode)) {_SAFE} AND LTRIM(RTRIM(m.cSTCode)) {_SAFE}
    AND LTRIM(RTRIM(m.cDepCode)) {_SAFE} AND LTRIM(RTRIM(m.cPersonCode)) {_SAFE}
  ORDER BY m.ID DESC
) AS t"""


def _template_cols() -> str:
    return "LTRIM(RTRIM(t.cCusCode)) + N'|' + LTRIM(RTRIM(t.cSTCode)) + N'|' + LTRIM(RTRIM(t.cDepCode)) + N'|'" \
        " + LTRIM(RTRIM(t.cPersonCode)) + N'|' + LTRIM(RTRIM(i.cInvCode)) + N'|' + LTRIM(RTRIM(w.cWhCode))"


def _sale_inv(batch: int) -> str:
    return f"""ISNULL(i.bSale, 0) = 1 AND ISNULL(i.bInvBatch, 0) = {batch} AND ISNULL(i.bInvQuality, 0) = 0
  AND ISNULL(i.bSerial, 0) = 0 AND ISNULL(i.bService, 0) = 0 AND ISNULL(i.iGroupType, 0) = 0 AND {_no_free("i")}
  AND LTRIM(RTRIM(i.cInvCode)) {_SAFE} AND LEN(i.cInvCode) <= 60 AND LTRIM(RTRIM(w.cWhCode)) {_SAFE}
  AND ISNULL(w.bFreeze, 0) = 0"""


# ---- 挑单 ----


def plain_line_sql() -> str:
    # 拒绝用例：存货不管批次、仓库不管货位的一行。发货单 DLID|发货行 iDLsID。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.DLID) + N'|' + CONVERT(varchar(20), x.iDLsID) AS v
FROM dbo.DispatchList AS h
JOIN dbo.DispatchLists AS x ON x.DLID = h.DLID
JOIN dbo.Inventory AS i ON i.cInvCode = x.cInvCode
JOIN dbo.Warehouse AS w ON w.cWhCode = x.cWhCode
WHERE {_open_dispatch()}
  AND ISNULL(i.bInvBatch, 0) = 0
  AND ISNULL(w.bWhPos, 0) = 0
ORDER BY h.DLID DESC, x.iDLsID;
"""


def bin_split_sql() -> str:
    # 货位用例（用例自己新建销售订单 2 个 → 发货单写货位仓库 → 销售出库）：外购可销售、不管批次 / 保质期 / 序列号、
    # 不是服务、无计量单位组和自由项的存货，在货位仓库里（无批号）可用量 >= 2。末级货位 1 上至少 1；有第二个货位
    # （至少 1）时拆成 1 + 1，没有时货位 1 要至少 2、整行放货位 1。
    # 客户|销售类型|部门|业务员|存货|仓库|货位 1|货位 1 结存|货位 2（可空）。
    pos = """SELECT TOP (1) s.cPosCode, SUM(ISNULL(s.iQuantity, 0)) AS qty FROM dbo.InvPositionSum AS s
    JOIN dbo.Position AS p ON p.cPosCode = s.cPosCode AND p.cWhCode = s.cWhCode AND p.bPosEnd = 1
    WHERE s.cWhCode = w.cWhCode AND s.cInvCode = i.cInvCode AND ISNULL(s.cBatch, N'') = N''
      AND LTRIM(RTRIM(s.cPosCode)) {safe} {extra}
    GROUP BY s.cPosCode HAVING SUM(ISNULL(s.iQuantity, 0)) >= 1 ORDER BY SUM(ISNULL(s.iQuantity, 0)) DESC, s.cPosCode"""
    first = pos.format(safe=_SAFE, extra="")
    second = pos.format(safe=_SAFE, extra="AND s.cPosCode <> p1.cPosCode")
    stock = _avail("c.cWhCode = w.cWhCode AND c.cInvCode = i.cInvCode AND ISNULL(c.cBatch, N'') = N''")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {_template_cols()} + N'|' + LTRIM(RTRIM(p1.cPosCode)) + N'|' + {_qty("p1.qty")} + N'|'
  + ISNULL(LTRIM(RTRIM(p2.cPosCode)), N'') AS v
FROM dbo.Warehouse AS w
JOIN dbo.Inventory AS i ON EXISTS (SELECT 1 FROM dbo.InvPositionSum AS e WHERE e.cWhCode = w.cWhCode AND e.cInvCode = i.cInvCode)
{_so_template()}
CROSS APPLY ({first}) AS p1
OUTER APPLY ({second}) AS p2
WHERE w.bWhPos = 1
  AND {_sale_inv(0)}
  AND (p2.cPosCode IS NOT NULL OR p1.qty >= 2)
  AND {stock} >= 2
ORDER BY CASE WHEN p2.cPosCode IS NULL THEN 1 ELSE 0 END, w.cWhCode, i.cInvCode;
"""


def batch_sql() -> str:
    # 改批号用例（用例自己新建销售订单 1 个 → 发货单写仓库和批号 1 → 销售出库时换成批号 2）：外购可销售的批次存货
    # （不管保质期 / 序列号、无计量单位组和自由项），不管货位的仓库里两个批号可用量各 >= 1。
    # 客户|销售类型|部门|业务员|存货|仓库|批号 1|批号 2。
    lot = """SELECT TOP (1) c.cBatch FROM dbo.CurrentStock AS c
    WHERE c.cWhCode = w.cWhCode AND c.cInvCode = i.cInvCode AND ISNULL(c.cBatch, N'') <> N'' {extra}
      AND ISNULL(c.bStopFlag, 0) = 0 AND ISNULL(c.bGSPStop, 0) = 0
      AND ISNULL(c.iQuantity, 0) - ISNULL(c.fStopQuantity, 0) >= 1
      AND LTRIM(RTRIM(c.cBatch)) {safe} AND LEN(c.cBatch) <= 60
    ORDER BY c.cBatch"""
    first = lot.format(safe=_SAFE, extra="")
    second = lot.format(safe=_SAFE, extra="AND c.cBatch > b1.cBatch")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {_template_cols()} + N'|' + LTRIM(RTRIM(b1.cBatch)) + N'|' + LTRIM(RTRIM(b2.cBatch)) AS v
FROM dbo.Warehouse AS w
JOIN dbo.Inventory AS i ON EXISTS (SELECT 1 FROM dbo.CurrentStock AS e WHERE e.cWhCode = w.cWhCode AND e.cInvCode = i.cInvCode)
{_so_template()}
CROSS APPLY ({first}) AS b1
CROSS APPLY ({second}) AS b2
WHERE ISNULL(w.bWhPos, 0) = 0
  AND {_sale_inv(1)}
ORDER BY w.cWhCode, i.cInvCode;
"""


def _po_open() -> str:
    return f"""NOT {_blank("m.cVerifier")}
  AND m.cAuditDate IS NOT NULL
  AND {_blank("m.cCloser")}
  AND ISNULL(m.iswfcontrolled, 0) = 0
  AND {_blank("d.cbCloser")}
  AND ISNULL(i.bPropertyCheck, 0) = 0 AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0
  AND ISNULL(i.iGroupType, 0) = 0
  AND LTRIM(RTRIM(w.cWhCode)) {_SAFE}"""


def fx_po_sql() -> str:
    # 已有的外币采购订单（汇率不是 1）上一行剩余可入库 >= 1，仓库不管货位。订单 POID|订单行 ID|仓库。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.POID) + N'|' + CONVERT(varchar(20), d.ID) + N'|'
  + LTRIM(RTRIM(w.cWhCode)) AS v
FROM dbo.PO_Pomain AS m
JOIN dbo.PO_Podetails AS d ON d.POID = m.POID
JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode
JOIN dbo.Warehouse AS w ON w.bWhPos = 0 AND ISNULL(w.bFreeze, 0) = 0
WHERE {_po_open()}
  AND ISNULL(m.nflat, 1) <> 1
  AND ISNULL(m.cexch_name, N'') NOT IN (N'', N'人民币')
  AND ISNULL(d.iQuantity, 0) - ISNULL(d.iReceivedQTY, 0) >= 1
ORDER BY m.POID DESC, d.ID, w.cWhCode;
"""


def fx_template_sql() -> str:
    # 没有外币订单时新建一张：取一张已审核普通采购订单的首行作模板（同 po_master，另要存货不需来料检验），
    # 外币取币种档案里第一个非本币，仓库取不管货位的。
    # 供应商|部门|业务员|采购类型|业务类型|税率|存货|单价|T 含税 / U 无税|外币|仓库。
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + LTRIM(RTRIM(m.cVenCode)) + N'|' + LTRIM(RTRIM(m.cDepCode)) + N'|'
  + LTRIM(RTRIM(ISNULL(m.cPersonCode, N''))) + N'|' + LTRIM(RTRIM(m.cPTCode)) + N'|' + LTRIM(RTRIM(m.cBusType)) + N'|'
  + ISNULL(CONVERT(varchar(40), CONVERT(decimal(18,6), m.iTaxRate)), N'13') + N'|'
  + LTRIM(RTRIM(d.cInvCode)) + N'|'
  + CONVERT(varchar(40), CONVERT(decimal(18,6), CASE WHEN ISNULL(d.bTaxCost, 0) = 1 THEN d.iTaxPrice ELSE d.iUnitPrice END))
  + N'|' + CASE WHEN ISNULL(d.bTaxCost, 0) = 1 THEN N'T' ELSE N'U' END
  + N'|' + LTRIM(RTRIM(f.cexch_name)) + N'|' + LTRIM(RTRIM(w.cWhCode)) AS v
FROM dbo.PO_Pomain AS m
JOIN dbo.PO_Podetails AS d ON d.POID = m.POID
JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode
JOIN dbo.Warehouse AS w ON w.bWhPos = 0 AND ISNULL(w.bFreeze, 0) = 0
CROSS APPLY (
  SELECT TOP (1) c.cexch_name FROM dbo.foreigncurrency AS c
  WHERE c.cexch_name <> N'人民币' AND ISNULL(c.cexch_code, N'') <> N'RMB' ORDER BY c.i_id
) AS f
WHERE NOT {_blank("m.cVerifier")}
  AND m.cAuditDate IS NOT NULL
  AND m.cBusType = N'普通采购'
  AND NOT {_blank("m.cVenCode")} AND NOT {_blank("m.cDepCode")} AND NOT {_blank("m.cPTCode")}
  AND ISNULL(CASE WHEN ISNULL(d.bTaxCost, 0) = 1 THEN d.iTaxPrice ELSE d.iUnitPrice END, 0) > 0
  AND ISNULL(i.bPropertyCheck, 0) = 0 AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0
  AND ISNULL(i.iGroupType, 0) = 0
  AND LTRIM(RTRIM(w.cWhCode)) {_SAFE}
  AND d.ID = (SELECT MIN(z.ID) FROM dbo.PO_Podetails AS z WHERE z.POID = m.POID)
ORDER BY m.POID DESC, w.cWhCode;
"""


def havepo_sql() -> str:
    # 采购选项「普通业务必有订单」：1 打开，0 关闭。
    return """SET NOCOUNT ON;
SELECT N'ROW:' + CASE WHEN EXISTS (
    SELECT 1 FROM dbo.AccInformation AS o
    WHERE o.cSysID = N'PU' AND o.cName = N'bPTHavePO' AND UPPER(LTRIM(RTRIM(o.cValue))) IN (N'TRUE', N'1')
  ) THEN N'1' ELSE N'0' END AS v;
"""


def red_pin_sql() -> str:
    # 红字退库：不管货位的仓库里可用量 >= 1 的外购存货（不批次、不保质期、不需来料检验、无计量单位组、无自由项），
    # 本币供应商和部门取最近一张普通采购入库单；采购类型优先默认的（账套可能没设默认，桥此时要求 cptcode）。
    # 仓库|供应商|部门|存货|采购类型。
    stock = _avail("c.cWhCode = w.cWhCode AND c.cInvCode = i.cInvCode AND ISNULL(c.cBatch, N'') = N''")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(w.cWhCode)) + N'|' + LTRIM(RTRIM(r.cVenCode)) + N'|'
  + LTRIM(RTRIM(r.cDepCode)) + N'|' + LTRIM(RTRIM(i.cInvCode)) + N'|' + LTRIM(RTRIM(pt.cPTCode)) AS v
FROM dbo.Warehouse AS w
JOIN dbo.Inventory AS i ON ISNULL(i.bPurchase, 0) = 1
CROSS APPLY (
  SELECT TOP (1) p.cPTCode FROM dbo.PurchaseType AS p
  WHERE NOT {_blank("p.cPTCode")} AND LTRIM(RTRIM(p.cPTCode)) {_SAFE}
  ORDER BY ISNULL(p.bDefault, 0) DESC, p.cPTCode
) AS pt
CROSS APPLY (
  SELECT TOP (1) h.cVenCode, h.cDepCode FROM dbo.RdRecord01 AS h
  JOIN dbo.Vendor AS v ON v.cVenCode = h.cVenCode
  WHERE h.cBusType = N'普通采购' AND ISNULL(v.cVenExch_name, N'人民币') IN (N'', N'人民币')
    AND NOT {_blank("h.cDepCode")}
    AND LTRIM(RTRIM(h.cVenCode)) {_SAFE} AND LTRIM(RTRIM(h.cDepCode)) {_SAFE}
  ORDER BY h.ID DESC
) AS r
WHERE ISNULL(w.bWhPos, 0) = 0 AND ISNULL(w.bFreeze, 0) = 0
  AND ISNULL(i.bPropertyCheck, 0) = 0 AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0
  AND ISNULL(i.bSerial, 0) = 0 AND ISNULL(i.iGroupType, 0) = 0 AND {_no_free("i")}
  AND LTRIM(RTRIM(w.cWhCode)) {_SAFE} AND LTRIM(RTRIM(i.cInvCode)) {_SAFE} AND LEN(i.cInvCode) <= 60
  AND EXISTS (SELECT 1 FROM dbo.CurrentStock AS e WHERE e.cWhCode = w.cWhCode AND e.cInvCode = i.cInvCode)
  AND {stock} >= 1
ORDER BY w.cWhCode, i.cInvCode;
"""


# ---- 回读 ----


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def out_qty_sql(line: str) -> str:
    """发货行累计出库数量。"""
    return _one(_qty("x.fOutQuantity"), f"FROM dbo.DispatchLists AS x WHERE x.iDLsID = {safe_id(line)}")


def so_line_sql(doc: str) -> str:
    """销售订单的第一行 iSOsID（用例自己新建的订单只有一行）。"""
    return _one("CONVERT(varchar(20), MIN(d.iSOsID))", f"FROM dbo.SO_SODetails AS d WHERE d.ID = {safe_id(doc)}")


def disp_line_sql(doc: str) -> str:
    """发货单的第一行 iDLsID（用例自己生成的发货单只有一行）。"""
    return _one("CONVERT(varchar(20), MIN(x.iDLsID))", f"FROM dbo.DispatchLists AS x WHERE x.DLID = {safe_id(doc)}")


def out_rows_sql(doc: str, line: str) -> str:
    """新销售出库单上挂该发货行的每一行：批号|货位|数量|InvPosition 行数（按 AutoID）。"""
    expr = (
        "ISNULL(b.cBatch, N'') + N'|' + ISNULL(b.cPosition, N'') + N'|' + " + _qty("b.iQuantity") + " + N'|'"
        " + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.InvPosition AS p"
        " WHERE p.RdID = b.ID AND p.RdsID = b.AutoID AND p.cvouchtype = N'32'))"
    )
    frm = f"FROM dbo.rdrecords32 AS b WHERE b.ID = {safe_id(doc)} AND b.iDLsID = {safe_id(line)} ORDER BY b.AutoID"
    return _one(expr, frm)


def out_binned_sql(doc: str) -> str:
    """销售出库单的 InvPosition 行数（删除后应为 0）。"""
    return _one("CONVERT(varchar(10), COUNT(*))", f"FROM dbo.InvPosition AS p WHERE p.RdID = {safe_id(doc)} AND p.cvouchtype = N'32'")


def po_line_sql(po: str) -> str:
    """采购订单的第一行 ID。"""
    return _one("CONVERT(varchar(20), MIN(d.ID))", f"FROM dbo.PO_Podetails AS d WHERE d.POID = {safe_id(po)}")


def fx_in_sql(doc: str) -> str:
    """采购入库单：汇率|本币价税合计与 round(原币价税合计 × 汇率, 2) 的最大差|原币价税合计。"""
    expr = (
        "CONVERT(varchar(40), CONVERT(decimal(18,6), MAX(ISNULL(h.iExchRate, 1)))) + N'|'"
        " + CONVERT(varchar(40), CONVERT(decimal(18,2), MAX(ABS(ROUND(ISNULL(b.ioriSum, 0) * ISNULL(h.iExchRate, 1), 2)"
        " - ISNULL(b.iSum, 0))))) + N'|' + CONVERT(varchar(40), CONVERT(decimal(18,2), SUM(ISNULL(b.ioriSum, 0))))"
    )
    return _one(expr, f"FROM dbo.RdRecord01 AS h JOIN dbo.rdrecords01 AS b ON b.ID = h.ID WHERE h.ID = {safe_id(doc)}")


def red_in_sql(doc: str) -> str:
    """红字采购入库单：bredvouch|数量合计|价税合计。"""
    expr = (
        "CONVERT(varchar(5), MAX(CONVERT(int, ISNULL(h.bredvouch, 0)))) + N'|' + " + _qty("SUM(b.iQuantity)") + " + N'|'"
        " + CONVERT(varchar(40), CONVERT(decimal(18,2), SUM(ISNULL(b.iSum, 0))))"
    )
    return _one(expr, f"FROM dbo.RdRecord01 AS h JOIN dbo.rdrecords01 AS b ON b.ID = h.ID WHERE h.ID = {safe_id(doc)}")


def stockplus_samples() -> list[tuple[str, str]]:
    picks = (plain_line_sql, bin_split_sql, batch_sql, fx_po_sql, fx_template_sql, havepo_sql, red_pin_sql)
    rows = [("more-" + fn.__name__.removesuffix("_sql").replace("_", "-"), fn()) for fn in picks]
    rows += [
        ("more-out-qty", out_qty_sql("100")),
        ("more-so-line", so_line_sql("100")),
        ("more-disp-line", disp_line_sql("100")),
        ("more-out-rows", out_rows_sql("100", "101")),
        ("more-out-binned", out_binned_sql("100")),
        ("more-po-line", po_line_sql("100")),
        ("more-fx-in", fx_in_sql("100")),
        ("more-red-in", red_in_sql("100")),
    ]
    return rows
