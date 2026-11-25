"""写用例：发货单、退货单（红字发货单）、销售发票的修改（vouchers/update）。

每条用例先生单 2 个，改成 1 个（数量只能减少），核对来源累计数的变化、新行金额已重算、表头备注和修改人，
再删除并确认来源回到原值。生单、修改后的回读先记进 bag，删除之后再判，断言失败也不会把新单留在库里；
修改本身失败时留给快照还原。销售发票修改已实测；失败时先看审计里的 idisp 记录。

接线由 wiring.py 统一做：SA_MOD_BUILDERS 进 steps._make，SA_MOD_PACKS 进 picks._PACKS / pick_keys，
has_sa_mod / sa_mod_sql 进 steps_sql._flow_cell，sa_mod_samples 进 samples.sample_sql，sale_edit_cases 进 catalog。
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


def _cycle(kind: str, prefix: str, head: str, sources: tuple[str, ...], extra: tuple[Step, ...] = ()) -> tuple[Step, ...]:
    # 基准 → 生单 2 → 记来源和新行 → （extra）→ 修改成 1 → 记来源、新行数量和金额、表头 → 删除。
    made = (("type", kind), ("code", "$nonempty"))
    steps = tuple(stash_step(name, name, "need") for name in sources)
    steps += (apply_step(_GEN, prefix + "_gen", "need", made),)
    steps += tuple(stash_step(name, name + "_gen", "need") for name in sources)
    steps += (stash_step(head + "_nline", head + "_nline"),) + extra
    steps += (apply_step(_UPD, prefix + "_upd", "made", (("ok", "true"), ("type", kind), ("lines", "1"))),)
    steps += tuple(stash_step(name, name + "_upd", "need") for name in sources)
    steps += (stash_step(head + "_nqty", head + "_nqty"), stash_step(head + "_namt", head + "_namt"))
    steps += (stash_step(head + "_head", head + "_head"),)
    return steps


def _after(head: str, shifts: tuple[tuple[str, int], ...]) -> tuple[Step, ...]:
    # 生单后来源变了 2×方向，修改后只剩 1×方向；新行数量 1、金额对得上、表头已改；删除后回到原值。
    steps: tuple[Step, ...] = ()
    for name, sign in shifts:
        steps += (_cmp(name + "_gen", f"shift:{2 * sign}:{name}"), _cmp(name + "_upd", f"shift:{sign}:{name}"))
    steps += (_cmp(head + "_nqty", _ONE), _cmp(head + "_namt", "eq:1"), _cmp(head + "_head", "eq:1"))
    return steps + tuple(pred_step(name, "same:" + name, "need") for name, _sign in shifts)


def _dispatch() -> CaseSpec:
    # 销售订单生成发货单 2 个 → 先试改成 3 个（400）→ 改成 1 个：订单行 iFHQuantity 先 +2、改后 +1；删除后还原。
    shifts = (("disp_mod_fh", 1),)
    up = apply_step(_UPD, "sa_mod_disp_up", "made", (), (400, "bad_request"))
    steps = _cycle("dispatch", "sa_mod_disp", "disp_mod", ("disp_mod_fh",), (up,)) + _drop("dispatch") + _after("disp_mod", shifts)
    title = "发货单修改：数量 2 改 1（改大 400），订单行累计发货随之回退，金额重算，删除后还原"
    return CaseSpec("dispatch-update", title, ("disp_mod_src", "disp_mod_line", "disp_mod_wh"), steps, True)


def _sale_return() -> CaseSpec:
    # 蓝字发货行退 2 个（未开票）→ 改成退 1 个：原行 iRetQuantity、fretqtywkp，订单行 fretquantity 先 +2 后 +1，
    # 订单行 iFHQuantity 先 −2 后 −1；删除后都回到原值。
    shifts = (("sret_mod_ret", 1), ("sret_mod_wkp", 1), ("sret_mod_soret", 1), ("sret_mod_sofh", -1))
    names = tuple(name for name, _sign in shifts)
    steps = _cycle("sale_return", "sa_mod_ret", "sret_mod", names) + _drop("sale_return") + _after("sret_mod", shifts)
    title = "退货单修改：退货数量 2 改 1，原发货行和订单行回写随之回退，删除后还原"
    return CaseSpec("sale-return-update", title, ("sret_mod_src", "sret_mod_line"), steps, True)


def _sale_invoice() -> CaseSpec:
    # 发货行开票 2 个 → 改成 1 个：发货行 iSettleQuantity 先 +2 后 +1，表头 iDisp 改后为 1；删除后还原。
    shifts = (("sinv_mod_settle", 1),)
    steps = _cycle("sale_invoice", "sa_mod_inv", "sinv_mod", ("sinv_mod_settle",))
    steps += (stash_step("sinv_mod_disp", "sinv_mod_disp"),) + _drop("sale_invoice") + _after("sinv_mod", shifts)
    steps += (_cmp("sinv_mod_disp", "eq:1"),)
    title = "销售发票修改：数量 2 改 1，发货行累计开票随之回退，金额重算，删除后还原"
    return CaseSpec("sale-invoice-update", title, ("sinv_mod_src", "sinv_mod_line"), steps, True)


def sale_edit_cases() -> tuple:
    return (_dispatch(), _sale_return(), _sale_invoice())
