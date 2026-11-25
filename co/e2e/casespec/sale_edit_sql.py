"""销售类修改（发货单、退货单、销售发票）的挑单和回读 SQL。

生单 2 个再改成 1 个，所以来源剩余数量都要 >= 2。挑单沿用对应生单用例的挑单，只收紧剩余数量。
标识符都是常量；单据 id 和 bag 里的明细 id 只接受正整数再拼进 SQL。数量一律 ISNULL(…, 0)，红字取绝对值。
"""

from __future__ import annotations

import re

from co.e2e.casespec.returns_misc_sql import sale_return_src_sql
from co.e2e.casespec.flow_pick_sql import invoice_src_body, no_gathering_plan, so_ship_sql

# 修改时写入的表头备注；回读按这个文本比对（与 sale_edit_bodies.MEMO 相同）。
MEMO = "CO桥修改"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_INT_MAX = 2147483647
# 改后的行：数量 × 含税单价与价税合计、数量 × 无税单价与金额对得上（桥重算过金额）为 1，否则 0。红字按带符号的值比。
_AMT_OK = (
    "CASE WHEN ABS(ISNULL(s.iSum, 0) - ROUND(ISNULL(s.iQuantity, 0) * ISNULL(s.iTaxUnitPrice, 0), 2)) <= 0.02"
    " AND ABS(ISNULL(s.iMoney, 0) - ROUND(ISNULL(s.iQuantity, 0) * ISNULL(s.iUnitPrice, 0), 2)) <= 0.02"
    " THEN N'1' ELSE N'0' END"
)


def _doc(raw: str) -> str:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError("单据 id 不合法")
    return raw


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT TOP (1) N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(col: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ABS(ISNULL({col}, 0))))"


def _id_of(col: str) -> str:
    return f"CONVERT(varchar(20), {col})"


def _tighten(sql: str, pairs: tuple[tuple[str, str], ...]) -> str:
    # 原挑单 SQL 改了写法时直接报错，免得悄悄挑到剩余不够 2 个的单据。
    for old, new in pairs:
        if sql.count(old) != 1:
            raise ValueError("挑单 SQL 已变，需同步销售修改的挑单")
        sql = sql.replace(old, new)
    return sql


# ---- 挑单 ----


def disp_src_sql() -> str:
    # 销售订单行剩余可发 >= 2。订单 ID|行 iSOsID|仓库。
    return _tighten(so_ship_sql(), (("ISNULL(d.iFHQuantity, 0) >= 1", "ISNULL(d.iFHQuantity, 0) >= 2"),))


def ret_src_sql() -> str:
    # 同退货生单的挑单（可退 >= 2、订单行累计发货 >= 2）。发货单 DLID|发货行 iDLsID。
    return sale_return_src_sql()


def inv_src_sql() -> str:
    # 同发票生单的条件，剩余可开票（减未开票退货）>= 2；避开发票生单用例挑的那张发货单（那条用例复核再弃复后
    # 可能留下已复核开票数量）。发货单 DLID|发货行 iDLsID。
    # 这里有意保留「行上没有任何发票」（NOT EXISTS SaleBillVouchs）：改单要干净的行，只有发票生单的挑单放宽到部分开票。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.DLID) + N'|' + CONVERT(varchar(20), x.iDLsID) AS v
FROM dbo.DispatchList AS h
JOIN dbo.DispatchLists AS x ON x.DLID = h.DLID
JOIN dbo.Customer AS c ON c.cCusCode = h.cCusCode
WHERE h.cVouchType = N'05'
  AND LTRIM(RTRIM(ISNULL(c.cCusRegCode, N''))) <> N''
  AND h.bFirst = 0
  AND h.bReturnFlag = 0
  AND LTRIM(RTRIM(ISNULL(h.cVerifier, N''))) <> N''
  AND h.dverifydate IS NOT NULL
  AND LTRIM(RTRIM(ISNULL(h.cCloser, N''))) = N''
  AND {no_gathering_plan("h")}
  AND (h.iswfcontrolled = 0 OR h.iswfcontrolled IS NULL)
  AND LTRIM(RTRIM(ISNULL(x.cSCloser, N''))) = N''
  AND ISNULL(x.fVeriBillQty, 0) = 0
  AND ISNULL(x.iQuantity, 0) - ISNULL(x.iSettleQuantity, 0) - ISNULL(x.fretqtywkp, 0) >= 2
  AND EXISTS (SELECT 1 FROM dbo.rdrecords32 AS r WHERE r.iDLsID = x.iDLsID)
  AND NOT EXISTS (SELECT 1 FROM dbo.SaleBillVouchs AS b WHERE b.iDLsID = x.iDLsID)
  AND h.DLID <> ISNULL((
    SELECT TOP (1) d.DLID
{invoice_src_body()}    ORDER BY d.DLID DESC, l.iDLsID
  ), 0)
ORDER BY h.DLID DESC, x.iDLsID;
"""


# ---- 发货单（来源 doc 是销售订单 ID，新单 doc 是 DLID；订单行是 bag 的 disp_mod_line）----


def _d_line(bag: dict[str, str]) -> str:
    return _doc(bag.get("disp_mod_line", ""))


def _d_fh(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("d.iFHQuantity"), f"FROM dbo.SO_SODetails AS d WHERE d.ID = {_doc(doc)} AND d.iSOsID = {_d_line(bag)}")


def _d_new(doc: str, bag: dict[str, str]) -> str:
    return f"FROM dbo.DispatchLists AS s WHERE s.DLID = {_doc(doc)} AND s.iSOsID = {_d_line(bag)} ORDER BY s.iDLsID"


# ---- 退货单（来源 doc 是原发货单 DLID，新单 doc 是退货单 DLID；原发货行是 bag 的 sret_mod_line）----


def _r_from(doc: str, bag: dict[str, str]) -> str:
    return (
        "FROM dbo.DispatchLists AS d\n"
        "LEFT JOIN dbo.SO_SODetails AS o ON o.iSOsID = d.iSOsID\n"
        f"WHERE d.DLID = {_doc(doc)} AND d.iDLsID = {_doc(bag.get('sret_mod_line', ''))}"
    )


def _r_new(doc: str, bag: dict[str, str]) -> str:
    line = _doc(bag.get("sret_mod_line", ""))
    return f"FROM dbo.DispatchLists AS s WHERE s.DLID = {_doc(doc)} AND s.iCorID = {line} ORDER BY s.iDLsID"


# ---- 销售发票（来源 doc 是发货单 DLID，新单 doc 是 SBVID；发货行是 bag 的 sinv_mod_line）----


def _v_settle(doc: str, bag: dict[str, str]) -> str:
    frm = f"FROM dbo.DispatchLists AS d WHERE d.DLID = {_doc(doc)} AND d.iDLsID = {_doc(bag.get('sinv_mod_line', ''))}"
    return _one(_qty("d.iSettleQuantity"), frm)


def _v_new(doc: str, bag: dict[str, str]) -> str:
    line = _doc(bag.get("sinv_mod_line", ""))
    return f"FROM dbo.SaleBillVouchs AS s WHERE s.SBVID = {_doc(doc)} AND s.iDLsID = {line} ORDER BY s.AutoID"


def _head_ok(table: str, key: str, doc: str) -> str:
    # 表头备注是修改写入的文本、修改人非空：是 1，否 0。
    expr = (
        f"CASE WHEN h.cMemo = N'{MEMO}' AND LTRIM(RTRIM(ISNULL(h.cmodifier, N''))) <> N'' THEN N'1' ELSE N'0' END"
    )
    return _one(expr, f"FROM dbo.{table} AS h WHERE h.{key} = {_doc(doc)}")


def _v_disp(doc: str, _bag: dict[str, str]) -> str:
    # 修改后发票表头 iDisp 为 1（参照发货单开票），sale-invoice-update 断言 eq:1，已实测。
    return _one("CONVERT(varchar(5), ISNULL(h.iDisp, 0))", f"FROM dbo.SaleBillVouch AS h WHERE h.SBVID = {_doc(doc)}")


SA_MOD_READBACK = {
    "disp_mod_fh": _d_fh,
    "disp_mod_nline": lambda doc, bag: _one(_id_of("s.iDLsID"), _d_new(doc, bag)),
    "disp_mod_nqty": lambda doc, bag: _one(_qty("s.iQuantity"), _d_new(doc, bag)),
    "disp_mod_namt": lambda doc, bag: _one(_AMT_OK, _d_new(doc, bag)),
    "disp_mod_head": lambda doc, _bag: _head_ok("DispatchList", "DLID", doc),
    "sret_mod_ret": lambda doc, bag: _one(_qty("d.iRetQuantity"), _r_from(doc, bag)),
    "sret_mod_wkp": lambda doc, bag: _one(_qty("d.fretqtywkp"), _r_from(doc, bag)),
    "sret_mod_soret": lambda doc, bag: _one(_qty("o.fretquantity"), _r_from(doc, bag)),
    "sret_mod_sofh": lambda doc, bag: _one(_qty("o.iFHQuantity"), _r_from(doc, bag)),
    "sret_mod_nline": lambda doc, bag: _one(_id_of("s.iDLsID"), _r_new(doc, bag)),
    "sret_mod_nqty": lambda doc, bag: _one(_qty("s.iQuantity"), _r_new(doc, bag)),
    "sret_mod_namt": lambda doc, bag: _one(_AMT_OK, _r_new(doc, bag)),
    "sret_mod_head": lambda doc, _bag: _head_ok("DispatchList", "DLID", doc),
    "sinv_mod_settle": _v_settle,
    "sinv_mod_nline": lambda doc, bag: _one(_id_of("s.AutoID"), _v_new(doc, bag)),
    "sinv_mod_nqty": lambda doc, bag: _one(_qty("s.iQuantity"), _v_new(doc, bag)),
    "sinv_mod_namt": lambda doc, bag: _one(_AMT_OK, _v_new(doc, bag)),
    "sinv_mod_head": lambda doc, _bag: _head_ok("SaleBillVouch", "SBVID", doc),
    "sinv_mod_disp": _v_disp,
}

# 挑单登记（接进 picks.py 的 _PACKS）：按 | 拆成多个键，标志 True 表示必须是正整数 id。
SA_MOD_PACKS = (
    ("disp_mod_src", ("disp_mod_src", "disp_mod_line", "disp_mod_wh"), (True, True, False), disp_src_sql),
    ("sret_mod_src", ("sret_mod_src", "sret_mod_line"), (True, True), ret_src_sql),
    ("sinv_mod_src", ("sinv_mod_src", "sinv_mod_line"), (True, True), inv_src_sql),
)


def has_sa_mod(name: str) -> bool:
    return name in SA_MOD_READBACK


def sa_mod_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = SA_MOD_READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def sa_mod_samples() -> list[tuple[str, str]]:
    bag = {"disp_mod_line": "100", "sret_mod_line": "100", "sinv_mod_line": "100"}
    rows = [("sa-mod-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in SA_MOD_PACKS]
    rows.extend(("sa-mod-" + name.replace("_", "-"), fn("100", bag)) for name, fn in SA_MOD_READBACK.items())
    return rows
