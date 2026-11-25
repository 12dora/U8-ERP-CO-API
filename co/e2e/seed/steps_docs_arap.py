"""造单据（六）：收付款单。C9999、S9999 始终不碰（回归要「没有往来的客户、供应商」）。

- ar_receipt：C0001 收款 100（结算方式 1，结算科目 = data.GL 的银行科目），审核，再 arap/voucher 制单。
- ap_payment：S0001 付款 100，审核，不制单。
- ar_bill / ap_bill：C0001 应收单、S0001 应付单各一张（金额 100），不审核（载入、应收应付单按最新一张取）。
- ar_receipt2：C0001 第二张收款 100，审核，不制单（按客户列表要同一客户至少两张；核销用它的余额）。
- arwo_so → arwo_dispatch → arwo_sale_out → arwo_invoice：C0001 单行订单（审核）发货 2 个（审核）、整单出库（不审核）、
  全数开专用发票，复核并应收审核。发票没有剩余可开数量，不会被发票生单、退货类挑单选中；
  和 ar_receipt2 一起给核销用例（同一客户、本币、已应收审核的发票行和有余额的收款单行）。
表头摘要 cDigest 写标记，按它找；是否已制单看 Ap_CloseBill.cPzID。
往来受控科目按会计年度在 code 里取：应收 1122 开头、带客户辅助的末级科目，应付 2202 开头、带供应商辅助的末级科目。
"""

from __future__ import annotations

import re

from co.e2e.seed import data
from co.e2e.seed.base import quoted
from co.e2e.seed.context import SeedContext, SeedError
from co.e2e.seed.steps_docs_base import (
    VERIFY,
    Doc,
    create_doc,
    doc_step,
    ensure_verified,
    find_ids,
    find_marked,
    generate_doc,
    line_ids,
    marker,
    need_one,
    one_or_none,
    post_ok,
    safe_id,
    verify_after,
)
from co.e2e.seed.steps_docs_sale import CUSTOMER, DL_LINES_SQL, INVOICE_TYPE, SO_LINES_SQL, WH, so_body

AMOUNT = 100
SETTLE = data.SETTLE[0].code
_YEAR = re.compile(r"^[0-9]{4}\Z")

# 类型 → (cFlag, cVouchType, 科目前缀, 辅助核算列)
SIDES = {
    "ar_receipt": ("AR", "48", "1122", "bcus"),
    "ap_payment": ("AP", "49", "2202", "bsup"),
}
_FIND_SQL = (
    "SELECT iID AS id FROM Ap_CloseBill WHERE cFlag = N'{flag}' AND cVouchType = N'{vouch}'"
    " AND cDigest = {mark} ORDER BY iID"
)
_KM_SQL = (
    "SELECT TOP (1) LTRIM(RTRIM(ccode)) AS code FROM code WHERE iyear = {year} AND bend = 1 AND {aux} = 1"
    " AND ccode LIKE N'{prefix}%' ORDER BY ccode"
)
_PZ_SQL = "SELECT COUNT(*) AS n FROM Ap_CloseBill WHERE iID = {id} AND ISNULL(cPzID, N'') <> N''"

# 应收单 / 应付单：类型 → (cFlag, cVouchType, 往来单位, 同一侧收付款单类型)。表体科目按前缀依次找不带辅助核算的末级科目，
# 与回归用例挑单的退路一致。
BILLS = {
    "ar_bill": ("AR", "R0", data.CUSTOMERS[0].code, "ar_receipt"),
    "ap_bill": ("AP", "P0", data.VENDORS[0].code, "ap_payment"),
}
BILL_PREFIXES = {"ar_bill": ("6001", "6051", "6301", "2241"), "ap_bill": ("6401", "1403", "6602", "2241")}
_BILL_FIND_SQL = (
    "SELECT Auto_ID AS id FROM Ap_Vouch WHERE cFlag = N'{flag}' AND cVouchType = N'{vouch}'"
    " AND cDigest = {mark} ORDER BY Auto_ID"
)
_PLAIN = "bdept = 0 AND bperson = 0 AND bcus = 0 AND bsup = 0 AND bitem = 0 AND bbank = 0"
_BILL_KM_SQL = (
    "SELECT TOP (1) LTRIM(RTRIM(ccode)) AS code FROM code WHERE iyear = {year} AND bend = 1 AND " + _PLAIN
    + " AND ({likes}) ORDER BY CASE {rank} ELSE {last} END, ccode"
)

# 核销用的销售链：发货、开票数量；发票复核、应收审核的状态。
WO_QTY = 2
_SALE_OUT_SQL = "SELECT ID AS id FROM rdrecord32 WHERE cDLCode = N'{id}' ORDER BY ID"
_INV_STATE_SQL = (
    "SELECT LTRIM(RTRIM(ISNULL(cChecker, N''))) AS checker, LTRIM(RTRIM(ISNULL(cVerifier, N''))) AS verifier"
    " FROM SaleBillVouch WHERE SBVID = {id}"
)


# ---- 正文（纯函数） ----


def receipt_body(day: str, km: str, key: str = "ar_receipt") -> dict:
    head = {
        "cDwCode": data.CUSTOMERS[0].code,
        "cCode": data.bank_account(),
        "cSSCode": SETTLE,
        "cDeptCode": data.DEPT_SALES,
        "cPerson": data.SALES_PERSON,
        "dVouchDate": day,
        "cDigest": marker(key),
    }
    return {"type": "ar_receipt", "head": head, "lines": [{"iType": 0, "cKm": km, "iAmt": AMOUNT}]}


def payment_body(day: str, km: str) -> dict:
    head = {
        "cDwCode": data.VENDORS[0].code,
        "cCode": data.bank_account(),
        "cSSCode": SETTLE,
        "cDeptCode": data.DEPT_PURCHASE,
        "cPerson": data.PURCHASE_PERSON,
        "dVouchDate": day,
        "cDigest": marker("ap_payment"),
    }
    return {"type": "ap_payment", "head": head, "lines": [{"iType": 0, "cKm": km, "iAmt": AMOUNT}]}


def voucher_body(doc_id: int) -> dict:
    return {"flag": "AR", "type": "ar_receipt", "id": doc_id}


def km_sql(kind: str, day: str) -> str:
    year = day[:4]
    if _YEAR.fullmatch(year) is None:
        raise SeedError("日期不合法：" + day)
    _flag, _vouch, prefix, aux = SIDES[kind]
    return _KM_SQL.format(year=year, aux=aux, prefix=prefix)


def bill_km_sql(kind: str, day: str) -> str:
    year = day[:4]
    if _YEAR.fullmatch(year) is None:
        raise SeedError("日期不合法：" + day)
    prefixes = BILL_PREFIXES[kind]
    likes = " OR ".join("ccode LIKE N'" + p + "%'" for p in prefixes)
    rank = " ".join("WHEN ccode LIKE N'" + p + "%' THEN " + str(n) for n, p in enumerate(prefixes))
    return _BILL_KM_SQL.format(year=year, likes=likes, rank=rank, last=len(prefixes))


def bill_body(kind: str, day: str, ctl: str, km: str) -> dict:
    # 形状同回归用例的应收单 / 应付单正文：表头受控科目 cCode，表体对方科目 cCode、金额 iAmount。
    _flag, _vouch, unit, _side = BILLS[kind]
    head = {"cDwCode": unit, "cCode": ctl, "dVouchDate": day, "cDigest": marker(kind)}
    if kind == "ar_bill":
        head.update({"cDeptCode": data.DEPT_SALES, "cPerson": data.SALES_PERSON})
    return {"type": kind, "head": head, "lines": [{"cCode": km, "iAmount": AMOUNT}]}


def wo_so_body(day: str) -> dict:
    body = so_body(day, "arwo_so")
    body["lines"] = body["lines"][:1]
    return body


def wo_dispatch_body(so_id: int, so_line_id: int) -> dict:
    line = {"source_line_id": so_line_id, "quantity": WO_QTY, "cwhcode": WH}
    head = {"cwhcode": WH, "cmemo": marker("arwo_dispatch")}
    return {"type": "dispatch", "source_type": "sale_order", "id": so_id, "head": head, "lines": [line]}


def wo_invoice_body(dl_id: int, dl_line_id: int) -> dict:
    head = {"cvouchtype": INVOICE_TYPE, "cmemo": marker("arwo_invoice")}
    line = {"source_line_id": dl_line_id, "quantity": WO_QTY}
    return {"type": "sale_invoice", "id": dl_id, "head": head, "lines": [line]}


# ---- 查找、新建 ----


def _finder(kind: str):
    flag, vouch, _prefix, _aux = SIDES[kind]

    def find(ctx: SeedContext) -> list[int]:
        return find_ids(ctx, _FIND_SQL.format(flag=flag, vouch=vouch, mark=quoted([marker(kind)])))

    return find


def _receipt_finder(key: str):
    def find(ctx: SeedContext) -> list[int]:
        return find_ids(ctx, _FIND_SQL.format(flag="AR", vouch="48", mark=quoted([marker(key)])))

    return find


def _bill_finder(kind: str):
    flag, vouch, _unit, _side = BILLS[kind]

    def find(ctx: SeedContext) -> list[int]:
        return find_ids(ctx, _BILL_FIND_SQL.format(flag=flag, vouch=vouch, mark=quoted([marker(kind)])))

    return find


def control_km(ctx: SeedContext, kind: str) -> str:
    rows = ctx.rows(km_sql(kind, ctx.day))
    code = rows[0].get("code", "").strip() if rows else ""
    if not code:
        raise SeedError(kind + "：科目表里找不到往来受控科目（" + SIDES[kind][2] + " 开头的末级科目）")
    return code


def _make(kind: str):
    build = receipt_body if kind == "ar_receipt" else payment_body
    return lambda ctx: create_doc(ctx, build(ctx.day, control_km(ctx, kind)))


def bill_km(ctx: SeedContext, kind: str) -> str:
    rows = ctx.rows(bill_km_sql(kind, ctx.day))
    code = rows[0].get("code", "").strip() if rows else ""
    if not code:
        heads = "、".join(BILL_PREFIXES[kind])
        raise SeedError(kind + "：科目表里找不到表体科目（" + heads + " 开头、不带辅助核算的末级科目）")
    return code


def _make_bill(kind: str):
    side = BILLS[kind][3]
    return lambda ctx: create_doc(ctx, bill_body(kind, ctx.day, control_km(ctx, side), bill_km(ctx, kind)))


def _make_receipt2(ctx: SeedContext) -> int:
    return create_doc(ctx, receipt_body(ctx.day, control_km(ctx, "ar_receipt"), "ar_receipt2"))


def find_wo_so(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "sale_order", "arwo_so")


def find_wo_dispatch(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "dispatch", "arwo_dispatch")


def find_wo_sale_out(ctx: SeedContext) -> list[int]:
    dl_id = one_or_none(ctx, find_wo_dispatch)
    return find_ids(ctx, _SALE_OUT_SQL.format(id=safe_id(dl_id))) if dl_id else []


def find_wo_invoice(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "sale_invoice", "arwo_invoice")


def _make_wo_dispatch(ctx: SeedContext) -> int:
    so_id = need_one(ctx, find_wo_so, "arwo_so")
    return generate_doc(ctx, wo_dispatch_body(so_id, line_ids(ctx, SO_LINES_SQL, so_id)[0]))


def _make_wo_sale_out(ctx: SeedContext) -> int:
    # 销售出库生单不能带表头字段，按来源发货单找。
    return generate_doc(ctx, {"type": "sale_out", "id": need_one(ctx, find_wo_dispatch, "arwo_dispatch")})


def _make_wo_invoice(ctx: SeedContext) -> int:
    dl_id = need_one(ctx, find_wo_dispatch, "arwo_dispatch")
    return generate_doc(ctx, wo_invoice_body(dl_id, line_ids(ctx, DL_LINES_SQL, dl_id)[0]))


def _invoice_after(ctx: SeedContext, doc_id: int) -> None:
    # 先复核（cChecker）再应收审核（cVerifier）；按 SQL 看状态，重跑时已做过的不再发（重复审核是 409）。
    rows = ctx.rows(_INV_STATE_SQL.format(id=safe_id(doc_id)))
    state = rows[0] if rows else {}
    for col, action, what in (("checker", "verify", "复核"), ("verifier", "arap_verify", "应收审核")):
        if not state.get(col, "").strip():
            post_ok(ctx, VERIFY, {"type": "sale_invoice", "id": doc_id, "action": action}, "销售发票" + what)


def _receipt_after(ctx: SeedContext, doc_id: int) -> None:
    ensure_verified(ctx, "ar_receipt", doc_id)
    rows = ctx.rows(_PZ_SQL.format(id=safe_id(doc_id)))
    if rows and rows[0].get("n", "0").strip() not in ("", "0"):
        return
    post_ok(ctx, "v1/arap/voucher", voucher_body(doc_id), "收款单制单")


STEPS = (
    doc_step(Doc("ar_receipt", "收款单（审核并制单）", _finder("ar_receipt"), _make("ar_receipt"), _receipt_after)),
    doc_step(
        Doc(
            "ap_payment",
            "付款单（审核）",
            _finder("ap_payment"),
            _make("ap_payment"),
            lambda ctx, doc_id: ensure_verified(ctx, "ap_payment", doc_id),
        )
    ),
    doc_step(Doc("ar_bill", "应收单（不审核）", _bill_finder("ar_bill"), _make_bill("ar_bill"))),
    doc_step(Doc("ap_bill", "应付单（不审核）", _bill_finder("ap_bill"), _make_bill("ap_bill"))),
    doc_step(
        Doc("ar_receipt2", "第二张收款单（审核，不制单）", _receipt_finder("ar_receipt2"), _make_receipt2, verify_after("ar_receipt"))
    ),
    doc_step(
        Doc(
            "arwo_so",
            "核销用销售订单（审核）",
            find_wo_so,
            lambda ctx: create_doc(ctx, wo_so_body(ctx.day)),
            verify_after("sale_order"),
        )
    ),
    doc_step(Doc("arwo_dispatch", "核销用发货单（审核）", find_wo_dispatch, _make_wo_dispatch, verify_after("dispatch"))),
    doc_step(Doc("arwo_sale_out", "核销用销售出库单（不审核）", find_wo_sale_out, _make_wo_sale_out)),
    doc_step(Doc("arwo_invoice", "核销用销售专用发票（复核、应收审核）", find_wo_invoice, _make_wo_invoice, _invoice_after)),
)
