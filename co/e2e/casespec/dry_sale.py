"""写预演（rollback）用例：销售、采购单据。每个（类型, 操作）组合都是现有回归真写过的，挑单、正文构造器和回读沿用那些用例。

每步预演都核对：200、dry_run、mode=rollback、warnings、docs 里的单据映像，以及「什么都没变」（见 dry_ops.dry_step）。
为了预演修改、审核、关闭、删除，用例先像原用例那样真建一张单据，最后真删掉并回读来源累计数回到原值；
中途失败时 DRY_SUITE_CLEANUP（dry_cleanup）按记下的主键尽力弃审、删除。

dry-sale-order：新建 → 真建 → 修改、审核、锁定 → 真锁定 → 解锁 → 真解锁 → 真审核 → 关闭、弃审 → 真弃审 → 删除 → 真删除。
dry-dispatch-generate：销售订单生发货单（订单行累计发货不变）→ 真生单 → 审核 → 真审核 → 发货单生销售出库 → 真弃审 → 删除 → 真删除。
dry-sale-invoice：发货单生销售发票（累计开票不变）→ 真生单 → 复核、删除 → 真删除。
dry-sale-return：发货单生退货单，发货行、订单行回写都不变。
dry-po-cycle：采购订单新建 → 真建 → 修改、审核 → 真审核 → 关闭、弃审 → 真弃审 → 删除 → 真删除。
dry-arrival：采购订单生到货单（累计到货不变）→ 真生单 → 审核、删除 → 真删除。
dry-purchase-return：到货单生采购退货单，回写都不变。
dry-purchase-in：采购订单生采购入库（累计入库不变）→ 真生单 → 审核 → 真审核 → 弃审 → 真弃审 → 删除 → 真删除。
dry-purchase-invoice：采购入库生采购发票（累计开票不变）→ 真生单 → 复核、删除 → 真删除。
dry-purchase-requisition：请购单新建 → 真建 → 修改、审核 → 真审核 → 关闭、弃审 → 真弃审 → 删除 → 真删除。
"""

from __future__ import annotations

from co.e2e.casespec.returns_misc_bodies import REQ_DATA, SR_QTY
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_from, keep_step, pred_step, stash_step
from co.e2e.casespec.dry_steps import CLOSE, CREATE, DELETE, GEN, LOAD, LOCK, UPDATE, VERIFY, dry_doc, dry_new, ref

_GONE = (("deleted", "true"),)


def _real_verify(kind: str, key: str, action: str = "verify") -> Step:
    return call_from(VERIFY, ref(kind, ("action", action)), "bag:" + key)


def _real_drop(kind: str, key: str) -> tuple[Step, Step]:
    return (
        call_from(DELETE, ref(kind), "bag:" + key, _GONE),
        call_from(LOAD, ref(kind), "bag:" + key, (), (404, "not_found")),
    )


def _made(kind: str) -> tuple:
    return (("type", kind), ("code", "$nonempty"))


def _sale_order() -> CaseSpec:
    kind, key = "sale_order", "dry_so"
    src = "bag:" + key
    steps = (
        dry_new(CREATE, kind, "sale_order"),
        Step("create", sql_name="sale_order", checks=_made(kind)),
        keep_step(key),
        stash_step("so_line", "so_line", src),
        stash_step("so_qty", "so_qty", src),
        dry_doc(UPDATE, kind, src, builder="sale_update"),
        dry_doc(VERIFY, kind, src, ("action", "verify")),
        dry_doc(LOCK, kind, src, ("action", "lock")),
        call_from(LOCK, ref(kind, ("action", "lock")), src, (("locked", "true"),)),
        dry_doc(LOCK, kind, src, ("action", "unlock")),
        call_from(LOCK, ref(kind, ("action", "unlock")), src, (("locked", "false"),)),
        _real_verify(kind, key),
        dry_doc(CLOSE, kind, src, ("action", "close")),
        dry_doc(VERIFY, kind, src, ("action", "unverify")),
        _real_verify(kind, key, "unverify"),
        dry_doc(DELETE, kind, src),
    ) + _real_drop(kind, key)
    title = "预演：销售订单新建、修改、审核、锁定、解锁、关闭、弃审、删除，都回滚、单据不变"
    return CaseSpec("dry-sale-order", title, ("sale_master",), steps, True, tags=frozenset(("w:sale_order",)))


def _dispatch() -> CaseSpec:
    key = "dry_disp"
    src = "bag:" + key
    steps = (
        stash_step("so_fh", "so_fh", "need"),
        dry_new(GEN, "dispatch", "dispatch_gen", "need", watch="sale_order:$id"),
        pred_step("so_fh", "same:so_fh", "need"),
        apply_step(GEN, "dispatch_gen", "need", _made("dispatch")),
        keep_step(key),
        dry_doc(VERIFY, "dispatch", src, ("action", "verify")),
        _real_verify("dispatch", key),
        dry_new(GEN, "sale_out", "sale_out_gen", src, watch="dispatch:$id"),
        _real_verify("dispatch", key, "unverify"),
        dry_doc(DELETE, "dispatch", src),
    ) + _real_drop("dispatch", key) + (pred_step("so_fh", "same:so_fh", "need"),)
    title = "预演：销售订单生发货单、发货单审核、生销售出库、删除，都回滚，订单累计发货不变"
    return CaseSpec("dry-dispatch-generate", title, ("so_ship", "so_ship_line", "so_ship_wh"), steps, True)


def _sale_invoice() -> CaseSpec:
    key = "dry_sinv"
    src = "bag:" + key
    steps = (
        stash_step("inv_settle", "inv_settle", "need"),
        stash_step("inv_qty", "inv_qty", "need"),
        dry_new(GEN, "sale_invoice", "invoice_gen", "need", watch="dispatch:$id"),
        pred_step("inv_settle", "same:inv_settle", "need"),
        apply_step(GEN, "invoice_gen", "need", _made("sale_invoice")),
        keep_step(key),
        dry_doc(VERIFY, "sale_invoice", src, ("action", "verify")),
        dry_doc(DELETE, "sale_invoice", src),
    ) + _real_drop("sale_invoice", key) + (pred_step("inv_settle", "same:inv_settle", "need"),)
    title = "预演：发货单生销售发票、复核、删除，都回滚，累计开票不变"
    return CaseSpec("dry-sale-invoice", title, ("inv_src", "inv_src_line"), steps, True)


def _sale_return() -> CaseSpec:
    names = ("sr_ret", "sr_wkp", "sr_soret", "sr_sofh")
    room = Step("custom", fields=(("read", "sr_room"), ("min", str(SR_QTY))), sql_name="room", id_from="need")
    steps = (room,) + tuple(stash_step(name, name, "need") for name in names)
    steps += (dry_new(GEN, "sale_return", "sale_return_gen", "need", watch="dispatch:$id"),)
    steps += tuple(pred_step(name, "same:" + name, "need") for name in names)
    title = "预演：发货单生销售退货单，回滚后发货行、订单行回写都不变"
    return CaseSpec("dry-sale-return", title, ("sr_src", "sr_line"), steps, True)


def _po_cycle() -> CaseSpec:
    kind, key = "purchase_order", "dry_po"
    src = "bag:" + key
    steps = (
        dry_new(CREATE, kind, "purchase_order"),
        Step("create", sql_name="purchase_order", checks=_made(kind)),
        keep_step(key),
        stash_step("po_line", "po_line", src),
        dry_doc(UPDATE, kind, src, builder="po_qty"),
        dry_doc(VERIFY, kind, src, ("action", "verify")),
        _real_verify(kind, key),
        dry_doc(CLOSE, kind, src, ("action", "close")),
        dry_doc(VERIFY, kind, src, ("action", "unverify")),
        _real_verify(kind, key, "unverify"),
        dry_doc(DELETE, kind, src),
    ) + _real_drop(kind, key)
    title = "预演：采购订单新建、修改、审核、关闭、弃审、删除，都回滚、单据不变"
    return CaseSpec("dry-po-cycle", title, ("po_master",), steps, True, tags=frozenset(("w:purchase_order",)))


def _arrival() -> CaseSpec:
    key = "dry_arr"
    src = "bag:" + key
    steps = (
        stash_step("arr_qty", "arr_qty", "need"),
        dry_new(GEN, "arrival", "arrival_gen", "need", watch="purchase_order:$id"),
        pred_step("arr_qty", "same:arr_qty", "need"),
        apply_step(GEN, "arrival_gen", "need", _made("arrival")),
        keep_step(key),
        dry_doc(VERIFY, "arrival", src, ("action", "verify")),
        dry_doc(DELETE, "arrival", src),
    ) + _real_drop("arrival", key) + (pred_step("arr_qty", "same:arr_qty", "need"),)
    title = "预演：采购订单生到货单、到货单审核、删除，都回滚，累计到货不变"
    return CaseSpec("dry-arrival", title, ("arr_src", "arr_line", "arr_wh", "arr_over"), steps, True)


def _purchase_return() -> CaseSpec:
    names = ("pr_ret", "pr_poret", "pr_parr")
    steps = tuple(stash_step(name, name, "need") for name in names)
    steps += (dry_new(GEN, "purchase_return", "purchase_return_gen", "need", watch="arrival:$id"),)
    steps += tuple(pred_step(name, "same:" + name, "need") for name in names)
    title = "预演：到货单生采购退货单，回滚后到货行、订单行回写都不变"
    return CaseSpec("dry-purchase-return", title, ("pr_src", "pr_line"), steps, True)


def _purchase_in() -> CaseSpec:
    kind, key = "purchase_in", "dry_pin"
    src = "bag:" + key
    steps = (
        stash_step("po_got", "po_got", "need"),
        dry_new(GEN, kind, "pin_gen", "need", watch="purchase_order:$id"),
        pred_step("po_got", "same:po_got", "need"),
        apply_step(GEN, "pin_gen", "need", _made(kind)),
        keep_step(key),
        dry_doc(VERIFY, kind, src, ("action", "verify")),
        _real_verify(kind, key),
        dry_doc(VERIFY, kind, src, ("action", "unverify")),
        _real_verify(kind, key, "unverify"),
        dry_doc(DELETE, kind, src),
    ) + _real_drop(kind, key) + (pred_step("po_got", "same:po_got", "need"),)
    title = "预演：采购订单生采购入库、审核、弃审、删除，都回滚，累计入库不变"
    return CaseSpec("dry-purchase-in", title, ("po_recv", "po_recv_line", "po_recv_wh"), steps, True)


def _purchase_invoice() -> CaseSpec:
    kind, key = "purchase_invoice", "dry_pinv"
    src = "bag:" + key
    steps = (
        stash_step("pinv_billed", "pinv_billed", "need"),
        dry_new(GEN, kind, "purchase_invoice_gen", "need", watch="purchase_in:$id"),
        pred_step("pinv_billed", "same:pinv_billed", "need"),
        apply_step(GEN, "purchase_invoice_gen", "need", _made(kind)),
        keep_step(key),
        dry_doc(VERIFY, kind, src, ("action", "verify")),
        dry_doc(DELETE, kind, src),
    ) + _real_drop(kind, key) + (pred_step("pinv_billed", "same:pinv_billed", "need"),)
    title = "预演：采购入库生采购发票、复核、删除，都回滚，累计开票不变"
    return CaseSpec("dry-purchase-invoice", title, ("pinv_src", "pinv_line"), steps, True)


def _requisition() -> CaseSpec:
    kind, key = "purchase_requisition", "dry_req"
    src = "bag:" + key
    steps = (
        dry_new(CREATE, kind, "purchase_requisition_new"),
        Step("create", sql_name="purchase_requisition_new", checks=_made(kind)),
        keep_step(key),
        dry_doc(UPDATE, kind, src, builder="purchase_requisition_upd"),
        dry_doc(VERIFY, kind, src, ("action", "verify")),
        _real_verify(kind, key),
        dry_doc(CLOSE, kind, src, ("action", "close")),
        dry_doc(VERIFY, kind, src, ("action", "unverify")),
        _real_verify(kind, key, "unverify"),
        dry_doc(DELETE, kind, src),
    ) + _real_drop(kind, key)
    title = "预演：请购单新建、修改、审核、关闭、弃审、删除，都回滚、单据不变"
    return CaseSpec("dry-purchase-requisition", title, (), steps, True, data=REQ_DATA)


# 用例名 → 补偿计划（bag 键, 单据类型, 是否先弃审），按下游到上游排好；dry_cleanup 用。
SALE_PLANS = {
    "dry-sale-order": (("dry_so", "sale_order", True),),
    "dry-dispatch-generate": (("dry_disp", "dispatch", True),),
    "dry-sale-invoice": (("dry_sinv", "sale_invoice", True),),
    "dry-po-cycle": (("dry_po", "purchase_order", True),),
    "dry-arrival": (("dry_arr", "arrival", True),),
    "dry-purchase-in": (("dry_pin", "purchase_in", True),),
    "dry-purchase-invoice": (("dry_pinv", "purchase_invoice", True),),
    "dry-purchase-requisition": (("dry_req", "purchase_requisition", True),),
}


def dry_sale_cases() -> list[CaseSpec]:
    return [
        _sale_order(), _dispatch(), _sale_invoice(), _sale_return(), _po_cycle(),
        _arrival(), _purchase_return(), _purchase_in(), _purchase_invoice(), _requisition(),
    ]
