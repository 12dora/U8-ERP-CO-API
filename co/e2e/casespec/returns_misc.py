"""写用例：采购发票复核、采购退货、销售退货、请购单、不良品处理单入库、无来源采购入库的选项拒绝。

新建或生单的用例在自己的步骤里删掉新单据，并回读确认来源累计数回到原值；快照还原只是兜底。
不良品处理单入库例外：满额入库时 U8 可能自动关闭生产订单行，删除按文档 409，这时留给快照还原。
"""

from __future__ import annotations

from co.e2e.casespec.returns_misc_bodies import MEMO, MEMO_UPD, REQ_DATA, SR_QTY
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_made, pred_step, stash_step
from co.e2e.casespec.gl_idem import idem_case
from co.e2e.casespec.readonly import read_cases
from co.e2e.testdata import PRODUCT_IN_RD

_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_ON = (("verified_by", "$opname"),)
_OFF = (("verified_by", ""),)


def _ref(kind: str) -> tuple:
    return (("type", kind), ("id", "$id"))


def _verify_pair(kind: str) -> tuple[Step, Step]:
    ref = _ref(kind)
    return (
        call_made(_VERIFY, ref + (("action", "verify"),), _ON),
        call_made(_VERIFY, ref + (("action", "unverify"),), _OFF),
    )


def _drop(kind: str) -> tuple[Step, ...]:
    ref = _ref(kind)
    return (
        call_made("/v1/vouchers/delete", ref, (("deleted", "true"),)),
        call_made("/v1/vouchers/load", ref, (), 404, "not_found"),
    )


def _refuse(route: str, builder: str, text: str, id_from: str, expect: tuple[int, str]) -> Step:
    fields = (("builder", builder), ("text", text))
    return Step("custom", route, fields, expect[0], expect[1], (), "refuse", id_from)


def _pinv_review() -> CaseSpec:
    # 参照采购入库生成发票 → 复核（写 cVerifier）→ 取消复核（清空）→ 删除；累计开票数回到原值。
    on, off = _verify_pair("purchase_invoice")
    load = _ref("purchase_invoice")
    steps = (
        stash_step("pinv_billed", "pinv_billed", "need"),
        apply_step(_GEN, "purchase_invoice_gen", "need", (("type", "purchase_invoice"), ("code", "$nonempty"))),
        on,
        pred_step("pinv_rev", "opname"),
        call_made("/v1/vouchers/load", load, (("state.reviewed", "true"),)),
        off,
        pred_step("pinv_rev", "empty"),
    ) + _drop("purchase_invoice") + (pred_step("pinv_billed", "same:pinv_billed", "need"),)
    title = "采购发票复核、取消复核后删除，累计开票数回退"
    return CaseSpec("purchase-invoice-review", title, ("pinv_src", "pinv_line"), steps, True)


def _cmp(got: str, pred: str) -> Step:
    return Step("custom", fields=(("got", got), ("pred", pred)), sql_name="bag_compare", id_from="none")


def _purchase_return() -> CaseSpec:
    # 参照蓝字到货行退 1 个：原行累计退货、订单行累计退货 +1，订单行累计到货 −1；审核、弃审、删除后都回到原值。
    # 生单后的回读先记进 bag，删除之后再判，断言失败也不会把退货单留在库里。
    names = ("pr_ret", "pr_poret", "pr_parr")
    made = (("type", "purchase_return"), ("code", "$nonempty"))
    steps = tuple(stash_step(name, name, "need") for name in names)
    steps += (apply_step(_GEN, "purchase_return_gen", "need", made),)
    steps += tuple(stash_step(name, name + "_gen", "need") for name in names)
    steps += _verify_pair("purchase_return") + _drop("purchase_return")
    steps += (
        _cmp("pr_ret_gen", "plus1:pr_ret"),
        _cmp("pr_poret_gen", "plus1:pr_poret"),
        _cmp("pr_parr_gen", "shift:-1:pr_parr"),
    )
    steps += tuple(pred_step(name, "same:" + name, "need") for name in names)
    title = "到货单生成采购退货单：到货行、订单行回写，审核弃审后删除回退"
    return CaseSpec("purchase-return-cycle", title, ("pr_src", "pr_line"), steps, True)


def _sale_return() -> CaseSpec:
    # 参照蓝字发货行退 2 个（未开票）：原行 iRetQuantity、fretqtywkp +2，订单行 fretquantity +2、iFHQuantity −2；
    # 新单是红字、合计 −2，新行 iCorID 指回原行。生单前再核可退数量；回读先记进 bag，删除之后再判。
    names = ("sr_ret", "sr_wkp", "sr_soret", "sr_sofh")
    made = (("type", "sale_return"), ("code", "$nonempty"))
    room = Step("custom", fields=(("read", "sr_room"), ("min", str(SR_QTY))), sql_name="room", id_from="need")
    steps = (room,) + tuple(stash_step(name, name, "need") for name in names)
    steps += (apply_step(_GEN, "sale_return_gen", "need", made),)
    steps += tuple(stash_step(name, name + "_gen", "need") for name in names)
    steps += (stash_step("sr_cor", "sr_cor_gen"), stash_step("sr_neg", "sr_neg_gen"))
    steps += _verify_pair("sale_return") + _drop("sale_return")
    steps += (
        _cmp("sr_ret_gen", "shift:2:sr_ret"),
        _cmp("sr_wkp_gen", "shift:2:sr_wkp"),
        _cmp("sr_soret_gen", "shift:2:sr_soret"),
        _cmp("sr_sofh_gen", "shift:-2:sr_sofh"),
        _cmp("sr_cor_gen", "eq:1"),
        _cmp("sr_neg_gen", "eq:1"),
    )
    steps += tuple(pred_step(name, "same:" + name, "need") for name in names)
    title = "发货单生成销售退货单：发货行、订单行回写，审核弃审后删除回退"
    return CaseSpec("sale-return-cycle", title, ("sr_src", "sr_line"), steps, True)


def _requisition() -> CaseSpec:
    # 新建 → 读取 → 改备注 → 未审核关闭 409 → 审核 → 关闭 → 打开 → 弃审 → 删除。
    ref = _ref("purchase_requisition")
    close = "/v1/vouchers/close"
    on, off = _verify_pair("purchase_requisition")
    steps = (
        Step("create", sql_name="purchase_requisition_new", checks=(("type", "purchase_requisition"), ("code", "$nonempty"))),
        call_made("/v1/vouchers/load", ref, (("head.cmemo", MEMO),)),
        apply_step("/v1/vouchers/update", "purchase_requisition_upd", "made", (("ok", "true"),)),
        call_made("/v1/vouchers/load", ref, (("head.cmemo", MEMO_UPD),)),
        call_made(close, ref + (("action", "close"),), (), 409, "state_mismatch"),
        on,
        call_made(close, ref + (("action", "close"),), (("closed", "true"),)),
        call_made(close, ref + (("action", "open"),), (("closed", "false"),)),
        off,
    ) + _drop("purchase_requisition")
    title = "请购单新建、修改、审核、关闭、打开、弃审、删除"
    return CaseSpec("purchase-requisition-cycle", title, (), steps, True, data=REQ_DATA)


def _product_in_reject() -> CaseSpec:
    # 部分数量 409；全部处理后数量生单成功，入库行挂处理单行、处理单行标已入库。
    # 删除 200 时再核 404 和回退；409（U8 已自动关闭订单行）留给快照还原。
    part = _refuse(_GEN, "product_in_reject_part", "须一次入库全部", "need", (409, "state_mismatch"))
    steps = (
        part,
        apply_step(_GEN, "product_in_reject_gen", "need", (("type", "product_in"), ("code", "$nonempty"))),
        pred_step("qmrej_link", "eq:1"),
        pred_step("qmrej_flag", "eq:1", "need"),
        Step("custom", "/v1/vouchers/delete", sql_name="reject_drop", id_from="made"),
    )
    needs = ("qmrej", "qmrej_line", "qmrej_wh", "qmrej_qty", "qmrej_part")
    title = "产品不良品处理单生成产成品入库：部分数量 409，全部数量成功"
    routes = ("/v1/vouchers/generate", "/v1/vouchers/delete", "/v1/vouchers/load")
    return CaseSpec(
        "product-in-from-reject", title, needs, steps, True, optional=True, data=(PRODUCT_IN_RD,), routes=routes
    )


def _purchase_in_havepo() -> CaseSpec:
    # 账套打开「普通业务必有订单」时无来源采购入库 409；选项关闭时挑不到数据，记 N/A。
    step = _refuse("/v1/vouchers/create", "purchase_in_nosrc", "普通业务必有订单", "none", (409, "state_mismatch"))
    needs = ("pinsrc_wh", "pinsrc_ven", "pinsrc_dep", "pinsrc_inv")
    title = "普通业务必有订单时无来源采购入库被拒绝"
    # 自定义拒绝步骤看不出单据类型：按新建采购入库（动现存量）保守标记。
    tags = frozenset(("opt:havepo", "new:purchase_in", "w:purchase_in", "stock"))
    return CaseSpec(
        "purchase-in-nosrc-havepo", title, needs, (step,), True, optional=True, tags=tags, routes=("/v1/vouchers/create",)
    )


def write_cases() -> list[CaseSpec]:
    return [
        _pinv_review(),
        _purchase_return(),
        _sale_return(),
        _requisition(),
        _product_in_reject(),
        _purchase_in_havepo(),
        idem_case(),
    ]


def returns_misc_cases() -> list[CaseSpec]:
    return read_cases() + write_cases()
