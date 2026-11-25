"""造单据（三）：采购（普通件，不要来料检验）。供应商 S0001，部门、业务员是采购部的。

采购订单的字段名照回归用例（casespec/edit_close_gen_bodies.po_body）：只给无税单价 iUnitPrice 和税率，金额由桥算，
imoney 之类不能写（400「不能设置字段」）。

U8 不许同一张采购订单既有到货又直接入库（409「该操作会造成订单到货和入库同时存在」），所以到货链和
直接入库链各用一张订单：

- po_open：两行的采购订单，不审核。
- po_arr：两行的采购订单，审核；只走到货链。第一行到货 3 个，两行都留有余量（到货、退货来源）。
- po_arr_arrival：参照 po_arr 第一行到货 3 个（仓库 06），审核。
- po_in：两行的采购订单，审核；只直接入库。第一行入库 2 个，两行都留有余量（入库、开票来源）。
  建在 po_arr 之后：入库挑单按订单号倒序，且排除有到货的订单。
- po_in_inbound：参照 po_in 第一行入库 2 个（仓库 06），审核。
- purchase_invoice：参照 po_in_inbound 开 1 个的专用发票（发票号固定 SEEDINV001），不复核。按发票号找。

追加的几步（不改上面各步的数量）：

- po_arr_open：两行的采购订单，审核；只走到货链。
- po_arr_open_arrival：参照 po_arr_open 第一行到货 2 个，不审核（arrival-verify 挑未审核的到货单）。
- po_inv：两行的采购订单，审核；只直接入库。
- po_inv_inbound：参照 po_inv 第一行入库 4 个，审核。
- po_inv_invoice：参照 po_inv_inbound 开 1 个的专用发票（发票号 SEEDINV002），采购复核，不做应付审核。
  入库行剩余可开票 3 个（purchase-invoice-update 要 >= 2，生单用例另要 >= 1）；
  已复核、没有应付明细的发票给 ap-invoice-audit 挑。

旧基线里的 po_main（同一张订单既到货又入库）留着不动，按标记找的新步骤照样补齐新订单。
"""

from __future__ import annotations

from collections.abc import Callable

from co.e2e.seed import data
from co.e2e.seed.base import quoted
from co.e2e.seed.context import SeedContext
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

VENDOR = data.VENDORS[0].code
WH = data.WH_PLAIN[0]
PO_QTY = 5
PO_PRICE = 8
ARRIVE_QTY = 3
IN_QTY = 2
BILL_QTY = 1
INVOICE_CODE = "SEEDINV001"
INVOICE_TYPE = "01"
OPEN_ARRIVE_QTY = 2
INV_IN_QTY = 4
INV_CODE2 = "SEEDINV002"

PO_LINES_SQL = "SELECT ID AS id FROM PO_Podetails WHERE POID = {id} ORDER BY ID"
RD01_LINES_SQL = "SELECT AutoID AS id FROM rdrecords01 WHERE ID = {id} ORDER BY AutoID"
_INVOICE_SQL = "SELECT PBVID AS id FROM PurBillVouch WHERE cPBVCode = {code} ORDER BY PBVID"
_REVIEWED_SQL = "SELECT COUNT(*) AS n FROM PurBillVouch WHERE PBVID = {id} AND ISNULL(cVerifier, N'') <> N''"


# ---- 正文（纯函数） ----


def po_line(inv: str, qty: int) -> dict:
    return {"cInvCode": inv, "iQuantity": qty, "iUnitPrice": PO_PRICE, "iPerTaxRate": data.TAX_RATE}


def po_body(day: str, key: str, lines: list[dict]) -> dict:
    head = {
        "cVenCode": VENDOR,
        "cDepCode": data.DEPT_PURCHASE,
        "cPersonCode": data.PURCHASE_PERSON,
        "cPTCode": "CG",
        "cBusType": "普通采购",
        "cexch_name": data.CURRENCY,
        "nflat": 1,
        "iTaxRate": data.TAX_RATE,
        "dPODate": day,
        "cMemo": marker(key),
    }
    return {"type": "purchase_order", "head": head, "lines": lines}


def plain_po_body(day: str, key: str) -> dict:
    return po_body(day, key, [po_line(data.PLAIN_INVENTORY, PO_QTY), po_line(data.PLAIN_INVENTORY, PO_QTY)])


def arrival_body(po_id: int, po_line_id: int, qty: int, key: str) -> dict:
    head = {"cWhCode": WH, "cMemo": marker(key)}
    line = {"source_line_id": po_line_id, "quantity": qty}
    return {"type": "arrival", "source_type": "purchase_order", "id": po_id, "head": head, "lines": [line]}


def purchase_in_body(po_id: int, po_line_id: int, key: str, qty: int = IN_QTY) -> dict:
    head = {"cwhcode": WH, "cmemo": marker(key)}
    line = {"source_line_id": po_line_id, "quantity": qty}
    return {"type": "purchase_in", "source_type": "purchase_order", "id": po_id, "head": head, "lines": [line]}


def invoice_body(rd_id: int, rd_line_id: int, code: str = INVOICE_CODE, key: str = "purchase_invoice") -> dict:
    head = {"cPBVCode": code, "cPBVBillType": INVOICE_TYPE, "cPBVMemo": marker(key)}
    line = {"source_line_id": rd_line_id, "quantity": BILL_QTY}
    return {"type": "purchase_invoice", "id": rd_id, "head": head, "lines": [line]}


# ---- 查找 ----


def find_po_open(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_order", "po_open")


def find_po_arr(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_order", "po_arr")


def find_po_in(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_order", "po_in")


def find_arrival(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "arrival", "po_arr_arrival")


def find_purchase_in(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_in", "po_in_inbound")


def find_invoice(ctx: SeedContext) -> list[int]:
    return find_ids(ctx, _INVOICE_SQL.format(code=quoted([INVOICE_CODE])))


def find_po_arr_open(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_order", "po_arr_open")


def find_open_arrival(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "arrival", "po_arr_open_arrival")


def find_po_inv(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_order", "po_inv")


def find_inv_inbound(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_in", "po_inv_inbound")


def find_invoice2(ctx: SeedContext) -> list[int]:
    return find_ids(ctx, _INVOICE_SQL.format(code=quoted([INV_CODE2])))


# ---- 新建 ----


def _make_arrival(ctx: SeedContext) -> int:
    po_id = need_one(ctx, find_po_arr, "po_arr")
    first = line_ids(ctx, PO_LINES_SQL, po_id)[0]
    return generate_doc(ctx, arrival_body(po_id, first, ARRIVE_QTY, "po_arr_arrival"))


def _make_purchase_in(ctx: SeedContext) -> int:
    po_id = need_one(ctx, find_po_in, "po_in")
    first = line_ids(ctx, PO_LINES_SQL, po_id)[0]
    return generate_doc(ctx, purchase_in_body(po_id, first, "po_in_inbound"))


def _make_invoice(ctx: SeedContext) -> int:
    rd_id = need_one(ctx, find_purchase_in, "po_in_inbound")
    first = line_ids(ctx, RD01_LINES_SQL, rd_id)[0]
    return generate_doc(ctx, invoice_body(rd_id, first))


def _make_open_arrival(ctx: SeedContext) -> int:
    po_id = need_one(ctx, find_po_arr_open, "po_arr_open")
    first = line_ids(ctx, PO_LINES_SQL, po_id)[0]
    return generate_doc(ctx, arrival_body(po_id, first, OPEN_ARRIVE_QTY, "po_arr_open_arrival"))


def _make_inv_inbound(ctx: SeedContext) -> int:
    po_id = need_one(ctx, find_po_inv, "po_inv")
    first = line_ids(ctx, PO_LINES_SQL, po_id)[0]
    return generate_doc(ctx, purchase_in_body(po_id, first, "po_inv_inbound", INV_IN_QTY))


def _make_invoice2(ctx: SeedContext) -> int:
    rd_id = need_one(ctx, find_inv_inbound, "po_inv_inbound")
    first = line_ids(ctx, RD01_LINES_SQL, rd_id)[0]
    return generate_doc(ctx, invoice_body(rd_id, first, INV_CODE2, "po_inv_invoice"))


def _review_invoice(ctx: SeedContext, doc_id: int) -> None:
    # 采购发票的 vouchers/verify 是采购复核；先按 SQL 看复核人，已复核的不再发（再复核是 409）。
    rows = ctx.rows(_REVIEWED_SQL.format(id=safe_id(doc_id)))
    if rows and rows[0].get("n", "0").strip() not in ("", "0"):
        return
    body = {"type": "purchase_invoice", "id": doc_id, "action": "verify"}
    post_ok(ctx, VERIFY, body, "复核 purchase_invoice " + str(doc_id))


def _verified_po(key: str, title: str, find: Callable[[SeedContext], list[int]]) -> Doc:
    return Doc(key, title, find, lambda ctx: create_doc(ctx, plain_po_body(ctx.day, key)), verify_after("purchase_order"))


# 顺序要紧：po_in 在 po_arr 之后建（主键更大），入库挑单按订单号倒序先看到它。
STEPS = (
    doc_step(Doc("po_open", "采购订单（未审核）", find_po_open, lambda ctx: create_doc(ctx, plain_po_body(ctx.day, "po_open")))),
    doc_step(_verified_po("po_arr", "采购订单（审核，只走到货，留余量）", find_po_arr)),
    doc_step(Doc("po_arr_arrival", "到货单（参照 po_arr，审核）", find_arrival, _make_arrival, verify_after("arrival"))),
    doc_step(_verified_po("po_in", "采购订单（审核，只直接入库，留余量）", find_po_in)),
    doc_step(Doc("po_in_inbound", "采购入库单（参照 po_in，审核）", find_purchase_in, _make_purchase_in, verify_after("purchase_in"))),
    doc_step(Doc("purchase_invoice", "采购专用发票（参照入库单，不复核）", find_invoice, _make_invoice)),
    # 追加：未审核到货单；入库剩余可开票 >= 2；已复核、未应付审核的发票。
    doc_step(_verified_po("po_arr_open", "采购订单（审核，只走到货，给未审核到货单）", find_po_arr_open)),
    doc_step(Doc("po_arr_open_arrival", "到货单（参照 po_arr_open，不审核）", find_open_arrival, _make_open_arrival)),
    doc_step(_verified_po("po_inv", "采购订单（审核，只直接入库，给发票修改）", find_po_inv)),
    doc_step(
        Doc("po_inv_inbound", "采购入库单（参照 po_inv 入库 4 个，审核）", find_inv_inbound, _make_inv_inbound, verify_after("purchase_in"))
    ),
    doc_step(
        Doc("po_inv_invoice", "采购专用发票（参照 po_inv_inbound 开 1 个，复核）", find_invoice2, _make_invoice2, _review_invoice)
    ),
)
