"""写预演用例：库存、生产、质量、应收应付单据。挑单、正文构造器和回读沿用现有回归用例（写法同 dry_sale）。

dry-other-in：其他入库新建 → 真建 → 修改、审核 → 真审核 → 弃审 → 真弃审 → 删除 → 真删除。
dry-other-out：其他出库新建。
dry-transfer：调拨单新建 → 真建 → 修改、审核、删除 → 真删除。
dry-shape-change / dry-stock-check：形态转换单、盘点单新建（可选）。
dry-transfer-request：调拨申请单新建 → 真建 → 修改、审核、删除 → 真删除（可选）。
dry-material-out：生产订单生材料出库，子件已领量不变。
dry-product-in：产品检验单生产成品入库，合格入库量不变。
dry-ar-receipt：收款单新建 → 真建 → 修改、审核 → 真审核 → 弃审 → 真弃审 → 删除 → 真删除；应收单新建。
dry-ap-payment：付款单、应付单新建。
dry-mo-close：生产订单按行关闭（rollback），行仍打开（可选）。
dry-qm-product：生产订单生产品报检单（rollback，DeclaredQty 不变）→ 真生单 → 报检单生产品检验单（validate，不建单）
  → 删除报检单（validate）→ 真删除（可选）。
"""

from __future__ import annotations

from co.e2e.casespec.arap_ctl import ctl_step
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_from, keep_step, pred_step, stash_step
from co.e2e.casespec.qm_write import TPL76
from co.e2e.casespec.dry_steps import CLOSE, CREATE, DELETE, GEN, LOAD, UPDATE, VERIFY, dry, dry_doc, dry_new, ref
from co.e2e.testdata import MATERIAL_OUT_RD, PRODUCT_IN_RD

_GONE = (("deleted", "true"),)
_TRANSFER_DATA = ("stock.transfer_out_rd", "stock.transfer_in_rd")


def _made(kind: str) -> tuple:
    return (("type", kind), ("code", "$nonempty"))


def _create(builder: str, kind: str, key: str) -> tuple[Step, Step]:
    return Step("create", sql_name=builder, checks=_made(kind)), keep_step(key)


def _verify(kind: str, key: str, action: str) -> Step:
    return call_from(VERIFY, ref(kind, ("action", action)), "bag:" + key)


def _drop(kind: str, key: str) -> tuple[Step, Step]:
    return (
        call_from(DELETE, ref(kind), "bag:" + key, _GONE),
        call_from(LOAD, ref(kind), "bag:" + key, (), (404, "not_found")),
    )


def _full(kind: str, key: str, update: str) -> tuple:
    # 真建之后（调用方先插入修改要用的 stash）：修改、审核 → 真审核 → 弃审 → 真弃审 → 删除 → 真删除。
    src = "bag:" + key
    return (
        dry_doc(UPDATE, kind, src, builder=update),
        dry_doc(VERIFY, kind, src, ("action", "verify")),
        _verify(kind, key, "verify"),
        dry_doc(VERIFY, kind, src, ("action", "unverify")),
        _verify(kind, key, "unverify"),
        dry_doc(DELETE, kind, src),
    ) + _drop(kind, key)


def _other_in() -> CaseSpec:
    key = "dry_oi"
    src = "bag:" + key
    steps = (dry_new(CREATE, "other_in", "other_in"),) + _create("other_in", "other_in", key)
    steps += (stash_step("oi_line", "oi_line", src), stash_step("oi_qty", "oi_qty", src))
    steps += _full("other_in", key, "other_in_edit")
    title = "预演：其他入库新建、修改、审核、弃审、删除，都回滚、单据和现存量不变"
    return CaseSpec("dry-other-in", title, ("other_in_master",), steps, True)


def _other_out() -> CaseSpec:
    steps = (dry_new(CREATE, "other_out", "other_out"),)
    return CaseSpec("dry-other-out", "预演：其他出库新建，回滚后没有新单据", ("other_out_master",), steps, True)


def _transfer() -> CaseSpec:
    key = "dry_tv"
    src = "bag:" + key
    steps = (dry_new(CREATE, "transfer", "transfer"),) + _create("transfer", "transfer", key)
    steps += (
        stash_step("tv_line", "tv_line", src),
        dry_doc(UPDATE, "transfer", src, builder="transfer_qty"),
        dry_doc(VERIFY, "transfer", src, ("action", "verify")),
        pred_step("tv_kids", "eq:0", src),
        dry_doc(DELETE, "transfer", src),
    ) + _drop("transfer", key)
    title = "预演：调拨单新建、修改、审核（不留下其他出入库）、删除，都回滚"
    return CaseSpec("dry-transfer", title, ("transfer_master",), steps, True, data=_TRANSFER_DATA)


def _stock_misc() -> list[CaseSpec]:
    key = "dry_tr"
    src = "bag:" + key
    request = (dry_new(CREATE, "transfer_request", "sm_tr_new"),) + _create("sm_tr_new", "transfer_request", key)
    request += (
        stash_step("sm_tr_line", "sm_tr_line", src),
        dry_doc(UPDATE, "transfer_request", src, builder="sm_tr_upd"),
        dry_doc(VERIFY, "transfer_request", src, ("action", "verify")),
        dry_doc(DELETE, "transfer_request", src),
    ) + _drop("transfer_request", key)
    shape = (dry_new(CREATE, "shape_change", "sm_av_new"),)
    check = (dry_new(CREATE, "stock_check", "sm_cv_new"),)
    return [
        CaseSpec("dry-shape-change", "预演：形态转换单新建，回滚后没有新单据",
                 ("sm_av", "sm_av_from", "sm_av_to", "sm_av_dep"), shape, True, optional=True),
        CaseSpec("dry-transfer-request", "预演：调拨申请单新建、修改、审核、删除，都回滚",
                 ("sm_tr", "sm_tr_in", "sm_tr_inv"), request, True, optional=True),
        CaseSpec("dry-stock-check", "预演：盘点单新建，回滚后没有新单据（同仓库之后仍能建）",
                 ("sm_cv", "sm_cv_inv"), check, True, optional=True),
    ]


def _mfg() -> list[CaseSpec]:
    out = (
        stash_step("mo_iss", "mo_iss", "need"),
        dry_new(GEN, "material_out", "material_out_gen", "need", watch="production_order:$id"),
        pred_step("mo_iss", "same:mo_iss", "need"),
    )
    pin = (
        stash_step("pin_qin", "pin_qin", "need"),
        stash_step("pin_fsum", "pin_fsum", "need"),
        dry_new(GEN, "product_in", "product_in_gen", "need", watch="qm_product_check:$id"),
        pred_step("pin_qin", "same:pin_qin", "need"),
        pred_step("pin_fsum", "same:pin_fsum", "need"),
    )
    return [
        CaseSpec("dry-material-out", "预演：生产订单生材料出库，回滚后子件已领量不变",
                 ("mo_alloc", "mo_alloc_line", "mo_alloc_wh"), out, True, data=(MATERIAL_OUT_RD,)),
        CaseSpec("dry-product-in", "预演：产品检验单生产成品入库，回滚后合格入库量、累计入库不变",
                 ("qm_pin", "qm_pin_wh", "qm_pin_qty"), pin, True, data=(PRODUCT_IN_RD,)),
    ]


def _arap() -> list[CaseSpec]:
    key = "dry_ar"
    src = "bag:" + key
    receipt = (ctl_step("AR"), dry_new(CREATE, "ar_receipt", "ar_receipt")) + _create("edit_ar_receipt_new", "ar_receipt", key)
    receipt += (stash_step("ar_receipt_edit_line", "ar_receipt_edit_line", src),)
    receipt += _full("ar_receipt", key, "edit_ar_receipt_upd")
    receipt += (dry_new(CREATE, "ar_bill", "ar_bill"),)
    payment = (ctl_step("AP"), dry_new(CREATE, "ap_payment", "ap_payment"), dry_new(CREATE, "ap_bill", "ap_bill"))
    return [
        CaseSpec("dry-ar-receipt", "预演：收款单新建、修改、审核、弃审、删除，应收单新建，都回滚",
                 ("ar_master",), receipt, True),
        CaseSpec("dry-ap-payment", "预演：付款单、应付单新建，回滚后没有新单据", ("ap_master",), payment, True),
    ]


def _mo_close() -> CaseSpec:
    steps = (
        stash_step("mo_others", "mo_others", "need"),
        pred_step("mo_open", "eq:1", "need"),
        dry(CLOSE, (("type", "production_order"),), "need", builder="mo_close_line", expect="exists",
            watch="production_order:$id"),
        pred_step("mo_open", "eq:1", "need"),
        pred_step("mo_others", "same:mo_others", "need"),
    )
    title = "预演：生产订单按行关闭，回滚后行仍打开、其他行不变"
    return CaseSpec("dry-mo-close", title, ("mo_src", "mo_line"), steps, True, optional=True)


def _qm_product() -> CaseSpec:
    key = "dry_q2"
    src = "bag:" + key
    inspect = (("type", "qm_product_inspect"), ("code", "$nonempty"))
    # 先读产品报检 / 检验单模板必输的自定义项（qm_tpl），生单正文 qmp_qm02 / qmp_qm04 合进表头。
    steps = TPL76 + (
        stash_step("qmp_decl", "qmp_decl", "need"),
        dry_new(GEN, "qm_product_inspect", "qmp_qm02", "need", watch="production_order:$id"),
        pred_step("qmp_decl", "same:qmp_decl", "need"),
        apply_step(GEN, "qmp_qm02", "need", inspect),
        keep_step(key),
        stash_step("qmp_q2l", "qmp_q2l", src),
        dry(GEN, (("type", "qm_product_check"),), src, builder="qmp_qm04", mode="validate",
            watch="qm_product_inspect:$id"),
        pred_step("qmp_q2_sum", "eq:0.0000|0", src),
        dry(DELETE, ref("qm_product_inspect"), src, mode="validate", watch="qm_product_inspect:$id"),
        call_from(DELETE, ref("qm_product_inspect"), src, _GONE),
        pred_step("qmp_q2_gone", "eq:0", src),
        pred_step("qmp_decl", "same:qmp_decl", "need"),
    )
    title = "预演：生产订单生产品报检单（回滚）、报检单生产品检验单和删除报检单（只校验），都不留单据"
    needs = ("qmp_mo", "qmp_mod", "qmp_proj", "qmp_psn", "qmp_d11")
    tags = frozenset(("src:production_order:qmp_mo", "stock"))
    return CaseSpec("dry-qm-product", title, needs, steps, True, optional=True, tags=tags)


STOCK_PLANS = {
    "dry-other-in": (("dry_oi", "other_in", True),),
    "dry-transfer": (("dry_tv", "transfer", True),),
    "dry-transfer-request": (("dry_tr", "transfer_request", True),),
    "dry-ar-receipt": (("dry_ar", "ar_receipt", True),),
    "dry-qm-product": (("dry_q2", "qm_product_inspect", False),),
}


def dry_stock_cases() -> list[CaseSpec]:
    rows = [_other_in(), _other_out(), _transfer()] + _stock_misc() + _mfg() + _arap()
    return rows + [_mo_close(), _qm_product()]
