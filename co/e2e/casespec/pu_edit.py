"""写用例：到货单、采购退货单、采购发票的修改（vouchers/update）。

每条用例先生单 2 个，改成 1 个（数量只能减少），核对来源累计数的变化和新行金额已重算，再删除并确认来源回到原值。
生单、修改后的回读先记进 bag，删除之后再判，断言失败也不会把新单留在库里；修改本身失败时留给快照还原。

接线：PU_MOD_BUILDERS 进 steps._make，PU_MOD_PACKS 进 picks._PACKS / pick_keys，
has_pu_mod / pu_mod_sql 进 steps_sql._flow_cell，pu_mod_samples 进 samples.sample_sql，pu_edit_cases 进 catalog。
"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_made, pred_step, stash_step

_GEN = "/v1/vouchers/generate"
_UPD = "/v1/vouchers/update"
_ONE = "eq:1.0000"


def _ref(kind: str) -> tuple:
    return (("type", kind), ("id", "$id"))


def _drop(kind: str) -> tuple[Step, ...]:
    return (
        call_made("/v1/vouchers/delete", _ref(kind), (("deleted", "true"),)),
        call_made("/v1/vouchers/load", _ref(kind), (), 404, "not_found"),
    )


def _cmp(got: str, pred: str) -> Step:
    return Step("custom", fields=(("got", got), ("pred", pred)), sql_name="bag_compare", id_from="none")


def _cycle(kind: str, prefix: str, head: str, sources: tuple[str, ...], follow: bool = False) -> tuple[Step, ...]:
    # prefix 是正文生成器名前缀，head 是新单回读名前缀。基准 → 生单 2 → 记来源和新行 → 修改成 1 → 记来源、新行数量和金额核对 → 删除。
    # follow：到货单、退货单改前改后各记一次实收 / 合格数量（件数）与数量相等的位图（head_nfol）。
    made = (("type", kind), ("code", "$nonempty"))
    steps = tuple(stash_step(name, name, "need") for name in sources)
    steps += (apply_step(_GEN, prefix + "_gen", "need", made),)
    steps += tuple(stash_step(name, name + "_gen", "need") for name in sources)
    steps += (stash_step(head + "_nline", head + "_nline"),)
    if follow:
        steps += (stash_step(head + "_nfol", head + "_nfol"),)
    steps += (apply_step(_UPD, prefix + "_upd", "made", (("ok", "true"), ("type", kind))),)
    steps += tuple(stash_step(name, name + "_upd", "need") for name in sources)
    steps += (stash_step(head + "_nqty", head + "_nqty"), stash_step(head + "_namt", head + "_namt"))
    if follow:
        steps += (stash_step(head + "_nfol", head + "_nfol_upd"),)
    return steps + _drop(kind)


def _after(head: str, shifts: tuple[tuple[str, int], ...], follow: bool = False) -> tuple[Step, ...]:
    # 生单后来源变了 2×方向，修改后只剩 1×方向；新行数量 1、金额对得上；删除后回到原值。
    # follow：改前等于原数量的实收 / 合格列，改后等于新数量（位图不变；以实测为准，U8 是否自己维护，桥按此写入）。
    steps: tuple[Step, ...] = ()
    for name, sign in shifts:
        steps += (_cmp(name + "_gen", f"shift:{2 * sign}:{name}"), _cmp(name + "_upd", f"shift:{sign}:{name}"))
    steps += (_cmp(head + "_nqty", _ONE), _cmp(head + "_namt", "eq:1"))
    if follow:
        steps += (_cmp(head + "_nfol_upd", "same:" + head + "_nfol"),)
    return steps + tuple(pred_step(name, "same:" + name, "need") for name, _sign in shifts)


def _arrival() -> CaseSpec:
    # 采购订单生成到货单 2 个 → 改成 1 个：订单行 iArrQTY 先 +2、改后 +1；删除后回到原值。
    shifts = (("arr_mod_arr", 1),)
    steps = _cycle("arrival", "pu_mod_arr", "arr_mod", ("arr_mod_arr",), True) + _after("arr_mod", shifts, True)
    title = "到货单修改：数量 2 改 1，订单行累计到货随之回退，金额、实收合格数量随改，删除后还原"
    return CaseSpec("arrival-update", title, ("arr_mod_src", "arr_mod_line", "arr_mod_wh"), steps, True)


def _purchase_return() -> CaseSpec:
    # 到货单生成退货单退 2 个 → 改成退 1 个：原行 fRetQuantity、订单行 fPoRetQuantity 先 +2 后 +1，
    # 订单行 iArrQTY 先 −2 后 −1；删除后都回到原值。
    shifts = (("pret_mod_ret", 1), ("pret_mod_poret", 1), ("pret_mod_parr", -1))
    names = tuple(name for name, _sign in shifts)
    steps = _cycle("purchase_return", "pu_mod_ret", "pret_mod", names, True) + _after("pret_mod", shifts, True)
    title = "采购退货单修改：退货数量 2 改 1，到货行、订单行回写随之回退，删除后还原"
    return CaseSpec("purchase-return-update", title, ("pret_mod_src", "pret_mod_line"), steps, True)


def _purchase_invoice() -> CaseSpec:
    # 采购入库生成发票 2 个 → 改成 1 个：入库行 iSumBillQuantity 先 +2 后 +1；删除后回到原值。
    shifts = (("pinv_mod_billed", 1),)
    steps = _cycle("purchase_invoice", "pu_mod_inv", "pinv_mod", ("pinv_mod_billed",)) + _after("pinv_mod", shifts)
    title = "采购发票修改：数量 2 改 1，入库行累计开票随之回退，金额重算，删除后还原"
    return CaseSpec("purchase-invoice-update", title, ("pinv_mod_src", "pinv_mod_line"), steps, True)


def pu_edit_cases() -> tuple:
    return (_arrival(), _purchase_return(), _purchase_invoice())
