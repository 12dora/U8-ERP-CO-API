"""红字采购链路（采购退货单 → 红字采购入库 → 红字采购发票），全程自建自删。

purchase-in-red-chain：蓝字到货行退 1 个生成采购退货单 → 审核 → 参照退货单生成红字采购入库（超量 409）→
  退货行累计入库 fValidInQuan 移动 −1 → 审核红字入库 → 参照红字入库生成红字发票（超量 409，bNegative=1、数量 −1，
  入库行 iSumBillQuantity 移动 −1）→ 复核、取消复核 → 删发票（累计开票回 0）→ 弃审并删除红字入库（fValidInQuan 回 0）→
  弃审并删除退货单。账套里现有的红字入库都已参与采购结算，红字发票只能在本用例自建的红字入库上测。

purchase-invoice-update-red：退货 2 个 → 红字入库 2 个 → 红字发票 2 个（入库行累计开票 2）→ 修改成 1 个并改备注（数量 −1、金额重算、
  入库行累计开票回到 1）→ 删发票（回 0）→ 弃审并删除红字入库、退货单。

挑单与回读 SQL 在 pu_red_chain_sql。接线：REDPUR_BUILDERS / REDPUR_PACKS / has_redpur / redpur_sql / redpur_samples
汇到 wiring 模块，redpur_cases() 进 catalog；REDPUR_CLEANUP 是用例中途失败时的补偿（按记下的主键倒序删除）。
"""

from __future__ import annotations

import re
from datetime import datetime

from co.e2e.casespec.compensate import compensate
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, apply_step, call_from, keep_step, pred_step, stash_step

_GEN = "/v1/vouchers/generate"
_UPD = "/v1/vouchers/update"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_TOKEN = re.compile(r"^[0-9A-Za-z._-]{1,30}\Z")
_INT_MAX = 2147483647
_RET, _IN, _PBV = "bag:rp_ret", "bag:rp_in", "bag:rp_pbv"
# 红字发票修改用例的单据主键用独立键，--serial 下不会和红字链路用例的残留主键互相串。
_URET, _UIN, _UPBV = "bag:rpu_ret", "bag:rpu_in", "bag:rpu_pbv"
_NEEDS = ("rp_arr", "rp_arrl", "rp_wh")
_NEEDS_UPD = ("rpu_arr", "rpu_arrl", "rpu_wh")
_MEMO_UPD = "U8CO-REDINV-UPD"


# ---- 正文 ----


def _id(raw: str, label: str) -> int:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError(label)
    return int(raw)


def _wh(env: Env, key: str = "rp_wh") -> str:
    raw = (env.bag.get(key) or "").strip()
    if _TOKEN.fullmatch(raw) is None:
        raise ValueError("挑单编码不合法：" + key)
    return raw


def _line(env: Env, key: str, qty: int) -> list[dict]:
    return [{"source_line_id": _id(env.bag.get(key, ""), "来源行 id 不合法"), "quantity": qty}]


def _ret(env: Env, keys: tuple[str, str], qty: int) -> dict:
    # 退货单行带仓库（红字入库的待入库视图要求到货行有仓库）。keys：到货行键、仓库键。
    head = {"cWhCode": _wh(env, keys[1]), "dDate": env.today}
    body = {"type": "purchase_return", "id": _id(env.focus, "单据 id 不合法"), "source_type": "arrival"}
    body.update({"head": head, "lines": _line(env, keys[0], qty)})
    return body


def rp_ret(env: Env) -> dict:
    return _ret(env, ("rp_arrl", "rp_wh"), 1)


def rpu_ret(env: Env) -> dict:
    # 红字发票修改用例退 2 个（后面改成 1 个）。
    return _ret(env, ("rpu_arrl", "rpu_wh"), 2)


def _red_in(env: Env, qty: int, wh_key: str = "rp_wh") -> dict:
    body = {"type": "purchase_in", "id": _id(env.focus, "单据 id 不合法"), "source_type": "purchase_return"}
    body.update({"head": {"cWhCode": _wh(env, wh_key), "dDate": env.today}, "lines": _line(env, "rp_retl", qty)})
    return body


def rp_in(env: Env) -> dict:
    return _red_in(env, 1)


def rp_in_over(env: Env) -> dict:
    # 退货 1 个，入库 2 个超出，桥应 409。
    return _red_in(env, 2)


def _red_inv(env: Env, qty: int) -> dict:
    # 发票号每次不同（超量那次在桥里就被拒，不占号）。
    code = "RE2E" + datetime.now().strftime("%m%d%H%M%S")
    head = {"cPBVCode": code, "cPBVBillType": "01", "dPBVDate": env.today}
    return {"type": "purchase_invoice", "id": _id(env.focus, "单据 id 不合法"), "head": head, "lines": _line(env, "rp_inl", qty)}


def rp_inv(env: Env) -> dict:
    return _red_inv(env, 1)


def rp_inv_over(env: Env) -> dict:
    # 红字入库 1 个，开票 2 个超出，桥应 409。
    return _red_inv(env, 2)


def rpu_in(env: Env) -> dict:
    return _red_in(env, 2, "rpu_wh")


def rpu_inv(env: Env) -> dict:
    return _red_inv(env, 2)


def rpu_upd(env: Env) -> dict:
    # 红字发票行数量（正数）2 → 1，同时改表头和表体备注。
    line = {"op": "update", "line_id": _id(env.bag.get("rp_invl", ""), "发票行 id 不合法")}
    line.update({"ipbvquantity": 1, "cbmemo": _MEMO_UPD})
    head = {"cpbvmemo": _MEMO_UPD}
    return {"type": "purchase_invoice", "id": _id(env.focus, "单据 id 不合法"), "head": head, "lines": [line]}


REDPUR_BUILDERS = {
    "rp_ret": rp_ret,
    "rp_in": rp_in,
    "rp_in_over": rp_in_over,
    "rp_inv": rp_inv,
    "rp_inv_over": rp_inv_over,
    "rpu_ret": rpu_ret,
    "rpu_in": rpu_in,
    "rpu_inv": rpu_inv,
    "rpu_upd": rpu_upd,
}


# ---- 用例 ----


def _ref(kind: str, *extra: tuple[str, str]) -> tuple:
    return (("type", kind), ("id", "$id")) + extra


def _made(kind: str, *extra: tuple[str, str]) -> tuple:
    return (("type", kind), ("code", "$nonempty")) + extra


_OK = (("ok", "true"),)
_GONE = (("deleted", "true"),)
_CONFLICT = (409, "state_mismatch")


def _head_steps() -> tuple:
    # 采购退货单 → 审核 → 红字采购入库（超量 409）→ 退货行累计入库 −1 → 审核红字入库。
    red_in = _made("purchase_in", ("lines", "1"), ("source_type", "purchase_return"))
    return (
        apply_step(_GEN, "rp_ret", "need", _made("purchase_return")),
        keep_step("rp_ret"),
        call_from(_VERIFY, _ref("purchase_return", ("action", "verify")), _RET, _OK),
        stash_step("rp_retl", "rp_retl", _RET),
        pred_step("rp_valid", "eq:0.0000", _RET),
        apply_step(_GEN, "rp_in_over", _RET, (), _CONFLICT),
        apply_step(_GEN, "rp_in", _RET, red_in),
        keep_step("rp_in"),
        pred_step("rp_in_red", "eq:1|-1.0000|1", _IN),
        pred_step("rp_valid", "eq:1.0000", _RET),
        call_from(_VERIFY, _ref("purchase_in", ("action", "verify")), _IN, _OK),
        stash_step("rp_inl", "rp_inl", _IN),
    )


def _invoice_steps() -> tuple:
    # 红字发票（超量 409）→ bNegative、数量、累计开票 → 复核、取消复核 → 删除 → 累计开票回 0。
    return (
        pred_step("rp_billed_in", "eq:0.0000", _IN),
        apply_step(_GEN, "rp_inv_over", _IN, (), _CONFLICT),
        apply_step(_GEN, "rp_inv", _IN, _made("purchase_invoice", ("lines", "1"))),
        keep_step("rp_pbv"),
        pred_step("rp_inv", "eq:1|-1.0000", _PBV),
        pred_step("rp_billed_in", "eq:1.0000", _IN),
        call_from(_VERIFY, _ref("purchase_invoice", ("action", "verify")), _PBV, _OK),
        pred_step("rp_rev", "opname", _PBV),
        call_from(_VERIFY, _ref("purchase_invoice", ("action", "unverify")), _PBV, _OK),
        pred_step("rp_rev", "empty", _PBV),
        call_from(_DELETE, _ref("purchase_invoice"), _PBV, _GONE),
        pred_step("rp_billed_in", "eq:0.0000", _IN),
    )


def _tail_steps(ret: str = _RET, inn: str = _IN) -> tuple:
    # 弃审并删除红字入库（退货行累计入库回 0）→ 弃审并删除退货单。ret、inn：退货单、红字入库的 bag 引用。
    return (
        call_from(_VERIFY, _ref("purchase_in", ("action", "unverify")), inn, _OK),
        call_from(_DELETE, _ref("purchase_in"), inn, _GONE),
        pred_step("rp_valid", "eq:0.0000", ret),
        call_from(_VERIFY, _ref("purchase_return", ("action", "unverify")), ret, _OK),
        call_from(_DELETE, _ref("purchase_return"), ret, _GONE),
    )


def _chain_case() -> CaseSpec:
    title = "红字采购：退货单生成红字入库、红字入库生成红字发票，回写、复核、超量 409，倒序删除后回写归零"
    # 到货单 ↔ 采购退货单共用编号（交叉 new:），红字入库审核扣现存量（stock）；src 绑定挑中的到货单。
    tags = frozenset(
        (
            "new:arrival", "new:purchase_return", "new:purchase_in", "new:purchase_invoice",
            "src:arrival:rp_arr", "w:purchase_in", "w:purchase_invoice", "stock",
        )
    )
    steps = _head_steps() + _invoice_steps() + _tail_steps()
    routes = (_GEN, _VERIFY, _DELETE)
    return CaseSpec("purchase-in-red-chain", title, _NEEDS, steps, True, optional=True, tags=tags, routes=routes)


def _update_steps() -> tuple:
    # 退货 2 → 审核 → 红字入库 2 → 审核 → 红字发票 2 → 修改成 1 → 数量、金额、备注、入库行累计开票 → 删除发票。
    red_in = _made("purchase_in", ("lines", "1"), ("source_type", "purchase_return"))
    return (
        apply_step(_GEN, "rpu_ret", "need", _made("purchase_return")),
        keep_step("rpu_ret"),
        call_from(_VERIFY, _ref("purchase_return", ("action", "verify")), _URET, _OK),
        stash_step("rp_retl", "rp_retl", _URET),
        apply_step(_GEN, "rpu_in", _URET, red_in),
        keep_step("rpu_in"),
        call_from(_VERIFY, _ref("purchase_in", ("action", "verify")), _UIN, _OK),
        stash_step("rp_inl", "rp_inl", _UIN),
        pred_step("rp_billed_in", "eq:0.0000", _UIN),
        apply_step(_GEN, "rpu_inv", _UIN, _made("purchase_invoice", ("lines", "1"))),
        keep_step("rpu_pbv"),
        pred_step("rp_inv", "eq:1|-2.0000", _UPBV),
        pred_step("rp_billed_in", "eq:2.0000", _UIN),
        stash_step("rp_invl", "rp_invl", _UPBV),
        apply_step(_UPD, "rpu_upd", _UPBV, (("ok", "true"), ("type", "purchase_invoice"))),
        pred_step("rp_inv", "eq:1|-1.0000", _UPBV),
        pred_step("rp_inv_amt", "eq:1", _UPBV),
        pred_step("rp_pbvmemo", "eq:" + _MEMO_UPD, _UPBV),
        pred_step("rp_billed_in", "eq:1.0000", _UIN),
        call_from(_DELETE, _ref("purchase_invoice"), _UPBV, _GONE),
        pred_step("rp_billed_in", "eq:0.0000", _UIN),
    ) + _tail_steps(_URET, _UIN)


def _update_case() -> CaseSpec:
    title = "红字采购发票修改：数量 2 改 1，入库行累计开票随之移向 0，金额、备注重算，删除后回 0"
    tags = frozenset(
        (
            "new:arrival", "new:purchase_return", "new:purchase_in", "new:purchase_invoice",
            "src:arrival:rpu_arr", "w:purchase_in", "w:purchase_invoice", "stock",
        )
    )
    routes = (_GEN, _VERIFY, _UPD, _DELETE)
    steps = _update_steps()
    return CaseSpec("purchase-invoice-update-red", title, _NEEDS_UPD, steps, True, optional=True, tags=tags, routes=routes)


# ---- 补偿 ----


def _compensator(prefix: str):
    # 发票先取消复核再删；红字入库、退货单先弃审再删（没审核时弃审报错，忽略）。prefix 区分两个用例的单据主键。
    plan = (
        (prefix + "_pbv", "purchase_invoice", True),
        (prefix + "_in", "purchase_in", True),
        (prefix + "_ret", "purchase_return", True),
    )

    def run(env: Env, _case: str) -> str:
        return compensate(env, plan)

    return run


redpur_compensate = _compensator("rp")
redpur_update_compensate = _compensator("rpu")

REDPUR_CLEANUP = {"purchase-in-red-chain": redpur_compensate, "purchase-invoice-update-red": redpur_update_compensate}


def redpur_cases() -> list[CaseSpec]:
    return [_chain_case(), _update_case()]
