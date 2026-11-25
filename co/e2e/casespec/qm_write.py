"""质量单据新增 / 删除（VoucherOperate add / delete；删除已审核的报检单前 unconfirm）。报检单不开放单独审核、弃审。

qm01-create-delete：采购订单生成新到货单（要检验的存货，bGsp = 1、bInspect = 0）→ 审核 → 来料报检单（部分数量，
  U8 按选项自动审核，到货行 fInspectQuantity、bInspect 回写）→ 报检单单独弃审 400 → 来料检验单（检验员取报检部门的人员，
  检验方案取该存货最近一张来料检验单的方案）→ 报检单行 FSUMCHECKQTY、BFLAG 回写 → 删检验单 → 删报检单（桥先弃审）→
  计数回到 0、bInspect 回 0 → 弃审并删除到货单（补偿）。途中夹着拒绝路径：报检超出剩余 409、检验单缺检验员 400、
  检验超出报检数量 409、已有检验单的报检单删除 409。
qm02-create-delete：已审核生产订单行 → 产品报检单（DeclaredQty +1）→ 产品检验单 → 已有检验单的报检单删除 409 →
  删检验单 → 删报检单 → DeclaredQty 回到原值。

挑单与回读 SQL 在 qm_write_sql。接线：QMI_BUILDERS / QMI_PACKS / has_qmi / qmi_sql / qmi_samples
汇到 wiring 模块，qmi_cases() 进 catalog；QMI_CLEANUP 是用例中途失败时的补偿（按记下的主键倒序删除），
由 runner 在该用例不是 PASS 时调用。
"""

from __future__ import annotations

import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, apply_step, call_from, keep_step, pred_step, stash_step
from co.e2e.casespec.qm_write_sql import NO_DEFINE
from co.e2e.casespec.qm_tpl import tpl_step, with_tpl

_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_TOKEN = re.compile(r"^[0-9A-Za-z._-]{1,30}\Z")
_INT_MAX = 2147483647
_ARR, _Q1, _Q3 = "bag:qmi_arr", "bag:qmi_qm01", "bag:qmi_qm03"
_Q2, _Q4 = "bag:qmp_qm02", "bag:qmp_qm04"
_NEEDS_QMI = ("qmi_po", "qmi_pol", "qmi_wh", "qmi_proj", "qmi_psn", "qmi_d11")
_NEEDS_QMP = ("qmp_mo", "qmp_mod", "qmp_proj", "qmp_psn", "qmp_d11")
# 生单前读模板必输的自定义项（qm_tpl）：VT → bag 键。来料报检 351、产品报检 352、来料检验 353、产品检验 354。
TPL75 = (tpl_step(351, "qmi_t1"), tpl_step(353, "qmi_t3"))
TPL76 = (tpl_step(352, "qmp_t2"), tpl_step(354, "qmp_t4"))


# ---- 正文 ----


def _id(raw: str, label: str) -> int:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError(label)
    return int(raw)


def _code(env: Env, key: str) -> str:
    raw = (env.bag.get(key) or "").strip()
    if _TOKEN.fullmatch(raw) is None:
        raise ValueError("挑单编码不合法：" + key)
    return raw


def _focus(env: Env) -> int:
    return _id(env.focus, "单据 id 不合法")


def qmi_arrival(env: Env) -> dict:
    # 到货 2 个：报检 1 个，留 1 个给超出剩余的拒绝路径。
    line = {"source_line_id": _id(env.bag.get("qmi_pol", ""), "订单行 id 不合法"), "quantity": 2}
    return {"type": "arrival", "id": _focus(env), "head": {"cWhCode": _code(env, "qmi_wh")}, "lines": [line]}


def _inspect(env: Env, kind: str, line_key: str, qty: int) -> dict:
    line = {"source_line_id": _id(env.bag.get(line_key, ""), "来源行 id 不合法"), "quantity": qty}
    return {"type": kind, "id": _focus(env), "lines": [line]}


def qmi_qm01(env: Env) -> dict:
    return with_tpl(env, dict(_inspect(env, "qm_incoming_inspect", "qmi_arrl", 1), source_type="arrival"), "qmi_t1")


def qmi_qm01_over(env: Env) -> dict:
    # 到货 2、已报检 1，再报 2 超出剩余，桥应 409。
    return with_tpl(env, _inspect(env, "qm_incoming_inspect", "qmi_arrl", 2), "qmi_t1")


def qmp_qm02(env: Env) -> dict:
    body = dict(_inspect(env, "qm_product_inspect", "qmp_mod", 1), source_type="production_order")
    return with_tpl(env, body, "qmp_t2")


def _check_head(env: Env, lead: str) -> dict:
    head = {"cCheckPersonCode": _code(env, lead + "_psn"), "project_code": _code(env, lead + "_proj")}
    for number in ("11", "12", "13"):
        value = _code(env, lead + "_d" + number)
        if value != NO_DEFINE:
            head["chDefine" + number] = value
    return head


def _check(env: Env, kind: str, lead: str, line_key: str, tpl_key: str) -> dict:
    body = _inspect(env, kind, line_key, 1)
    body["head"] = _check_head(env, lead)
    return with_tpl(env, body, tpl_key)


def qmi_qm03(env: Env) -> dict:
    return _check(env, "qm_incoming_check", "qmi", "qmi_q1l", "qmi_t3")


def qmi_qm03_noperson(env: Env) -> dict:
    body = qmi_qm03(env)
    body["head"].pop("cCheckPersonCode")
    return body


def qmi_qm03_over(env: Env) -> dict:
    # 报检 1 个，检验 2 个超出，桥应 409。
    body = qmi_qm03(env)
    body["lines"][0]["quantity"] = 2
    return body


def qmp_qm04(env: Env) -> dict:
    return _check(env, "qm_product_check", "qmp", "qmp_q2l", "qmp_t4")


QMI_BUILDERS = {
    "qmi_arrival": qmi_arrival,
    "qmi_qm01": qmi_qm01,
    "qmi_qm01_over": qmi_qm01_over,
    "qmi_qm03": qmi_qm03,
    "qmi_qm03_noperson": qmi_qm03_noperson,
    "qmi_qm03_over": qmi_qm03_over,
    "qmp_qm02": qmp_qm02,
    "qmp_qm04": qmp_qm04,
}


# ---- 用例 ----


def _ref(kind: str, *extra: tuple[str, str]) -> tuple:
    return (("type", kind), ("id", "$id")) + extra


def _made(kind: str, *extra: tuple[str, str]) -> tuple:
    return (("type", kind), ("code", "$nonempty")) + extra


_OK = (("ok", "true"),)
_GONE = (("deleted", "true"),)
_CONFLICT = (409, "state_mismatch")


def _qm01_head_steps() -> tuple:
    # 新到货单 → 审核 → 来料报检单（U8 按 bArrInspectAutoVerify 自动审核）→ 回写、拒绝路径（含报检单单独弃审 400）。
    inspect = _made("qm_incoming_inspect", ("lines", "1"), ("source_type", "arrival"), ("state.verified", "true"))
    return TPL75 + (
        apply_step(_GEN, "qmi_arrival", "need", _made("arrival")),
        keep_step("qmi_arr"),
        pred_step("qmi_arr_flags", "eq:1|0", _ARR),
        call_from(_VERIFY, _ref("arrival", ("action", "verify")), _ARR, _OK),
        stash_step("qmi_arrl", "qmi_arrl", _ARR),
        apply_step(_GEN, "qmi_qm01", _ARR, inspect),
        keep_step("qmi_qm01"),
        pred_step("qmi_q1_maker", "opname", _Q1),
        pred_step("qmi_arr_insp", "eq:1.0000|0", _ARR),
        apply_step(_GEN, "qmi_qm01_over", _ARR, (), _CONFLICT),
        # 报检单不开放单独审核、弃审（U8 的 confirm 对报检单总是失败），桥在登录前 400。
        call_from(_VERIFY, _ref("qm_incoming_inspect", ("action", "unverify")), _Q1, (), (400, "bad_request")),
        stash_step("qmi_q1l", "qmi_q1l", _Q1),
    )


def _qm01_tail_steps() -> tuple:
    # 来料检验单 → 回写 → 报检单删除 409 → 删检验单 → 删报检单 → 计数归零 → 弃审并删除到货单。
    check = _made("qm_incoming_check", ("lines", "1"), ("state.verified", "false"))
    return (
        apply_step(_GEN, "qmi_qm03_noperson", _Q1, (), (400, "bad_request")),
        apply_step(_GEN, "qmi_qm03_over", _Q1, (), _CONFLICT),
        apply_step(_GEN, "qmi_qm03", _Q1, check),
        keep_step("qmi_qm03"),
        pred_step("qmi_q1_sum", "eq:1.0000|1", _Q1),
        call_from(_DELETE, _ref("qm_incoming_inspect"), _Q1, (), _CONFLICT),
        call_from(_DELETE, _ref("qm_incoming_check"), _Q3, _GONE),
        pred_step("qmi_q1_sum", "eq:0.0000|0", _Q1),
        call_from(_DELETE, _ref("qm_incoming_inspect"), _Q1, _GONE + (("unverified", "true"),)),
        pred_step("qmi_q1_gone", "eq:0", _Q1),
        pred_step("qmi_arr_insp", "eq:0.0000|0", _ARR),
        call_from(_VERIFY, _ref("arrival", ("action", "unverify")), _ARR, _OK),
        call_from(_DELETE, _ref("arrival"), _ARR, _GONE),
    )


def _qm01_case() -> CaseSpec:
    title = "来料报检单、来料检验单新增与删除：到货 / 报检 / 检验回写，超量 409、缺检验员 400、有检验单删报检单 409"
    # 到货单动现存量口径（待入），按 tags.py 的保守约定带 stock；src 标签绑定挑中的采购订单，和别的到货生单互斥。
    tags = frozenset(
        (
            "new:arrival", "new:purchase_return", "new:qm_incoming_inspect", "new:qm_incoming_check",
            "src:purchase_order:qmi_po", "stock",
        )
    )
    steps = _qm01_head_steps() + _qm01_tail_steps()
    return CaseSpec("qm01-create-delete", title, _NEEDS_QMI, steps, True, optional=True, tags=tags, routes=(_GEN, _VERIFY, _DELETE))


def _qm02_case() -> CaseSpec:
    # 产品报检单（U8 按 bProInspectAutoVerify 自动审核）→ 产品检验单 → 删除倒序，DeclaredQty 回到原值。
    inspect = _made("qm_product_inspect", ("lines", "1"), ("source_type", "production_order"), ("state.verified", "true"))
    check = _made("qm_product_check", ("lines", "1"), ("state.verified", "false"))
    steps = TPL76 + (
        stash_step("qmp_decl", "qmp_decl", "need"),
        apply_step(_GEN, "qmp_qm02", "need", inspect),
        keep_step("qmp_qm02"),
        pred_step("qmp_q2_maker", "opname", _Q2),
        pred_step("qmp_decl", "shift:1:qmp_decl", "need"),
        stash_step("qmp_q2l", "qmp_q2l", _Q2),
        apply_step(_GEN, "qmp_qm04", _Q2, check),
        keep_step("qmp_qm04"),
        pred_step("qmp_q2_sum", "eq:1.0000|1", _Q2),
        call_from(_DELETE, _ref("qm_product_inspect"), _Q2, (), _CONFLICT),
        call_from(_DELETE, _ref("qm_product_check"), _Q4, _GONE),
        pred_step("qmp_q2_sum", "eq:0.0000|0", _Q2),
        call_from(_DELETE, _ref("qm_product_inspect"), _Q2, _GONE),
        pred_step("qmp_q2_gone", "eq:0", _Q2),
        pred_step("qmp_decl", "same:qmp_decl", "need"),
    )
    title = "产品报检单、产品检验单新增与删除：生产订单 DeclaredQty 与报检单累计检验回写，有检验单删报检单 409"
    # 生产订单在 STOCK_KINDS 里，别的生产订单用例都带 stock；这里也带上，保守互斥。
    tags = frozenset(("new:qm_product_inspect", "new:qm_product_check", "src:production_order:qmp_mo", "stock"))
    return CaseSpec("qm02-create-delete", title, _NEEDS_QMP, steps, True, optional=True, tags=tags, routes=(_GEN, _DELETE))


# ---- 补偿 ----


def _send(env: Env, route: str, extra: dict) -> int:
    # steps 经 wiring 引用本模块的构造器，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.steps import auth_fields, invoke

    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, route, fields)[0]


def _drop(env: Env, key: str, kind: str, unverify: bool = False) -> str:
    raw = env.bag.get(key, "")
    if _ID.fullmatch(raw or "") is None:
        return ""
    ref = {"type": kind, "id": int(raw)}
    try:
        if unverify:
            _send(env, _VERIFY, dict(ref, action="unverify"))
        status = _send(env, _DELETE, ref)
    except (U8CoError, OSError, RuntimeError, ValueError) as exc:
        return f"{kind} {raw} 补偿出错：{str(exc)[:60]}"
    if status in (200, 404):
        return f"{kind} {raw} 已清理"
    return f"{kind} {raw} 返回 {status}，留给快照还原"


def _compensate(env: Env, plan: tuple[tuple[str, str, bool], ...]) -> str:
    # 尽力而为：按下游到上游的顺序删（报检单删除时桥自己先弃审），返回一句说明拼进失败信息。
    notes = [_drop(env, key, kind, unverify) for key, kind, unverify in plan]
    text = "；".join(note for note in notes if note)
    return text or "没有新建的单据"


def qmi_compensate(env: Env, _case: str) -> str:
    plan = (
        ("qmi_qm03", "qm_incoming_check", False),
        ("qmi_qm01", "qm_incoming_inspect", False),
        ("qmi_arr", "arrival", True),
    )
    return _compensate(env, plan)


def qmp_compensate(env: Env, _case: str) -> str:
    return _compensate(env, (("qmp_qm04", "qm_product_check", False), ("qmp_qm02", "qm_product_inspect", False)))


QMI_CLEANUP = {"qm01-create-delete": qmi_compensate, "qm02-create-delete": qmp_compensate}


def qmi_cases() -> list[CaseSpec]:
    return [_qm01_case(), _qm02_case()]
