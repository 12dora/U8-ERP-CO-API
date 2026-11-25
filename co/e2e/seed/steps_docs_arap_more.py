"""造单据（七）：应付核销链、预收 / 预付款单（只追加，不改前面各步的单据和状态）。

- apwo_po → apwo_in → apwo_invoice：S0001 单行普通采购订单 2 个（审核），参照它直接入库 2 个（审核），
  参照入库单全数开专用发票（发票号 SEEDINV003），采购复核并应付审核，不制单。订单、入库都不留余量，
  不会被入库、开票类挑单选中；和 ap_payment 一起给 ap-writeoff / ap-writeoff-auto（同一供应商、本币、
  已应付审核的发票行和有余额的付款单行）。SEEDINV001（不复核）、SEEDINV002（复核、不应付审核，ap-invoice-audit 用）不动。
- ar_prepay：C0001 收款单一行预收款（iType=1，bPrePay=1）50，审核，不制单。
- ap_prepay：S0001 付款单一行预付款（iType=1）50，审核，不制单。
  预收 / 预付行给 ar-writeoff-prepay / ap-writeoff-prepay；现有核销用例的挑单只取 bPrePay=0 的行，不受影响。
"""

from __future__ import annotations

from co.e2e.seed import data
from co.e2e.seed.base import quoted
from co.e2e.seed.context import SeedContext
from co.e2e.seed.steps_docs_arap import control_km, payment_body, receipt_body
from co.e2e.seed.steps_docs_base import (
    VERIFY,
    Doc,
    create_doc,
    doc_step,
    find_ids,
    find_marked,
    generate_doc,
    line_ids,
    marker,
    need_one,
    post_ok,
    safe_id,
    verify_after,
)
from co.e2e.seed.steps_docs_purchase import (
    INVOICE_TYPE,
    PO_LINES_SQL,
    RD01_LINES_SQL,
    po_body,
    po_line,
    purchase_in_body,
)

WO_QTY = 2
WO_INVOICE_CODE = "SEEDINV003"
PREPAY_AMOUNT = 50
PREPAY_TYPE = 1

_INVOICE_SQL = "SELECT PBVID AS id FROM PurBillVouch WHERE cPBVCode = {code} ORDER BY PBVID"
# 采购复核看 cVerifier，应付审核看 cPBVVerifier（同核销挑单）。
_INV_STATE_SQL = (
    "SELECT LTRIM(RTRIM(ISNULL(cVerifier, N''))) AS checker, LTRIM(RTRIM(ISNULL(cPBVVerifier, N''))) AS verifier"
    " FROM PurBillVouch WHERE PBVID = {id}"
)
_CLOSE_FIND_SQL = (
    "SELECT iID AS id FROM Ap_CloseBill WHERE cFlag = N'{flag}' AND cVouchType = N'{vouch}'"
    " AND cDigest = {mark} ORDER BY iID"
)
# 预收 / 预付：步骤名 → (单据类型, cFlag, cVouchType)。
PREPAYS = {"ar_prepay": ("ar_receipt", "AR", "48"), "ap_prepay": ("ap_payment", "AP", "49")}


# ---- 正文（纯函数） ----


def wo_po_body(day: str) -> dict:
    return po_body(day, "apwo_po", [po_line(data.PLAIN_INVENTORY, WO_QTY)])


def wo_in_body(po_id: int, po_line_id: int) -> dict:
    return purchase_in_body(po_id, po_line_id, "apwo_in", WO_QTY)


def wo_invoice_body(rd_id: int, rd_line_id: int) -> dict:
    head = {"cPBVCode": WO_INVOICE_CODE, "cPBVBillType": INVOICE_TYPE, "cPBVMemo": marker("apwo_invoice")}
    line = {"source_line_id": rd_line_id, "quantity": WO_QTY}
    return {"type": "purchase_invoice", "id": rd_id, "head": head, "lines": [line]}


def prepay_body(key: str, day: str, km: str) -> dict:
    # 形状同 ar_receipt / ap_payment，只换摘要标记和表体：一行预收 / 预付款。
    body = receipt_body(day, km, key) if PREPAYS[key][0] == "ar_receipt" else payment_body(day, km)
    body["head"]["cDigest"] = marker(key)
    body["lines"] = [{"iType": PREPAY_TYPE, "cKm": km, "iAmt": PREPAY_AMOUNT}]
    return body


# ---- 查找、新建 ----


def find_wo_po(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_order", "apwo_po")


def find_wo_in(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_in", "apwo_in")


def find_wo_invoice(ctx: SeedContext) -> list[int]:
    return find_ids(ctx, _INVOICE_SQL.format(code=quoted([WO_INVOICE_CODE])))


def _make_wo_in(ctx: SeedContext) -> int:
    po_id = need_one(ctx, find_wo_po, "apwo_po")
    return generate_doc(ctx, wo_in_body(po_id, line_ids(ctx, PO_LINES_SQL, po_id)[0]))


def _make_wo_invoice(ctx: SeedContext) -> int:
    rd_id = need_one(ctx, find_wo_in, "apwo_in")
    return generate_doc(ctx, wo_invoice_body(rd_id, line_ids(ctx, RD01_LINES_SQL, rd_id)[0]))


def _invoice_after(ctx: SeedContext, doc_id: int) -> None:
    # 先采购复核再应付审核；按 SQL 看状态，重跑时已做过的不再发（重复是 409）。
    rows = ctx.rows(_INV_STATE_SQL.format(id=safe_id(doc_id)))
    state = rows[0] if rows else {}
    for col, action, what in (("checker", "verify", "复核"), ("verifier", "arap_verify", "应付审核")):
        if not state.get(col, "").strip():
            post_ok(ctx, VERIFY, {"type": "purchase_invoice", "id": doc_id, "action": action}, "采购发票" + what)


def _prepay_finder(key: str):
    _kind, flag, vouch = PREPAYS[key]

    def find(ctx: SeedContext) -> list[int]:
        return find_ids(ctx, _CLOSE_FIND_SQL.format(flag=flag, vouch=vouch, mark=quoted([marker(key)])))

    return find


def _prepay_doc(key: str, title: str) -> Doc:
    kind = PREPAYS[key][0]

    def make(ctx: SeedContext) -> int:
        return create_doc(ctx, prepay_body(key, ctx.day, control_km(ctx, kind)))

    return Doc(key, title, _prepay_finder(key), make, verify_after(kind))


STEPS = (
    doc_step(
        Doc(
            "apwo_po",
            "核销用采购订单（审核）",
            find_wo_po,
            lambda ctx: create_doc(ctx, wo_po_body(ctx.day)),
            verify_after("purchase_order"),
        )
    ),
    doc_step(Doc("apwo_in", "核销用采购入库单（参照 apwo_po 全数入库，审核）", find_wo_in, _make_wo_in, verify_after("purchase_in"))),
    doc_step(Doc("apwo_invoice", "核销用采购专用发票（复核、应付审核）", find_wo_invoice, _make_wo_invoice, _invoice_after)),
    doc_step(_prepay_doc("ar_prepay", "预收款收款单（审核，不制单）")),
    doc_step(_prepay_doc("ap_prepay", "预付款付款单（审核，不制单）")),
)
