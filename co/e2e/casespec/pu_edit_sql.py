"""采购类修改（到货单、采购退货单、采购发票）的挑单和回读。

生单 2 个再改成 1 个，所以来源剩余数量都要 >= 2；其余条件与第四、五期同类生单用例的挑单相同。
单据 id 和 bag 里的明细 id 只接受正整数，再拼进 SQL。数量一律 ISNULL(…, 0)，红字取绝对值，
U8 删除后写回 0 而不是 NULL 也算回到原值。
"""

from __future__ import annotations

import re

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"
# 改后的行：数量 × 单价与金额、价税合计对得上（桥重算过金额）为 1，否则 0。红字按带符号的值比。
_AMT_OK = (
    "CASE WHEN ABS(ISNULL(s.iOriSum, 0) - ROUND({q} * ISNULL(s.iOriTaxCost, 0), 2)) <= 0.02"
    " AND ABS(ISNULL(s.iOriMoney, 0) - ROUND({q} * ISNULL(s.iOriCost, 0), 2)) <= 0.02 THEN N'1' ELSE N'0' END"
)
# 到货 / 退货行：实收、合格数量与数量相等记 1、2，实收、合格件数与件数相等记 4、8（带符号比，NULL 算不等）。
# 改前改后位图相同 = 改前与原数量相等的列改后等于新数量。
_FOLLOW = (
    "CONVERT(varchar(4), (CASE WHEN s.fRealQuantity = s.iQuantity THEN 1 ELSE 0 END)"
    " + (CASE WHEN s.fValidQuantity = s.iQuantity THEN 2 ELSE 0 END)"
    " + (CASE WHEN s.fRealNum = s.iNum THEN 4 ELSE 0 END)"
    " + (CASE WHEN s.fValidNum = s.iNum THEN 8 ELSE 0 END))"
)


def _doc(doc_id: str) -> str:
    if _ID.fullmatch(doc_id or "") is None:
        raise ValueError("单据 id 不合法")
    return doc_id


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT TOP (1) N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(col: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ABS(ISNULL({col}, 0))))"


def _blank(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) = N''"


def _filled(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) <> N''"


def arr_src_sql() -> str:
    # 已审核、未关闭的普通采购订单行：有单价、剩余可到货 >= 2、存货无单位组、无自由项。
    # 订单不能已有直接入库（来源采购订单的入库单）：U8 拒绝同一订单既到货又直接入库。
    # 采购订单 ID|行 ID|仓库（该存货最常用的采购入库仓库）。
    frees = " + ".join(f"ISNULL(d.cFree{i}, N'')" for i in range(1, 11))
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.POID) + N'|' + CONVERT(varchar(20), d.ID) + N'|' + wh.code AS v
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
WHERE {_filled("m.cVerifier")}
  AND {_blank("m.cCloser")}
  AND m.cBusType = N'普通采购'
  AND {_blank("d.cbCloser")}
  AND ISNULL(d.iQuantity, 0) - ISNULL(d.iArrQTY, 0) >= 2
  AND (CASE WHEN ISNULL(d.bTaxCost, 0) = 1 THEN d.iTaxPrice ELSE d.iUnitPrice END) IS NOT NULL
  AND ISNULL(i.iGroupType, 0) = 0
  AND LTRIM(RTRIM({frees})) = N''
  AND NOT EXISTS (SELECT 1 FROM dbo.rdrecords01 AS b
                  JOIN dbo.RdRecord01 AS r ON r.ID = b.ID
                  JOIN dbo.PO_Podetails AS x ON x.ID = b.iPOsID
                  WHERE x.POID = m.POID AND r.cSource = N'采购订单')
ORDER BY m.POID DESC, d.ID;
"""


def ret_src_sql() -> str:
    # 已审核、未关闭的蓝字普通采购到货行，挂未关闭的采购订单行：合格减已退、到货减已退、订单行净到货都 >= 2。
    # 有单价、无自由项、存货无单位组。到货单 ID|行 Autoid。
    frees = " + ".join(f"ISNULL(s.cFree{i}, N'')" for i in range(1, 11))
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), a.ID) + N'|' + CONVERT(varchar(20), s.Autoid) AS v
FROM dbo.PU_ArrivalVouch AS a
JOIN dbo.PU_ArrivalVouchs AS s ON s.ID = a.ID
JOIN dbo.PO_Podetails AS d ON d.ID = s.iPOsID
JOIN dbo.PO_Pomain AS m ON m.POID = d.POID
JOIN dbo.Inventory AS i ON i.cInvCode = s.cInvCode
WHERE ISNULL(a.iBillType, 0) = 0
  AND a.cBusType = N'普通采购'
  AND {_filled("a.cverifier")}
  AND {_blank("a.ccloser")}
  AND {_blank("s.cbcloser")}
  AND {_blank("d.cbCloser")}
  AND {_blank("m.cCloser")}
  AND ISNULL(s.fValidInQuan, 0) - ISNULL(s.fRetQuantity, 0) >= 2
  AND ISNULL(s.iQuantity, 0) - ISNULL(s.fRetQuantity, 0) >= 2
  AND ISNULL(d.iArrQTY, 0) - ISNULL(d.fPoRetQuantity, 0) >= 2
  AND (CASE WHEN ISNULL(s.bTaxCost, 0) = 1 THEN s.iOriTaxCost ELSE s.iOriCost END) IS NOT NULL
  AND LTRIM(RTRIM({frees})) = N''
  AND ISNULL(i.iGroupType, 0) = 0
ORDER BY a.ID DESC, s.Autoid;
"""


def inv_src_sql() -> str:
    # 已审核、来源采购订单的普通采购入库单行：未结算、有单价、剩余可开票数量 >= 2。入库单 ID|行 AutoID。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), r.ID) + N'|' + CONVERT(varchar(20), s.AutoID) AS v
FROM dbo.RdRecord01 AS r
JOIN dbo.rdrecords01 AS s ON s.ID = r.ID
JOIN dbo.PO_Podetails AS d ON d.ID = s.iPOsID
JOIN dbo.PO_Pomain AS p ON p.POID = d.POID
WHERE LTRIM(RTRIM(ISNULL(r.cHandler, N''))) <> N''
  AND r.cSource = N'采购订单'
  AND r.cBusType = N'普通采购'
  AND ISNULL(s.iQuantity, 0) - ISNULL(s.iSumBillQuantity, 0) >= 2
  AND (CASE WHEN ISNULL(s.bTaxCost, 0) = 1 THEN s.iOriTaxCost ELSE s.iOriCost END) IS NOT NULL
  AND LTRIM(RTRIM(ISNULL(p.cPOID, N''))) <> N''
  AND NOT EXISTS (SELECT 1 FROM dbo.PurSettleVouchs AS t WHERE t.iRdsID = s.AutoID)
ORDER BY r.ID DESC, s.AutoID;
"""


# ---- 到货单（来源 doc 是采购订单 POID，新单 doc 是到货单 ID；订单行是 bag 的 arr_mod_line）----


def _a_line(bag: dict[str, str]) -> str:
    return _doc(bag.get("arr_mod_line", ""))


def _a_arr(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("d.iArrQTY"), f"FROM dbo.PO_Podetails AS d WHERE d.POID = {_doc(doc)} AND d.ID = {_a_line(bag)}")


def _a_new(doc: str, bag: dict[str, str]) -> str:
    return f"FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {_doc(doc)} AND s.iPOsID = {_a_line(bag)} ORDER BY s.Autoid"


# ---- 采购退货单（来源 doc 是原到货单 ID，新单 doc 是退货单 ID；原到货行是 bag 的 pret_mod_line）----


def _r_from(doc: str, bag: dict[str, str]) -> str:
    return (
        "FROM dbo.PU_ArrivalVouchs AS s\n"
        "LEFT JOIN dbo.PO_Podetails AS d ON d.ID = s.iPOsID\n"
        f"WHERE s.ID = {_doc(doc)} AND s.Autoid = {_doc(bag.get('pret_mod_line', ''))}"
    )


def _r_new(doc: str, bag: dict[str, str]) -> str:
    line = _doc(bag.get("pret_mod_line", ""))
    return f"FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {_doc(doc)} AND s.iCorId = {line} ORDER BY s.Autoid"


# ---- 采购发票（来源 doc 是入库单 ID，新单 doc 是 PBVID；入库行是 bag 的 pinv_mod_line）----


def _i_billed(doc: str, bag: dict[str, str]) -> str:
    frm = f"FROM dbo.rdrecords01 AS s WHERE s.ID = {_doc(doc)} AND s.AutoID = {_doc(bag.get('pinv_mod_line', ''))}"
    return _one(_qty("s.iSumBillQuantity"), frm)


def _i_new(doc: str, bag: dict[str, str]) -> str:
    line = _doc(bag.get("pinv_mod_line", ""))
    return f"FROM dbo.PurBillVouchs AS s WHERE s.PBVID = {_doc(doc)} AND s.RdsId = {line} ORDER BY s.ID"


def _id_of(col: str) -> str:
    return f"CONVERT(varchar(20), {col})"


PU_MOD_READBACK = {
    "arr_mod_arr": _a_arr,
    "arr_mod_nline": lambda doc, bag: _one(_id_of("s.Autoid"), _a_new(doc, bag)),
    "arr_mod_nqty": lambda doc, bag: _one(_qty("s.iQuantity"), _a_new(doc, bag)),
    "arr_mod_namt": lambda doc, bag: _one(_AMT_OK.format(q="ISNULL(s.iQuantity, 0)"), _a_new(doc, bag)),
    "arr_mod_nfol": lambda doc, bag: _one(_FOLLOW, _a_new(doc, bag)),
    "pret_mod_ret": lambda doc, bag: _one(_qty("s.fRetQuantity"), _r_from(doc, bag)),
    "pret_mod_poret": lambda doc, bag: _one(_qty("d.fPoRetQuantity"), _r_from(doc, bag)),
    "pret_mod_parr": lambda doc, bag: _one(_qty("d.iArrQTY"), _r_from(doc, bag)),
    "pret_mod_nline": lambda doc, bag: _one(_id_of("s.Autoid"), _r_new(doc, bag)),
    "pret_mod_nqty": lambda doc, bag: _one(_qty("s.iQuantity"), _r_new(doc, bag)),
    "pret_mod_namt": lambda doc, bag: _one(_AMT_OK.format(q="ISNULL(s.iQuantity, 0)"), _r_new(doc, bag)),
    "pret_mod_nfol": lambda doc, bag: _one(_FOLLOW, _r_new(doc, bag)),
    "pinv_mod_billed": _i_billed,
    "pinv_mod_nline": lambda doc, bag: _one(_id_of("s.ID"), _i_new(doc, bag)),
    "pinv_mod_nqty": lambda doc, bag: _one(_qty("s.iPBVQuantity"), _i_new(doc, bag)),
    "pinv_mod_namt": lambda doc, bag: _one(_AMT_OK.format(q="ISNULL(s.iPBVQuantity, 0)"), _i_new(doc, bag)),
}

# 挑单登记（接进 picks.py 的 _PACKS）：按 | 拆成多个键，标志 True 表示必须是正整数 id。
PU_MOD_PACKS = (
    ("arr_mod_src", ("arr_mod_src", "arr_mod_line", "arr_mod_wh"), (True, True, False), arr_src_sql),
    ("pret_mod_src", ("pret_mod_src", "pret_mod_line"), (True, True), ret_src_sql),
    ("pinv_mod_src", ("pinv_mod_src", "pinv_mod_line"), (True, True), inv_src_sql),
)


def has_pu_mod(name: str) -> bool:
    return name in PU_MOD_READBACK


def pu_mod_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = PU_MOD_READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def pu_mod_samples() -> list[tuple[str, str]]:
    bag = {"arr_mod_line": "100", "pret_mod_line": "100", "pinv_mod_line": "100"}
    rows = [("edit-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in PU_MOD_PACKS]
    rows.extend(("edit-" + name.replace("_", "-"), fn("100", bag)) for name, fn in PU_MOD_READBACK.items())
    return rows
