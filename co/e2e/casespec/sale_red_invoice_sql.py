"""红字销售发票用例（sale_red_invoice）的挑单与回读 SQL。

RS_PACKS 进挑单，has_rs / rs_sql 进 stash / pred 的回读，rs_samples() 进样例 SQL。
标识符都是常量，id 只接受正整数再拼进 SQL。数量统一 decimal(18,4) 文本（例如 -1.0000）。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.flow_pick_sql import no_gathering_plan


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(col: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({col}, 0)))"


def rs_src_sql() -> str:
    # 已审核、未关闭的蓝字发货行（非期初），挂未关闭、已开票的订单行；已开票可退（同桥 SaleReturnRoom 的已开票规则）
    # 与未退数量都 >= 1，这样能生成 bneedbill=1 的退货单，再参照它开红字发票。发货单 DLID|发货行 iDLsID。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.DLID) + N'|' + CONVERT(varchar(20), x.iDLsID) AS v
FROM dbo.DispatchList AS h
JOIN dbo.DispatchLists AS x ON x.DLID = h.DLID
JOIN dbo.SO_SODetails AS so ON so.iSOsID = x.iSOsID
CROSS APPLY (SELECT ISNULL(x.iQuantity, 0) AS q, ISNULL(x.iSettleQuantity, 0) AS s, ISNULL(x.fretqtywkp, 0) AS w,
  ISNULL(x.fretqtyykp, 0) AS y, ISNULL(x.iRetQuantity, 0) AS r) AS n
WHERE h.cVouchType = N'05'
  AND ISNULL(h.bReturnFlag, 0) = 0
  AND ISNULL(h.bFirst, 0) = 0
  AND LTRIM(RTRIM(ISNULL(h.cVerifier, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(h.cCloser, N''))) = N''
  AND {no_gathering_plan("h")}
  AND LTRIM(RTRIM(ISNULL(x.cSCloser, N''))) = N''
  AND LTRIM(RTRIM(ISNULL(so.cSCloser, N''))) = N''
  AND ISNULL(so.iFHQuantity, 0) >= 1
  AND ISNULL(so.iKPQuantity, 0) >= 1
  AND n.q - n.r >= 1
  AND (CASE WHEN n.s - (n.q - n.w) > 0 THEN n.q - n.w ELSE n.s END) - n.y >= 1
ORDER BY h.DLID DESC, x.iDLsID;
"""


RS_PACKS = (("rs_src", ("rs_src", "rs_line"), (True, True), rs_src_sql),)


# ---- 回读 ----


def _ret_line(doc: str, _bag: dict[str, str]) -> str:
    # 新退货单（本用例只有一行）的行主键。
    return _one("CONVERT(varchar(20), MIN(x.iDLsID))", f"FROM dbo.DispatchLists AS x WHERE x.DLID = {safe_id(doc)}")


def _ret_head(doc: str, _bag: dict[str, str]) -> str:
    # 退货单表头：bReturnFlag|bneedbill。本用例要 1|1（已开票退货）。
    expr = (
        "CONVERT(varchar(5), CONVERT(int, ISNULL(MAX(CONVERT(int, h.bReturnFlag)), 0))) + N'|' + "
        "CONVERT(varchar(5), CONVERT(int, ISNULL(MAX(CONVERT(int, h.bneedbill)), 0)))"
    )
    return _one(expr, f"FROM dbo.DispatchList AS h WHERE h.DLID = {safe_id(doc)}")


def _ret_set(doc: str, _bag: dict[str, str]) -> str:
    # 退货行累计开票数量（红字发票保存后为负）。
    return _one(_qty("SUM(x.iSettleQuantity)"), f"FROM dbo.DispatchLists AS x WHERE x.DLID = {safe_id(doc)}")


def _so_kp(doc: str, _bag: dict[str, str]) -> str:
    # 退货行挂的订单行的累计开票数量（净额：蓝票 − |红票|）。
    frm = f"FROM dbo.DispatchLists AS x JOIN dbo.SO_SODetails AS so ON so.iSOsID = x.iSOsID WHERE x.DLID = {safe_id(doc)}"
    return _one(_qty("MAX(so.iKPQuantity)"), frm)


def _inv_shape(doc: str, _bag: dict[str, str]) -> str:
    # 新发票：bReturnFlag|iDisp|数量合计。红字参照退货单应是 1|1|-1.0000。
    expr = (
        "CONVERT(varchar(5), CONVERT(int, ISNULL(MAX(CONVERT(int, h.bReturnFlag)), 0))) + N'|' + "
        "CONVERT(varchar(5), ISNULL(MAX(h.iDisp), 0)) + N'|' + " + _qty("SUM(b.iQuantity)")
    )
    frm = f"FROM dbo.SaleBillVouch AS h JOIN dbo.SaleBillVouchs AS b ON b.SBVID = h.SBVID WHERE h.SBVID = {safe_id(doc)}"
    return _one(expr, frm)


def _inv_gone(doc: str, _bag: dict[str, str]) -> str:
    ref = safe_id(doc)
    expr = (
        f"CONVERT(varchar(20), (SELECT COUNT(*) FROM dbo.SaleBillVouch AS h WHERE h.SBVID = {ref})"
        f" + (SELECT COUNT(*) FROM dbo.SaleBillVouchs AS b WHERE b.SBVID = {ref}))"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


def _inv_vt(doc: str, _bag: dict[str, str]) -> str:
    # 红字发票的模板号 iVTid 应同该发票类型（cVouchType）最近一张蓝字发票的（实测红票的 iVTid 同蓝字）。
    # 一致或账套没有同类型蓝字发票时 1；红票没有 iVTid 时 NULL；不一致时「红票<>蓝票」。
    expr = (
        "CASE WHEN h.iVTid IS NULL THEN N'NULL' WHEN b.iVTid IS NULL OR b.iVTid = h.iVTid THEN N'1'"
        " ELSE CONVERT(varchar(20), h.iVTid) + N'<>' + CONVERT(varchar(20), b.iVTid) END"
    )
    frm = (
        "FROM dbo.SaleBillVouch AS h OUTER APPLY (SELECT TOP (1) x.iVTid FROM dbo.SaleBillVouch AS x"
        " WHERE x.cVouchType = h.cVouchType AND ISNULL(x.bReturnFlag, 0) = 0 AND x.SBVID <> h.SBVID"
        f" ORDER BY x.SBVID DESC) AS b WHERE h.SBVID = {safe_id(doc)}"
    )
    return _one(expr, frm)


_READBACK = {
    "rs_retl": _ret_line,
    "rs_rethead": _ret_head,
    "rs_set": _ret_set,
    "rs_kp": _so_kp,
    "rs_inv_shape": _inv_shape,
    "rs_inv_gone": _inv_gone,
    "rs_vt": _inv_vt,
}


def has_rs(name: str) -> bool:
    return name in _READBACK


def rs_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def rs_samples() -> list[tuple[str, str]]:
    rows = [("more-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in RS_PACKS]
    rows.extend(("more-" + name.replace("_", "-"), fn("100", {})) for name, fn in _READBACK.items())
    return rows
