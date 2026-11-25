"""红字采购（pu_red_chain）的挑单与回读 SQL。

REDPUR_PACKS 进挑单，has_redpur / redpur_sql 进 stash / pred 的回读，redpur_samples() 进样例 SQL（接线在 wiring 汇总模块）。
标识符都是常量，id 只接受正整数再拼进 SQL；挑出的编码只进请求正文，不拼进 SQL。
数量类回读一律取绝对值（红字为负，store_cell 只收非负数）。
账套里现有的红字入库行都已参与采购结算（不能再开票），所以红字发票只能在本用例自己生成的红字入库上测。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(col: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({col}, 0)))"


def _flag(col: str) -> str:
    return f"CONVERT(varchar(5), CONVERT(int, ISNULL({col}, 0)))"


def _blank(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) = N''"


def _filled(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) <> N''"


def _no_free(alias: str) -> str:
    cols = " + ".join(f"ISNULL({alias}.cFree{i}, N'')" for i in range(1, 11))
    return f"LTRIM(RTRIM({cols})) = N''"


# ---- 挑单 ----


def rp_arr_sql(qty: int = 1) -> str:
    # 已审核、未关闭的蓝字普通采购到货行（同 purchase-return-cycle 的挑单），另要求存货不管批次、不管保质期、无自由项、
    # 无单位组，且某个不管货位、未冻结的仓库里现存量 >= qty（红字入库审核要扣库存）；到货、订单行的可退数量也 >= qty。
    # 到货单 ID|行 Autoid|仓库。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), a.ID) + N'|' + CONVERT(varchar(20), s.Autoid) + N'|' + wh.code AS v
FROM dbo.PU_ArrivalVouch AS a
JOIN dbo.PU_ArrivalVouchs AS s ON s.ID = a.ID
JOIN dbo.PO_Podetails AS d ON d.ID = s.iPOsID
JOIN dbo.PO_Pomain AS m ON m.POID = d.POID
JOIN dbo.Inventory AS i ON i.cInvCode = s.cInvCode
CROSS APPLY (
  SELECT TOP (1) LTRIM(RTRIM(c.cWhCode)) AS code
  FROM dbo.CurrentStock AS c
  JOIN dbo.Warehouse AS w ON w.cWhCode = c.cWhCode
  WHERE c.cInvCode = s.cInvCode AND ISNULL(c.iQuantity, 0) >= {qty}
    AND ISNULL(w.bWhPos, 0) = 0 AND ISNULL(w.bFreeze, 0) = 0
    AND LTRIM(RTRIM(c.cWhCode)) {_SAFE} AND LEN(LTRIM(RTRIM(c.cWhCode))) <= 12
  ORDER BY c.iQuantity DESC, c.cWhCode
) AS wh
WHERE ISNULL(a.iBillType, 0) = 0
  AND a.cBusType = N'普通采购'
  AND {_filled("a.cverifier")}
  AND {_blank("a.ccloser")}
  AND {_blank("s.cbcloser")}
  AND {_blank("d.cbCloser")}
  AND {_blank("m.cCloser")}
  AND ISNULL(s.fValidInQuan, 0) - ISNULL(s.fRetQuantity, 0) >= {qty}
  AND ISNULL(s.iQuantity, 0) - ISNULL(s.fRetQuantity, 0) >= {qty}
  AND ISNULL(d.iArrQTY, 0) - ISNULL(d.fPoRetQuantity, 0) >= {qty}
  AND (CASE WHEN ISNULL(s.bTaxCost, 0) = 1 THEN s.iOriTaxCost ELSE s.iOriCost END) IS NOT NULL
  AND {_no_free("s")}
  AND ISNULL(i.iGroupType, 0) = 0
  AND ISNULL(i.bInvBatch, 0) = 0
  AND ISNULL(i.bInvQuality, 0) = 0
ORDER BY a.ID DESC, s.Autoid;
"""


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
# rpu_*：红字发票修改（数量 2 改 1）要退 2 个，来源行剩余和库存都要 >= 2。
REDPUR_PACKS = (
    ("rp_arr", ("rp_arr", "rp_arrl", "rp_wh"), (True, True, False), rp_arr_sql),
    ("rpu_arr", ("rpu_arr", "rpu_arrl", "rpu_wh"), (True, True, False), lambda: rp_arr_sql(2)),
)


# ---- 回读 ----


def _billed_in(_doc: str, bag: dict[str, str]) -> str:
    # 红字入库行 |累计开票数量|（iSumBillQuantity 为负或 NULL）。行取 bag 里的 rp_inl。
    frm = f"FROM dbo.rdrecords01 AS r WHERE r.AutoID = {safe_id(bag.get('rp_inl', ''))}"
    return _one(_qty("-MAX(r.iSumBillQuantity)"), frm)


def _inv(doc: str, _bag: dict[str, str]) -> str:
    # 新发票：bNegative|合计数量，红字 1 张 1 个应是 1|-1.0000。
    expr = _flag("MAX(CONVERT(int, h.bNegative))") + " + N'|' + " + _qty("SUM(b.iPBVQuantity)")
    frm = f"FROM dbo.PurBillVouch AS h JOIN dbo.PurBillVouchs AS b ON b.PBVID = h.PBVID WHERE h.PBVID = {safe_id(doc)}"
    return _one(expr, frm)


def _rev(doc: str, _bag: dict[str, str]) -> str:
    return _one("ISNULL(MAX(h.cVerifier), N'')", f"FROM dbo.PurBillVouch AS h WHERE h.PBVID = {safe_id(doc)}")


def _inv_line(doc: str, _bag: dict[str, str]) -> str:
    # 新发票的行主键（PurBillVouchs.ID），修改请求的 line_id。
    return _one("CONVERT(varchar(20), MIN(b.ID))", f"FROM dbo.PurBillVouchs AS b WHERE b.PBVID = {safe_id(doc)}")


def _inv_memo(doc: str, _bag: dict[str, str]) -> str:
    return _one("ISNULL(MAX(h.cPBVMemo), N'')", f"FROM dbo.PurBillVouch AS h WHERE h.PBVID = {safe_id(doc)}")


def _inv_amt(doc: str, _bag: dict[str, str]) -> str:
    # 改后行：带符号数量 × 单价与金额、价税合计对得上（桥重算过）为 1，否则 0。
    qty = "ISNULL(b.iPBVQuantity, 0)"
    expr = (
        f"CASE WHEN ABS(ISNULL(b.iOriSum, 0) - ROUND({qty} * ISNULL(b.iOriTaxCost, 0), 2)) <= 0.02"
        f" AND ABS(ISNULL(b.iOriMoney, 0) - ROUND({qty} * ISNULL(b.iOriCost, 0), 2)) <= 0.02 THEN N'1' ELSE N'0' END"
    )
    return _one(expr, f"FROM dbo.PurBillVouchs AS b WHERE b.PBVID = {safe_id(doc)}")


def _ret_line(doc: str, _bag: dict[str, str]) -> str:
    return _one("CONVERT(varchar(20), MIN(s.Autoid))", f"FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {safe_id(doc)}")


def _ret_valid(doc: str, _bag: dict[str, str]) -> str:
    # 退货单行 |累计入库数量|（红字入库回写 fValidInQuan 为负）。
    return _one(_qty("-SUM(s.fValidInQuan)"), f"FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {safe_id(doc)}")


def _in_red(doc: str, bag: dict[str, str]) -> str:
    # 新入库单：bredvouch|合计数量|行是否指回退货单行（iArrsId）。1 个应是 1|-1.0000|1。
    ret_line = safe_id(bag.get("rp_retl", ""))
    expr = (
        _flag("MAX(CONVERT(int, h.bredvouch))") + " + N'|' + " + _qty("SUM(r.iQuantity)")
        + f" + N'|' + CONVERT(varchar(5), MIN(CASE WHEN r.iArrsId = {ret_line} THEN 1 ELSE 0 END))"
    )
    frm = f"FROM dbo.RdRecord01 AS h JOIN dbo.rdrecords01 AS r ON r.ID = h.ID WHERE h.ID = {safe_id(doc)}"
    return _one(expr, frm)


def _in_line(doc: str, _bag: dict[str, str]) -> str:
    return _one("CONVERT(varchar(20), MIN(r.AutoID))", f"FROM dbo.rdrecords01 AS r WHERE r.ID = {safe_id(doc)}")


_READBACK = {
    "rp_billed_in": _billed_in,
    "rp_inv": _inv,
    "rp_invl": _inv_line,
    "rp_pbvmemo": _inv_memo,
    "rp_inv_amt": _inv_amt,
    "rp_rev": _rev,
    "rp_retl": _ret_line,
    "rp_valid": _ret_valid,
    "rp_in_red": _in_red,
    "rp_inl": _in_line,
}


def has_redpur(name: str) -> bool:
    return name in _READBACK


def redpur_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def redpur_samples() -> list[tuple[str, str]]:
    rows = [("more-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in REDPUR_PACKS]
    bag = {"rp_retl": "100", "rp_inl": "100"}
    rows.extend(("more-" + name.replace("_", "-"), fn("100", bag)) for name, fn in _READBACK.items())
    return rows
