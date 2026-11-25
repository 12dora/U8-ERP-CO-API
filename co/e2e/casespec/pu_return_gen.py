"""零散用例：参照采购订单生成采购退货单（生单、审核、弃审、删除，订单行累计到货和累计退货回退）。

挑单、回读 SQL 与正文都在本模块；接线（catalog、picks、steps._make、steps_sql 的回读）由 wiring.py 统一做：
misc_cases() 进用例登记，MISC_PACKS 进挑单，MISC_BUILDERS 进正文构造器，has_misc / misc_sql 进 stash / pred 的回读，
misc_samples() 进样例 SQL。标识符都是常量，id 只接受正整数再拼进 SQL。
"""

from __future__ import annotations

import re

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_made, pred_step, stash_step

_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_KIND = "purchase_return"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_INT_MAX = 2147483647


def _doc(raw: str) -> str:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError("单据 id 不合法")
    return raw


# ---- 挑单 ----


def _no_free(alias: str) -> str:
    cols = " + ".join(f"ISNULL({alias}.cFree{i}, N'')" for i in range(1, 11))
    return f"LTRIM(RTRIM({cols})) = N''"


def po_return_src_sql() -> str:
    # 已审核、未关闭的普通采购订单行：净到货减已入库 >= 1（退 1 个后累计入库仍不超过累计到货）。
    # 同桥的前提：有单价、无自由项、存货无单位组（浮动换算率）。订单 POID|订单行 ID。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.POID) + N'|' + CONVERT(varchar(20), d.ID) AS v
FROM dbo.PO_Podetails AS d
JOIN dbo.PO_Pomain AS m ON m.POID = d.POID
JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode
WHERE m.cBusType = N'普通采购'
  AND LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(m.cCloser, N''))) = N''
  AND LTRIM(RTRIM(ISNULL(d.cbCloser, N''))) = N''
  AND ISNULL(d.iQuantity, 0) > 0
  AND ISNULL(d.iArrQTY, 0) - ISNULL(d.iReceivedQTY, 0) >= 1
  AND (CASE WHEN ISNULL(d.bTaxCost, 0) = 1 THEN d.iTaxPrice ELSE d.iUnitPrice END) IS NOT NULL
  AND {_no_free("d")}
  AND ISNULL(i.iGroupType, 0) = 0
ORDER BY m.POID DESC, d.ID;
"""


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
MISC_PACKS = (("prpo_src", ("prpo_src", "prpo_line"), (True, True), po_return_src_sql),)


# ---- 回读（doc 是订单 POID 或新退货单 ID，行是 bag 的 prpo_line）----


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(col: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({col}, 0)))"


def _po_from(doc: str, bag: dict[str, str]) -> str:
    return f"FROM dbo.PO_Podetails AS d WHERE d.POID = {_doc(doc)} AND d.ID = {_doc(bag.get('prpo_line', ''))}"


def _prpo_poret(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("d.fPoRetQuantity"), _po_from(doc, bag))


def _prpo_parr(doc: str, bag: dict[str, str]) -> str:
    return _one(_qty("d.iArrQTY"), _po_from(doc, bag))


def _prpo_link(doc: str, bag: dict[str, str]) -> str:
    # doc 是新退货单 ID：红字、一行、挂回订单行（iPOsID）且数量 −1 时是 1，否则 0。
    safe = _doc(doc)
    line = _doc(bag.get("prpo_line", ""))
    rows = f"(SELECT COUNT(*) FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {safe})"
    hit = f"(SELECT COUNT(*) FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {safe} AND s.iPOsID = {line} AND s.iQuantity = -1)"
    expr = f"CASE WHEN ISNULL(h.iBillType, 0) = 1 AND {rows} = 1 AND {hit} = 1 THEN N'1' ELSE N'0' END"
    return _one(expr, f"FROM dbo.PU_ArrivalVouch AS h WHERE h.ID = {safe}")


_READBACK = {
    "prpo_poret": _prpo_poret,
    "prpo_parr": _prpo_parr,
    "prpo_link": _prpo_link,
}


def has_misc(name: str) -> bool:
    return name in _READBACK


def misc_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def misc_samples() -> list[tuple[str, str]]:
    bag = {"prpo_line": "100"}
    rows = [("edit-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in MISC_PACKS]
    rows.extend(("edit-" + name.replace("_", "-"), fn("100", bag)) for name, fn in _READBACK.items())
    return rows


# ---- 正文 ----


def purchase_return_po_gen(env: Env) -> dict:
    # 参照采购订单行退 1 个；来源类型写明 purchase_order。
    line = {"source_line_id": int(_doc(env.bag.get("prpo_line", ""))), "quantity": 1}
    body = {"type": _KIND, "source_type": "purchase_order", "id": int(_doc(env.focus))}
    body.update({"head": {"dDate": env.today}, "lines": [line]})
    return body


MISC_BUILDERS = {"purchase_return_po_gen": purchase_return_po_gen}


# ---- 用例 ----


def _cmp(got: str, pred: str) -> Step:
    return Step("custom", fields=(("got", got), ("pred", pred)), sql_name="bag_compare", id_from="none")


def _cycle() -> tuple[Step, ...]:
    ref = (("type", _KIND), ("id", "$id"))
    return (
        call_made(_VERIFY, ref + (("action", "verify"),), (("verified_by", "$opname"),)),
        call_made(_VERIFY, ref + (("action", "unverify"),), (("verified_by", ""),)),
        call_made("/v1/vouchers/delete", ref, (("deleted", "true"),)),
        call_made("/v1/vouchers/load", ref, (), 404, "not_found"),
    )


def _purchase_return_po() -> CaseSpec:
    # 参照订单行退 1 个：订单行 fPoRetQuantity +1、iArrQTY −1；审核、弃审、删除后两者都回到原值。
    # 生单后的回读先记进 bag，删除之后再判，断言失败也不会把退货单留在库里。
    names = ("prpo_poret", "prpo_parr")
    made = (("type", _KIND), ("code", "$nonempty"), ("source_type", "purchase_order"))
    steps = tuple(stash_step(name, name, "need") for name in names)
    steps += (apply_step(_GEN, "purchase_return_po_gen", "need", made),)
    steps += tuple(stash_step(name, name + "_gen", "need") for name in names)
    steps += (stash_step("prpo_link", "prpo_link_gen"),)
    steps += _cycle()
    steps += (
        _cmp("prpo_poret_gen", "plus1:prpo_poret"),
        _cmp("prpo_parr_gen", "shift:-1:prpo_parr"),
        _cmp("prpo_link_gen", "eq:1"),
    )
    steps += tuple(pred_step(name, "same:" + name, "need") for name in names)
    title = "采购订单生成采购退货单：订单行累计到货、累计退货回写，审核弃审后删除回退"
    return CaseSpec("purchase-return-po-cycle", title, ("prpo_src", "prpo_line"), steps, True)


def misc_cases() -> list[CaseSpec]:
    return [_purchase_return_po()]
