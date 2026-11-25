"""回读：退货回写、采购发票复核人、不良品处理单入库链接。

单据 id 和 bag 里的明细 id 只接受正整数，再拼进 SQL。数量一律 ISNULL(…, 0)，U8 写回 0 而不是 NULL 也算回到原值。
"""

from __future__ import annotations

import re

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")

# 销售退货可退数量：未开票可退（同桥 SaleReturnRoom）与已出库未退的较小者。别名 x 是发货行。
SR_ROOM = (
    "CASE WHEN ISNULL(x.iQuantity, 0) - ISNULL(x.iSettleQuantity, 0) - ISNULL(x.fretqtywkp, 0)"
    " < ISNULL(x.fOutQuantity, 0) - ISNULL(x.iRetQuantity, 0)"
    " THEN ISNULL(x.iQuantity, 0) - ISNULL(x.iSettleQuantity, 0) - ISNULL(x.fretqtywkp, 0)"
    " ELSE ISNULL(x.fOutQuantity, 0) - ISNULL(x.iRetQuantity, 0) END"
)


def _doc(doc_id: str) -> str:
    if _ID.fullmatch(doc_id or "") is None:
        raise ValueError("单据 id 不合法")
    return doc_id


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(col: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({col}, 0)))"


def _count(col: str) -> str:
    return f"CONVERT(varchar(12), COUNT({col}))"


# ---- 采购发票复核 ----


def _pinv_rev(doc: str, _bag: dict[str, str]) -> str:
    # 采购复核人 cVerifier（不是应付审核人 cPBVVerifier）。
    return _one("ISNULL(LTRIM(RTRIM(h.cVerifier)), N'')", f"FROM dbo.PurBillVouch AS h WHERE h.PBVID = {_doc(doc)}")


# ---- 采购退货（doc 是原到货单 ID，行是 bag 的 pr_line）----


def _pr_from(doc: str, bag: dict[str, str]) -> str:
    return (
        "FROM dbo.PU_ArrivalVouchs AS s\n"
        "LEFT JOIN dbo.PO_Podetails AS d ON d.ID = s.iPOsID\n"
        f"WHERE s.ID = {_doc(doc)} AND s.Autoid = {_doc(bag.get('pr_line', ''))}"
    )


def _pr_ret(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("s.fRetQuantity"), _pr_from(doc, bag))


def _pr_poret(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("d.fPoRetQuantity"), _pr_from(doc, bag))


def _pr_parr(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("d.iArrQTY"), _pr_from(doc, bag))


# ---- 销售退货（doc 是原发货单 DLID，行是 bag 的 sr_line）----


def _sr_from(doc: str, bag: dict[str, str]) -> str:
    return (
        "FROM dbo.DispatchLists AS d\n"
        "LEFT JOIN dbo.SO_SODetails AS s ON s.iSOsID = d.iSOsID\n"
        f"WHERE d.DLID = {_doc(doc)} AND d.iDLsID = {_doc(bag.get('sr_line', ''))}"
    )


def _sr_ret(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("d.iRetQuantity"), _sr_from(doc, bag))


def _sr_wkp(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("d.fretqtywkp"), _sr_from(doc, bag))


def _sr_soret(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("s.fretquantity"), _sr_from(doc, bag))


def _sr_sofh(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("s.iFHQuantity"), _sr_from(doc, bag))


def _sr_cor(doc: str, bag: dict[str, str]) -> str:
    # doc 是新退货单 DLID：挂回原发货行（iCorID = 原 iDLsID）的行数。
    line = _doc(bag.get("sr_line", ""))
    return _one(_count("d.iDLsID"), f"FROM dbo.DispatchLists AS d WHERE d.DLID = {_doc(doc)} AND d.iCorID = {line}")


def _sr_room(doc: str, bag: dict[str, str]) -> str:
    # 生单前再核一次可退数量（挑单之后别的用例可能已开票或退货）。
    frm = f"FROM dbo.DispatchLists AS x WHERE x.DLID = {_doc(doc)} AND x.iDLsID = {_doc(bag.get('sr_line', ''))}"
    return _one(f"CONVERT(varchar(40), CONVERT(decimal(18,4), {SR_ROOM}))", frm)


def _sr_neg(doc: str, _bag: dict[str, str]) -> str:
    # 新退货单是红字、表体数量合计为 −2（退 2 个）：是 1，否 0。
    safe = _doc(doc)
    total = f"(SELECT SUM(ISNULL(d.iQuantity, 0)) FROM dbo.DispatchLists AS d WHERE d.DLID = {safe})"
    expr = f"CASE WHEN ISNULL(h.bReturnFlag, 0) = 1 AND {total} = -2 THEN N'1' ELSE N'0' END"
    return _one(expr, f"FROM dbo.DispatchList AS h WHERE h.DLID = {safe}")


# ---- 产成品入库 ← 不良品处理单（doc 是处理单 ID 或新入库单 ID，行是 bag 的 qmrej_line）----


def _qmrej_link(doc: str, bag: dict[str, str]) -> str:
    line = _doc(bag.get("qmrej_line", ""))
    return _one(_count("s.AutoID"), f"FROM dbo.rdrecords10 AS s WHERE s.ID = {_doc(doc)} AND s.iRejectIds = {line}")


def _qmrej_used(_doc_id: str, bag: dict[str, str]) -> str:
    # 挂在处理单行上的全部入库行数（不论哪张入库单）；删除成功后应为 0。
    line = _doc(bag.get("qmrej_line", ""))
    return _one(_count("s.AutoID"), f"FROM dbo.rdrecords10 AS s WHERE s.iRejectIds = {line}")


def _qmrej_flag(doc: str, bag: dict[str, str]) -> str:
    line = _doc(bag.get("qmrej_line", ""))
    frm = f"FROM dbo.QMREJECTVOUCHERS AS s WHERE s.ID = {_doc(doc)} AND s.AUTOID = {line}"
    return _one("CONVERT(varchar(5), ISNULL(s.BFLAG, 0))", frm)


CUSTOM_READBACK = {
    "pinv_rev": _pinv_rev,
    "pr_ret": _pr_ret,
    "pr_poret": _pr_poret,
    "pr_parr": _pr_parr,
    "sr_ret": _sr_ret,
    "sr_wkp": _sr_wkp,
    "sr_soret": _sr_soret,
    "sr_sofh": _sr_sofh,
    "sr_cor": _sr_cor,
    "sr_neg": _sr_neg,
    "sr_room": _sr_room,
    "qmrej_link": _qmrej_link,
    "qmrej_flag": _qmrej_flag,
    "qmrej_used": _qmrej_used,
}


def custom_read_samples() -> list[tuple[str, str]]:
    bag = {"pr_line": "100", "sr_line": "100", "qmrej_line": "100"}
    return [("custom-" + name.replace("_", "-"), fn("100", bag)) for name, fn in CUSTOM_READBACK.items()]


def has_custom(name: str) -> bool:
    return name in CUSTOM_READBACK


def custom_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = CUSTOM_READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)
