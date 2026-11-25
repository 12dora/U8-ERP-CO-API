"""应收应付单据、材料出库和产成品入库生单、生产订单审核、列表和并发的用例。

新建或生单的用例都在自己的步骤里删掉新单据，并回读确认来源数量已回退；快照还原只是兜底。
"""

from __future__ import annotations

from co.e2e.casespec.arap_ctl import ctl_step_for
from co.e2e.casespec.arap_gen_bodies import AMOUNT_TEXT
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call, call_made, pred_step, stash_step
from co.e2e.casespec.dry_steps import GEN, refuse
from co.e2e.testdata import MATERIAL_OUT_RD, PRODUCT_IN_RD

# 并发用例读取的单据类型（bag 键 load_<type>），再加 3 张其他入库新建，一共 12 个请求同时发出。
BURST_LOADS = (
    "sale_order",
    "dispatch",
    "purchase_order",
    "arrival",
    "purchase_in",
    "other_out",
    "transfer",
    "production_order",
    "qm_product_check",
)
_ARAP = (
    ("ar_receipt", "ar-receipt-cycle", "收款单", "ar_master"),
    ("ap_payment", "ap-payment-cycle", "付款单", "ap_master"),
    ("ar_bill", "ar-bill-cycle", "应收单", "ar_master"),
    ("ap_bill", "ap-bill-cycle", "应付单", "ap_master"),
)


def core(op: str, fields: tuple = (), id_from: str = "none") -> Step:
    return Step("core", fields=fields, sql_name=op, id_from=id_from)


def _arap_cycle(kind: str, name: str, title: str, need: str) -> CaseSpec:
    ref = (("type", kind), ("id", "$id"))
    on = ref + (("action", "verify"),)
    off = ref + (("action", "unverify"),)
    steps = (
        ctl_step_for(need),
        Step("create", sql_name=kind, checks=(("type", kind), ("code", "$nonempty"))),
        core("load_same", (("type", kind),), "made"),
        pred_step(kind + "_amt", "eq:" + AMOUNT_TEXT),
        call_made("/v1/vouchers/verify", on, (("state.verified", "true"),)),
        pred_step(kind + "_chk", "opname"),
        pred_step(kind + "_det", "gt0"),
        call_made("/v1/vouchers/delete", ref, (), 409, "state_mismatch"),
        call_made("/v1/vouchers/verify", off, (("state.verified", "false"),)),
        pred_step(kind + "_chk", "empty"),
        pred_step(kind + "_det", "eq:0"),
        call_made("/v1/vouchers/delete", ref, (("deleted", "true"),)),
        call_made("/v1/vouchers/load", ref, (), 404, "not_found"),
        pred_step(kind + "_gone", "eq:0"),
    )
    return CaseSpec(name, title + "新建、审核、弃审、删除", (need,), steps, True)


def _arap_refuse() -> CaseSpec:
    # 已制凭证的收款单：弃审和删除都 409，审核人和凭证号不变。
    ref = (("type", "ar_receipt"), ("id", "$id"))
    steps = (
        call("/v1/vouchers/verify", ref + (("action", "unverify"),), (), 409, "state_mismatch"),
        call("/v1/vouchers/delete", ref, (), 409, "state_mismatch"),
        pred_step("ar_receipt_chk", "nonempty", "need"),
        pred_step("ar_receipt_pz", "nonempty", "need"),
    )
    return CaseSpec("ar-receipt-voucher-refuse", "已制凭证的收款单不能弃审或删除", ("ar_voucher",), steps, True)


def _material_out() -> CaseSpec:
    ref = (("type", "material_out"), ("id", "$id"))
    steps = (
        stash_step("mo_iss", "mo_iss", "need"),
        apply_step("/v1/vouchers/generate", "material_out_gen", "need", (("type", "material_out"), ("code", "$nonempty"))),
        pred_step("mo_iss", "plus1:mo_iss", "need"),
        pred_step("mo_out_link", "eq:1"),
        call_made("/v1/vouchers/delete", ref, (("deleted", "true"),)),
        call_made("/v1/vouchers/load", ref, (), 404, "not_found"),
        pred_step("mo_iss", "same:mo_iss", "need"),
    )
    needs = ("mo_alloc", "mo_alloc_line", "mo_alloc_wh")
    title = "生产订单生成材料出库：已领量增加，删除后回退"
    return CaseSpec("material-out-generate", title, needs, steps, True, data=(MATERIAL_OUT_RD,))


def _material_out_no_rd() -> CaseSpec:
    # 不带收发类别：桥在进 U8 之前 400 field head.crdcode，子件已领量不变。挑不到生产订单记 N/A。
    steps = (
        stash_step("mo_iss", "mo_iss", "need"),
        refuse(GEN, (("type", "material_out"),), builder="material_out_nord", field="head.crdcode", id_from="need"),
        pred_step("mo_iss", "same:mo_iss", "need"),
    )
    needs = ("mo_alloc", "mo_alloc_line", "mo_alloc_wh")
    title = "生产订单生成材料出库不带收发类别被拒绝（400 head.crdcode）"
    tags = frozenset(("probe",))
    return CaseSpec("material-out-no-rd", title, needs, steps, True, optional=True, tags=tags, routes=(GEN,))


def _product_in() -> CaseSpec:
    ref = (("type", "product_in"), ("id", "$id"))
    steps = (
        stash_step("pin_qin", "pin_qin", "need"),
        stash_step("pin_fsum", "pin_fsum", "need"),
        apply_step("/v1/vouchers/generate", "product_in_gen", "need", (("type", "product_in"), ("code", "$nonempty"))),
        pred_step("pin_qin", "added:pin_qin+qm_pin_qty", "need"),
        pred_step("pin_fsum", "added:pin_fsum+qm_pin_qty", "need"),
        pred_step("pin_link", "eq:1"),
        call_made("/v1/vouchers/delete", ref, (("deleted", "true"),)),
        call_made("/v1/vouchers/load", ref, (), 404, "not_found"),
        pred_step("pin_qin", "same:pin_qin", "need"),
        pred_step("pin_fsum", "same:pin_fsum", "need"),
    )
    needs = ("qm_pin", "qm_pin_wh", "qm_pin_qty")
    title = "产品检验单生成产成品入库：合格入库量增加，删除后回退"
    return CaseSpec("product-in-generate", title, needs, steps, True, data=(PRODUCT_IN_RD,))


def _mo_cases() -> list[CaseSpec]:
    ref = (("type", "production_order"), ("id", "$id"))
    reverse = (
        call("/v1/vouchers/verify", ref + (("action", "unverify"),), (("state.verified", "false"),)),
        pred_step("mo_released", "eq:0", "need"),
        call("/v1/vouchers/verify", ref + (("action", "verify"),), (("state.verified", "true"),)),
        pred_step("mo_unreleased", "eq:0", "need"),
    )
    again = (
        call("/v1/vouchers/verify", ref + (("action", "verify"),), (), 409, "state_mismatch"),
        pred_step("mo_unreleased", "eq:0", "need"),
    )
    return [
        CaseSpec("mo-unverify-verify", "生产订单弃审后重新审核", ("mo_free",), reverse, True),
        CaseSpec("mo-verify-audited", "已审核生产订单再审核被拒绝", ("mo_audited",), again, True),
    ]


def _purchase_invoice() -> CaseSpec:
    # 参照采购入库开 1 个：入库行累计开票数量和采购订单行累计开票数量 +1；删除后回到原值（NULL 与 0 视为相同）。
    ref = (("type", "purchase_invoice"), ("id", "$id"))
    gen = "/v1/vouchers/generate"
    made = (("type", "purchase_invoice"), ("code", "$nonempty"))
    steps = (
        stash_step("pinv_billed", "pinv_billed", "need"),
        stash_step("pinv_po", "pinv_po", "need"),
        apply_step(gen, "purchase_invoice_badsrc", "need", (), (400, "bad_request")),
        apply_step(gen, "purchase_invoice_nocode", "need", (), (400, "bad_request")),
        apply_step(gen, "purchase_invoice_gen", "need", made),
        pred_step("pinv_billed", "plus1:pinv_billed", "need"),
        pred_step("pinv_po", "plus1:pinv_po", "need"),
        core("load_code", (("type", "purchase_invoice"), ("key", "pinv_code")), "made"),
        apply_step(gen, "purchase_invoice_dup", "need", (), (409, "state_mismatch")),
        call_made("/v1/vouchers/delete", ref, (("deleted", "true"),)),
        call_made("/v1/vouchers/load", ref, (), 404, "not_found"),
        pred_step("pinv_billed", "same:pinv_billed", "need"),
        pred_step("pinv_po", "same:pinv_po", "need"),
    )
    title = "采购入库生成采购发票：累计开票数增加，删除后回退；错来源、缺发票号 400，重号 409"
    return CaseSpec("purchase-invoice-generate", title, ("pinv_src", "pinv_line"), steps, True)


def _purchase_in_qm() -> CaseSpec:
    # 参照来料检验单入库 1 个：检验单累计入库、到货行合格入库、采购订单行累计到货 +1；删除后回到原值。
    ref = (("type", "purchase_in"), ("id", "$id"))
    made = (("type", "purchase_in"), ("code", "$nonempty"))
    names = ("qmin_fsum", "qmin_valid", "qmin_po")
    steps = tuple(stash_step(name, name, "need") for name in names)
    steps += (apply_step("/v1/vouchers/generate", "purchase_in_qm_gen", "need", made),)
    steps += tuple(pred_step(name, "plus1:" + name, "need") for name in names)
    steps += (
        call_made("/v1/vouchers/delete", ref, (("deleted", "true"),)),
        call_made("/v1/vouchers/load", ref, (), 404, "not_found"),
    )
    steps += tuple(pred_step(name, "same:" + name, "need") for name in names)
    title = "来料检验单生成采购入库：检验单、到货单、采购订单累计数增加，删除后回退"
    return CaseSpec("purchase-in-from-qm", title, ("qmin_src", "qmin_wh"), steps, True)


def _arrival() -> CaseSpec:
    # 采购订单生成到货单 1 个：超出剩余先 409；订单行 iArrQTY、fPoArrQuantity 各 +1；删除后回到原值。
    ref = (("type", "arrival"), ("id", "$id"))
    gen = "/v1/vouchers/generate"
    names = ("arr_qty", "arr_fpo")
    steps = tuple(stash_step(name, name, "need") for name in names)
    steps += (
        apply_step(gen, "arrival_over", "need", (), (409, "state_mismatch")),
        apply_step(gen, "arrival_gen", "need", (("type", "arrival"), ("code", "$nonempty"))),
    )
    steps += tuple(pred_step(name, "plus1:" + name, "need") for name in names)
    # 新到货行 bInspect（已报检）为 0，报检单才能参照。
    steps += (pred_step("arr_binsp", "eq:0", "made"),)
    steps += (
        core("load_code", (("type", "arrival"), ("key", "$last")), "made"),
        call_made("/v1/vouchers/delete", ref, (("deleted", "true"),)),
        call_made("/v1/vouchers/load", ref, (), 404, "not_found"),
    )
    steps += tuple(pred_step(name, "same:" + name, "need") for name in names)
    needs = ("arr_src", "arr_line", "arr_wh", "arr_over")
    return CaseSpec("arrival-generate", "采购订单生成到货单：累计到货增加，删除后回退；超出剩余 409", needs, steps, True)


def _list_cases() -> list[CaseSpec]:
    sale = (("type", "sale_order"), ("pick", "so_list"))
    ar = (("type", "ar_receipt"), ("pick", "ar_list"))
    other = (("type", "other_in"),)
    since = (
        core("list_mark", other),
        Step("create", sql_name="other_in", checks=(("code", "$nonempty"),)),
        core("list_since", other),
        call_made("/v1/vouchers/delete", (("type", "other_in"), ("id", "$id")), (("deleted", "true"),)),
    )
    listed = ("/v1/vouchers/list",)
    return [
        CaseSpec("list-sale-order", "销售订单列表按客户和日期筛选、只取键", ("so_list",),
                 (core("list_filter", sale), core("list_keys", sale)), True, routes=listed),
        CaseSpec("list-ar-receipt", "收款单列表按客户和日期筛选、翻页", ("ar_list",),
                 (core("list_filter", ar), core("list_page", (("type", "ar_receipt"),))), True, routes=listed),
        CaseSpec("list-changed-since", "按变更水位只返回之后改过的单据", ("other_in_master",), since, True,
                 routes=listed + ("/v1/vouchers/create",)),
        CaseSpec("stock-current-filter", "现存量按仓库和存货筛选", ("stock_pair",),
                 (core("stock_filter", (("pick", "stock_pair"),)),), True, routes=("/v1/stock/current",)),
    ]


def _burst() -> CaseSpec:
    needs = ("other_in_master",) + tuple("load_" + kind for kind in BURST_LOADS)
    # 自己就发 12 个并发请求，能占满桥的写池：独占，跑的时候不和任何用例同时跑。
    tags = frozenset(("burst", "new:other_in", "w:other_in", "stock"))
    routes = ("/v1/vouchers/load", "/v1/vouchers/create", "/v1/vouchers/delete")
    title = "12 个请求同时读取和新建，再并发删除"
    return CaseSpec("bridge-concurrency", title, needs, (core("burst"),), True, tags=tags, routes=routes, exclusive=True)


def arap_gen_cases() -> list[CaseSpec]:
    rows = [_arap_cycle(*item) for item in _ARAP]
    rows += [_arap_refuse(), _material_out(), _material_out_no_rd(), _product_in(), _purchase_invoice(), _purchase_in_qm()]
    rows.append(_arrival())
    return rows + _mo_cases() + _list_cases() + [_burst()]
