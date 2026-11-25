"""写预演补缺：生产订单行打开、来料报检单生单、质量单据弃审（validate）、以及六类库存 / 退货单据的审核、弃审、删除预演。

写法同 dry_sale / dry_stock：每步预演核对 200、dry_run、mode、warnings、docs 和「什么都没变」（dry_ops.dry_step），
单据按原用例的挑单、正文构造器真建，最后真删并回读来源累计数；中途失败时 dry_cleanup 先撤回预演漏出去的（dry_leaks），
再按 GAP_PLANS 弃审、删除本用例真建的单据（生产订单行由 _mo_reopen 重新打开）。

回归原本就真审核过的类型（其他出库、销售退货单、采购退货单）走「审核预演 → 真审核 → 弃审预演 → 真弃审 → 删除预演 → 真删除」；
原本只生单、删除不审核的类型（销售出库、材料出库、产成品入库）只预演审核和删除，不真审核（回归不在它们上面动现存量审核）。

dry-mo-open：生产订单行关闭预演 → 真关闭 → 打开预演（行仍关闭）→ 真打开，其他行不变（可选）。
dry-qm-incoming：采购订单真生到货单并审核 → 来料报检单生单预演（rollback，到货行已报检数不变）→ 真生报检单（U8 自动审核）
  → 报检单单独弃审带 dry_run 仍 400（报检单不开放单独弃审）→ 删除报检单预演（validate：先弃审再删，都在 U8 组件里，停在它之前）
  → 真删除 → 到货单弃审、删除（可选）。
dry-sale-out-cycle / dry-other-out-cycle / dry-material-out-cycle / dry-product-in-cycle / dry-sale-return-cycle /
dry-purchase-return-cycle：见上。
"""

from __future__ import annotations

from co.e2e.casespec.returns_misc_bodies import SR_QTY
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_from, keep_step, pred_step, stash_step
from co.e2e.casespec.qm_tpl import tpl_step
from co.e2e.casespec.dry_steps import CLOSE, DELETE, GEN, LOAD, VERIFY, dry, dry_doc, dry_new, ref, refuse
from co.e2e.testdata import MATERIAL_OUT_RD, PRODUCT_IN_RD

_GONE = (("deleted", "true"),)
_NEEDS_QMI = ("qmi_po", "qmi_pol", "qmi_wh", "qmi_proj", "qmi_psn", "qmi_d11")


def _made(kind: str) -> tuple:
    return (("type", kind), ("code", "$nonempty"))


def _real(kind: str, key: str, action: str) -> Step:
    return call_from(VERIFY, ref(kind, ("action", action)), "bag:" + key)


def _drop(kind: str, key: str) -> tuple[Step, Step]:
    return (
        call_from(DELETE, ref(kind), "bag:" + key, _GONE),
        call_from(LOAD, ref(kind), "bag:" + key, (), (404, "not_found")),
    )


def _verified_cycle(kind: str, key: str) -> tuple:
    # 审核预演 → 真审核 → 弃审预演 → 真弃审 → 删除预演 → 真删除。
    src = "bag:" + key
    return (
        dry_doc(VERIFY, kind, src, ("action", "verify")),
        _real(kind, key, "verify"),
        dry_doc(VERIFY, kind, src, ("action", "unverify")),
        _real(kind, key, "unverify"),
        dry_doc(DELETE, kind, src),
    ) + _drop(kind, key)


def _plain_cycle(kind: str, key: str) -> tuple:
    # 只预演审核和删除（不真审核），再真删除。
    src = "bag:" + key
    return (dry_doc(VERIFY, kind, src, ("action", "verify")), dry_doc(DELETE, kind, src)) + _drop(kind, key)


def _mo_open() -> CaseSpec:
    watch = "production_order:$id"
    steps = (
        stash_step("mo_others", "mo_others", "need"),
        pred_step("mo_open", "eq:1", "need"),
        dry(CLOSE, (("type", "production_order"),), "need", builder="mo_close_line", expect="exists", watch=watch),
        apply_step(CLOSE, "mo_close_line", "need", (("action", "close"), ("changed", "1"))),
        keep_step("dry_mo_closed", "need"),
        pred_step("mo_closed", "eq:1", "need"),
        dry(CLOSE, (("type", "production_order"),), "need", builder="mo_open_line", expect="exists", watch=watch),
        pred_step("mo_closed", "eq:1", "need"),
        apply_step(CLOSE, "mo_open_line", "need", (("action", "open"), ("changed", "1"))),
        pred_step("mo_open", "eq:1", "need"),
        pred_step("mo_others", "same:mo_others", "need"),
    )
    title = "预演：生产订单行关闭、打开都回滚（打开预演之后行仍关闭），中间真关闭、真打开，其他行不变"
    return CaseSpec("dry-mo-open", title, ("mo_src", "mo_line"), steps, True, optional=True)


def _qm_incoming() -> CaseSpec:
    arr, q1 = "dry_qarr", "dry_q1"
    arr_src, q1_src = "bag:" + arr, "bag:" + q1
    inspect = _made("qm_incoming_inspect") + (("state.verified", "true"),)
    steps = (
        # 来料报检单模板必输的自定义项（qm_tpl，VT 351）：生单正文 qmi_qm01 合进表头。
        tpl_step(351, "qmi_t1"),
        apply_step(GEN, "qmi_arrival", "need", _made("arrival")),
        keep_step(arr),
        _real("arrival", arr, "verify"),
        stash_step("qmi_arrl", "qmi_arrl", arr_src),
        pred_step("qmi_arr_insp", "eq:0.0000|0", arr_src),
        dry_new(GEN, "qm_incoming_inspect", "qmi_qm01", arr_src, watch="arrival:$id"),
        pred_step("qmi_arr_insp", "eq:0.0000|0", arr_src),
        apply_step(GEN, "qmi_qm01", arr_src, inspect),
        keep_step(q1),
        refuse(VERIFY, ref("qm_incoming_inspect", ("action", "unverify")), dry="1", id_from=q1_src),
        dry(DELETE, ref("qm_incoming_inspect"), q1_src, mode="validate", watch="qm_incoming_inspect:$id"),
        call_from(DELETE, ref("qm_incoming_inspect"), q1_src, _GONE + (("unverified", "true"),)),
        pred_step("qmi_q1_gone", "eq:0", q1_src),
        pred_step("qmi_arr_insp", "eq:0.0000|0", arr_src),
        _real("arrival", arr, "unverify"),
    ) + _drop("arrival", arr)
    title = (
        "预演：到货单生来料报检单回滚（到货行已报检数不变）；报检单单独弃审带 dry_run 仍 400；"
        "删除已审核的报检单只校验（先弃审再删都停在 U8 组件之前）"
    )
    tags = frozenset(("src:purchase_order:qmi_po", "stock"))
    return CaseSpec("dry-qm-incoming", title, _NEEDS_QMI, steps, True, optional=True, tags=tags)


def _sale_out() -> CaseSpec:
    disp, out = "dry_g_disp", "dry_g_out"
    steps = (
        stash_step("so_fh", "so_fh", "need"),
        apply_step(GEN, "dispatch_gen", "need", _made("dispatch")),
        keep_step(disp),
        _real("dispatch", disp, "verify"),
        apply_step(GEN, "sale_out_gen", "bag:" + disp, _made("sale_out")),
        keep_step(out),
    ) + _plain_cycle("sale_out", out) + (_real("dispatch", disp, "unverify"),) + _drop("dispatch", disp)
    steps += (pred_step("so_fh", "same:so_fh", "need"),)
    title = "预演：销售出库单审核、删除都回滚；之后真删出库单、弃审并删除发货单，订单累计发货复原"
    return CaseSpec("dry-sale-out-cycle", title, ("so_ship", "so_ship_line", "so_ship_wh"), steps, True)


def _other_out() -> CaseSpec:
    key = "dry_g_oo"
    steps = (Step("create", sql_name="other_out", checks=_made("other_out")), keep_step(key))
    steps += _verified_cycle("other_out", key)
    title = "预演：其他出库单审核、弃审、删除都回滚，中间真审核、真弃审，最后真删除"
    return CaseSpec("dry-other-out-cycle", title, ("other_out_master",), steps, True)


def _material_out() -> CaseSpec:
    key = "dry_g_mo"
    steps = (
        stash_step("mo_iss", "mo_iss", "need"),
        apply_step(GEN, "material_out_gen", "need", _made("material_out")),
        keep_step(key),
    ) + _plain_cycle("material_out", key) + (pred_step("mo_iss", "same:mo_iss", "need"),)
    title = "预演：材料出库单审核、删除都回滚；真删除后子件已领量复原"
    needs = ("mo_alloc", "mo_alloc_line", "mo_alloc_wh")
    return CaseSpec("dry-material-out-cycle", title, needs, steps, True, data=(MATERIAL_OUT_RD,))


def _product_in() -> CaseSpec:
    key = "dry_g_pi"
    steps = (
        stash_step("pin_qin", "pin_qin", "need"),
        stash_step("pin_fsum", "pin_fsum", "need"),
        apply_step(GEN, "product_in_gen", "need", _made("product_in")),
        keep_step(key),
    ) + _plain_cycle("product_in", key)
    steps += (pred_step("pin_qin", "same:pin_qin", "need"), pred_step("pin_fsum", "same:pin_fsum", "need"))
    title = "预演：产成品入库单审核、删除都回滚；真删除后合格入库量、累计入库复原"
    needs = ("qm_pin", "qm_pin_wh", "qm_pin_qty")
    return CaseSpec("dry-product-in-cycle", title, needs, steps, True, data=(PRODUCT_IN_RD,))


def _sale_return() -> CaseSpec:
    key = "dry_g_sr"
    names = ("sr_ret", "sr_wkp", "sr_soret", "sr_sofh")
    room = Step("custom", fields=(("read", "sr_room"), ("min", str(SR_QTY))), sql_name="room", id_from="need")
    steps = (room,) + tuple(stash_step(name, name, "need") for name in names)
    steps += (apply_step(GEN, "sale_return_gen", "need", _made("sale_return")), keep_step(key))
    steps += _verified_cycle("sale_return", key)
    steps += tuple(pred_step(name, "same:" + name, "need") for name in names)
    title = "预演：销售退货单审核、弃审、删除都回滚，中间真审核、真弃审；真删除后发货行、订单行回写复原"
    return CaseSpec("dry-sale-return-cycle", title, ("sr_src", "sr_line"), steps, True)


def _purchase_return() -> CaseSpec:
    key = "dry_g_pr"
    names = ("pr_ret", "pr_poret", "pr_parr")
    steps = tuple(stash_step(name, name, "need") for name in names)
    steps += (apply_step(GEN, "purchase_return_gen", "need", _made("purchase_return")), keep_step(key))
    steps += _verified_cycle("purchase_return", key)
    steps += tuple(pred_step(name, "same:" + name, "need") for name in names)
    title = "预演：采购退货单审核、弃审、删除都回滚，中间真审核、真弃审；真删除后到货行、订单行回写复原"
    return CaseSpec("dry-purchase-return-cycle", title, ("pr_src", "pr_line"), steps, True)


# 用例名 → 补偿计划（bag 键, 单据类型, 是否先弃审），下游在前；dry_cleanup 用。dry-mo-open 另由 dry_cleanup._mo_reopen 处理。
GAP_PLANS = {
    "dry-qm-incoming": (("dry_q1", "qm_incoming_inspect", False), ("dry_qarr", "arrival", True)),
    "dry-sale-out-cycle": (("dry_g_out", "sale_out", True), ("dry_g_disp", "dispatch", True)),
    "dry-other-out-cycle": (("dry_g_oo", "other_out", True),),
    "dry-material-out-cycle": (("dry_g_mo", "material_out", True),),
    "dry-product-in-cycle": (("dry_g_pi", "product_in", True),),
    "dry-sale-return-cycle": (("dry_g_sr", "sale_return", True),),
    "dry-purchase-return-cycle": (("dry_g_pr", "purchase_return", True),),
}


def dry_gap_cases() -> list[CaseSpec]:
    return [
        _mo_open(), _qm_incoming(), _sale_out(), _other_out(), _material_out(), _product_in(),
        _sale_return(), _purchase_return(),
    ]
