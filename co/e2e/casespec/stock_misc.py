"""库存零散用例：销售出库按发货单行部分生成；有货位记录的其他入库删除（先清货位再删）。

挑单与回读 SQL 在 stock_misc_sql；正文和用例在本模块。接线（catalog、picks、steps._make、steps_sql 的回读）
由 wiring.py 统一做：stock_misc_cases() 进用例登记，STOCK_MISC_PACKS 进挑单，STOCK_MISC_BUILDERS 进正文构造器，
has_stock_misc / stock_misc_sql 进 stash / pred 的回读，stock_misc_samples() 进样例 SQL。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_made, pred_step, stash_step
from co.e2e.casespec.stock_misc_sql import safe_code, safe_id

_GEN = "/v1/vouchers/generate"


# ---- 正文 ----


def sale_out_part(env: Env) -> dict:
    # 只出请求的发货行 1 个；其余行不出。
    line = {"source_line_id": int(safe_id(env.bag.get("spo_line", ""))), "quantity": 1}
    return {"type": "sale_out", "id": int(safe_id(env.focus)), "lines": [line]}


def binned_other_in(env: Env) -> dict:
    # 货位管理仓库的其他入库，一行带末级货位；U8 保存时写 InvPosition 和 InvPositionSum。
    keys = ("bin_wh", "bin_rd", "bin_dep", "bin_inv", "bin_pos")
    wh, rd, dep, inv, pos = (safe_code(env.bag.get(key, "")) for key in keys)
    head = {"cWhCode": wh, "cRdCode": rd, "cDepCode": dep, "dDate": env.today, "cMemo": "CO桥回归"}
    line = {"cInvCode": inv, "iQuantity": 1, "iUnitCost": 1, "cPosition": pos}
    return {"type": "other_in", "head": head, "lines": [line]}


STOCK_MISC_BUILDERS = {"sale_out_part": sale_out_part, "binned_other_in": binned_other_in}


# ---- 用例 ----


def _cmp(got: str, pred: str) -> Step:
    return Step("custom", fields=(("got", got), ("pred", pred)), sql_name="bag_compare", id_from="none")


def _gone(kind: str) -> tuple[Step, ...]:
    ref = (("type", kind), ("id", "$id"))
    return (
        call_made("/v1/vouchers/delete", ref, (("deleted", "true"),)),
        call_made("/v1/vouchers/load", ref, (), 404, "not_found"),
    )


def _sale_out_part() -> CaseSpec:
    # 请求行累计出库 +1、其余行不变、指向本发货单的出库行只多 1 行（整单生成后未请求的行和整张都已删掉）；
    # 删除后全部回到原值。生单后的回读先记进 bag，删除之后再判，断言失败也不会把出库单留在库里。
    names = ("spo_out", "spo_rest", "spo_rows")
    steps = tuple(stash_step(name, name, "need") for name in names)
    steps += (apply_step(_GEN, "sale_out_part", "need", (("type", "sale_out"), ("code", "$nonempty"))),)
    steps += tuple(stash_step(name, name + "_gen", "need") for name in names)
    steps += (stash_step("spo_link", "spo_link_gen"),)
    steps += _gone("sale_out")
    steps += (
        _cmp("spo_out_gen", "plus1:spo_out"),
        _cmp("spo_rest_gen", "same:spo_rest"),
        _cmp("spo_rows_gen", "plus1:spo_rows"),
        _cmp("spo_link_gen", "eq:1"),
    )
    steps += tuple(pred_step(name, "same:" + name, "need") for name in names)
    title = "发货单按行部分生成销售出库：只出请求行 1 个，其余行不动，删除后回退"
    return CaseSpec("sale-out-partial", title, ("spo_src", "spo_line"), steps, True)


def _binned_delete() -> CaseSpec:
    # 新建带货位的其他入库：InvPosition 有 1 行、货位存量 +1；删除后单据和 InvPosition 都没有，货位存量回到原值。
    steps = (
        stash_step("bin_psum", "bin_psum", "need"),
        Step("create", sql_name="binned_other_in", checks=(("code", "$nonempty"),)),
        stash_step("bin_pos", "bin_pos_gen"),
        stash_step("bin_psum", "bin_psum_gen"),
    )
    steps += _gone("other_in")
    steps += (
        _cmp("bin_pos_gen", "eq:1"),
        _cmp("bin_psum_gen", "plus1:bin_psum"),
        pred_step("bin_pos", "eq:0"),
        pred_step("bin_head", "eq:0"),
        pred_step("bin_psum", "same:bin_psum", "need"),
    )
    needs = ("bin_src", "bin_wh", "bin_rd", "bin_dep", "bin_inv", "bin_pos")
    title = "有货位记录的其他入库删除：先清货位再删，InvPosition 与货位存量回退"
    return CaseSpec("binned-other-in-delete", title, needs, steps, True)


def stock_misc_cases() -> list[CaseSpec]:
    return [_sale_out_part(), _binned_delete()]
