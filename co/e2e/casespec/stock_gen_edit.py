"""写用例：修改生单来的库存单据（销售出库、采购入库参照订单 / 来料检验单、产成品入库、材料出库）。

每条都是：生单 2 个 → 读出新单的行 id → 数量改成 1、写表头备注和行备注 → 来源累计数少 1 → 删除 → 回到原值。
生单、修改后的回读先记进 bag，删除之后再判，断言失败也不会把新单据留在库里；快照还原只是兜底。
挑单、回读 SQL 在 stock_gen_edit_sql，正文在 stock_gen_edit_bodies。接线（catalog、picks、steps._make、
steps_sql 的回读）由 wiring.py 统一做：stock_edit_cases() 进用例登记，SE_PACKS 进挑单，SE_BUILDERS 进正文构造器，
has_stock_edit / stock_edit_sql 进 stash / pred 的回读，stock_edit_samples() 进样例 SQL。
"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_from, keep_step, pred_step, stash_step
from co.e2e.testdata import MATERIAL_OUT_RD, PRODUCT_IN_RD

_GEN = "/v1/vouchers/generate"
_UPD = "/v1/vouchers/update"
_VERIFY = "/v1/vouchers/verify"
_OK = (("ok", "true"),)


def _cmp(got: str, pred: str) -> Step:
    return Step("custom", fields=(("got", got), ("pred", pred)), sql_name="bag_compare", id_from="none")


def _drop(kind: str, bag_key: str) -> tuple[Step, ...]:
    ref = (("type", kind), ("id", "$id"))
    src = "bag:" + bag_key
    return (
        call_from("/v1/vouchers/delete", ref, src, (("deleted", "true"),)),
        call_from("/v1/vouchers/load", ref, src, (), (404, "not_found")),
    )


def _snap(names: tuple[str, ...], suffix: str, id_from: str) -> tuple[Step, ...]:
    return tuple(stash_step(name, name + suffix, id_from) for name in names)


def _judge(names: tuple[str, ...], memo_key: str) -> tuple[Step, ...]:
    # 生单后 +2，修改后 +1（比修改前少 1），删除后回到原值；备注写进了表头和行。
    steps = tuple(_cmp(name + "_gen", "shift:2:" + name) for name in names)
    steps += tuple(_cmp(name + "_upd", "plus1:" + name) for name in names)
    return steps + (_cmp(memo_key, "eq:1"),)


def _edit(kind: str, made: str, line: tuple[str, str], builder: str, memo: str) -> tuple[Step, ...]:
    # 生单之后：记下新单 id 和第一行 id，再改数量和备注。line 是（行 id 回读名, bag 键）。
    src = "bag:" + made
    return (
        keep_step(made),
        stash_step(line[0], line[1], src),
        apply_step(_UPD, builder, src, _OK),
        stash_step(memo, made + "_memo", src),
    )


def _cycle(spec: tuple[str, str, str], gen: Step, names: tuple[str, ...], line_read: str) -> tuple[Step, ...]:
    # spec：（类型, 新单 bag 键, 修改正文）。来源累计数的回读都以挑出的来源单据为 id（need）。
    kind, made, builder = spec
    memo = "se_memo" + line_read[-2:]
    steps = _snap(names, "", "need") + (gen,) + _snap(names, "_gen", "need")
    steps += _edit(kind, made, (line_read, made + "_line"), builder, memo)
    steps += _snap(names, "_upd", "need") + _drop(kind, made)
    steps += _judge(names, made + "_memo")
    return steps + tuple(pred_step(name, "same:" + name, "need") for name in names)


def _sale_out() -> CaseSpec:
    # 销售订单发货 2 个 → 审核发货单 → 生成销售出库 → 改成 1 个：发货单累计出库 2→1；
    # 删除出库单后回到 0，再弃审、删除发货单，订单行累计发货回到原值。
    disp = (("type", "dispatch"), ("id", "$id"))
    steps = (
        stash_step("se_sofh", "se_sofh", "need"),
        apply_step(_GEN, "se_disp_gen", "need", (("type", "dispatch"), ("code", "$nonempty"))),
        keep_step("se_disp"),
        call_from(_VERIFY, disp + (("action", "verify"),), "bag:se_disp"),
        stash_step("se_dout", "se_dout", "bag:se_disp"),
        apply_step(_GEN, "se_out_gen", "bag:se_disp", (("type", "sale_out"),)),
        stash_step("se_dout", "se_dout_gen", "bag:se_disp"),
    )
    steps += _edit("sale_out", "se_out", ("se_line32", "se_out_line"), "se_out_upd", "se_memo32")
    steps += (stash_step("se_dout", "se_dout_upd", "bag:se_disp"),) + _drop("sale_out", "se_out")
    steps += (
        stash_step("se_dout", "se_dout_del", "bag:se_disp"),
        call_from(_VERIFY, disp + (("action", "unverify"),), "bag:se_disp"),
    ) + _drop("dispatch", "se_disp")
    steps += _judge(("se_dout",), "se_out_memo") + (
        _cmp("se_dout_del", "same:se_dout"),
        pred_step("se_sofh", "same:se_sofh", "need"),
    )
    title = "修改销售出库单：数量减 1，发货单累计出库随之减 1，删除后回退"
    return CaseSpec("sale-out-update", title, ("se_so", "se_so_line", "se_so_wh"), steps, True)


def _purchase_in_po() -> CaseSpec:
    made = (("type", "purchase_in"), ("code", "$nonempty"))
    gen = apply_step(_GEN, "se_pin_po_gen", "need", made)
    steps = _cycle(("purchase_in", "se_pin", "se_pin_upd"), gen, ("se_po_got",), "se_line01")
    title = "修改参照采购订单的采购入库单：数量减 1，订单行累计入库随之减 1，删除后回退"
    return CaseSpec("purchase-in-po-update", title, ("se_po", "se_po_line", "se_po_wh"), steps, True)


def _purchase_in_qm() -> CaseSpec:
    made = (("type", "purchase_in"), ("code", "$nonempty"))
    gen = apply_step(_GEN, "se_pin_qm_gen", "need", made)
    names = ("se_qm_fsum", "se_qm_valid", "se_qm_po")
    steps = _cycle(("purchase_in", "se_pinq", "se_pinq_upd"), gen, names, "se_line01")
    title = "修改参照来料检验单的采购入库单：检验单、到货单、订单累计数随之减 1，删除后回退"
    return CaseSpec("purchase-in-qm-update", title, ("se_qm", "se_qm_wh"), steps, True)


def _product_in() -> CaseSpec:
    made = (("type", "product_in"), ("code", "$nonempty"))
    gen = apply_step(_GEN, "se_pq_gen", "need", made)
    steps = _cycle(("product_in", "se_pq_rd", "se_pq_upd"), gen, ("se_pq_qin", "se_pq_fsum"), "se_line10")
    title = "修改参照产品检验单的产成品入库单：合格入库量、检验单累计入库随之减 1，删除后回退"
    # 要有合格剩余 >= 2、生产订单剩余 >= 3 的产品检验单；有些账套的历史数据里常年没有，可造数的通道每次都有。
    # 挑不到记 N/A，不让整轮因此判失败。
    need = ("se_pq", "se_pq_wh", "se_pq_qty")
    return CaseSpec("product-in-update", title, need, steps, True, True, data=(PRODUCT_IN_RD,))


def _material_out() -> CaseSpec:
    # 另测两条拒绝：加数量 400、新增行 400（都在删除之前，不改单据）。
    made = (("type", "material_out"), ("code", "$nonempty"))
    gen = apply_step(_GEN, "se_mo_gen", "need", made)
    steps = _cycle(("material_out", "se_mo_rd", "se_mo_upd"), gen, ("se_mo_iss",), "se_line11")
    refuse = (
        apply_step(_UPD, "se_mo_up", "bag:se_mo_rd", (), (400, "bad_request")),
        apply_step(_UPD, "se_mo_add", "bag:se_mo_rd", (), (400, "bad_request")),
    )
    at = next(i for i, step in enumerate(steps) if step.sql_name == "se_mo_upd")
    steps = steps[:at] + refuse + steps[at:]
    title = "修改材料出库单：数量减 1，子件已领量随之减 1，删除后回退；加数量、新增行 400"
    needs = ("se_mo", "se_mo_line", "se_mo_wh")
    return CaseSpec("material-out-update", title, needs, steps, True, data=(MATERIAL_OUT_RD,))


def stock_edit_cases() -> list[CaseSpec]:
    return [_sale_out(), _purchase_in_po(), _purchase_in_qm(), _product_in(), _material_out()]
