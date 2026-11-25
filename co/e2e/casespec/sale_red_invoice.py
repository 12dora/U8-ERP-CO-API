"""红字销售发票参照退货单（sale_invoice ← sale_return）。

sale-red-invoice：蓝字发货行 → 已开票退货单（invoiced=true，bneedbill=1）→ 未审核时生红票 409 → 审核退货单 →
  红字发票 1（1 行、−1）→ 表头 bReturnFlag=1、iDisp=1，退货行 iSettleQuantity −1，订单行 iKPQuantity 净减 1 →
  删除红票 1 → 两项回到原值 → 红字发票 2 → 复核 → 已复核删除 409 → 弃复 → 删除成功（已实测：与蓝字不同，红字发票
  弃复后 U8 允许删除，删完回写归零）→ 弃审并删除退货单。用例中途失败时 RS_CLEANUP 尽力删红票、弃审并删退货单，
  删不掉的留给快照还原（docs/co/e2e/README.md「已知的还原差异」 的 invoice-generate 同一例外）。
  红票 2 保存后、复核前订单行 iKPQuantity 已净减 1，复核、弃复后不变，删除后回到原值；红票 iVTid 同蓝字。

挑单与回读 SQL 在 sale_red_invoice_sql。接线由 wiring.py 统一做：RS_BUILDERS / RS_PACKS / has_rs / rs_sql /
rs_samples 汇到 wiring 模块，rs_cases() 进 catalog，RS_CLEANUP 进 runner._CLEANUP。
"""

from __future__ import annotations

import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, apply_step, call_from, keep_step, pred_step, stash_step

_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_INT_MAX = 2147483647
_RET, _INV1, _INV2 = "bag:rs_ret", "bag:rs_inv1", "bag:rs_inv2"
_ON = (("verified_by", "$opname"),)
_OFF = (("verified_by", ""),)
_OK_GONE = (("deleted", "true"),)
_CONFLICT = (409, "state_mismatch")


# ---- 正文 ----


def _id(raw: str, label: str) -> int:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError(label)
    return int(raw)


def rs_return(env: Env) -> dict:
    # 参照蓝字发货行退 1 个，按已开票退货（bneedbill=1，回写原行 fretqtyykp），才能再开红字发票。
    line = {"source_line_id": _id(env.bag.get("rs_line", ""), "发货行 id 不合法"), "quantity": 1}
    return {"type": "sale_return", "id": _id(env.focus, "单据 id 不合法"), "head": {"invoiced": True}, "lines": [line]}


def rs_red(env: Env) -> dict:
    # 参照退货单行开 1 个红字发票（quantity 正数，桥写负数）。表头不给 cVouchType：桥按原蓝字发票类型取。
    line = {"source_line_id": _id(env.bag.get("rs_retl", ""), "退货行 id 不合法"), "quantity": 1}
    body = {"type": "sale_invoice", "id": _id(env.focus, "单据 id 不合法"), "source_type": "sale_return"}
    body["lines"] = [line]
    return body


RS_BUILDERS = {"rs_return": rs_return, "rs_red": rs_red}


# ---- 用例 ----


def _ref(kind: str, *extra: tuple[str, str]) -> tuple:
    return (("type", kind), ("id", "$id")) + extra


def _made(kind: str, *extra: tuple[str, str]) -> tuple:
    return (("type", kind), ("code", "$nonempty")) + extra


def _return_steps() -> tuple:
    return (
        apply_step(_GEN, "rs_return", "need", _made("sale_return")),
        keep_step("rs_ret"),
        pred_step("rs_rethead", "eq:1|1", _RET),
        stash_step("rs_retl", "rs_retl", _RET),
        apply_step(_GEN, "rs_red", _RET, (), _CONFLICT),
        call_from(_VERIFY, _ref("sale_return", ("action", "verify")), _RET, _ON),
        stash_step("rs_kp", "rs_kp", _RET),
    )


def _first_invoice_steps() -> tuple:
    red = _made("sale_invoice", ("lines", "1"), ("source_type", "sale_return"))
    return (
        apply_step(_GEN, "rs_red", _RET, red),
        keep_step("rs_inv1"),
        pred_step("rs_inv_shape", "eq:1|1|-1.0000", _INV1),
        pred_step("rs_set", "eq:-1.0000", _RET),
        pred_step("rs_kp", "shift:-1:rs_kp", _RET),
        call_from(_DELETE, _ref("sale_invoice"), _INV1, _OK_GONE),
        pred_step("rs_inv_gone", "eq:0", _INV1),
        pred_step("rs_set", "eq:0.0000", _RET),
        pred_step("rs_kp", "same:rs_kp", _RET),
    )


def _second_invoice_steps() -> tuple:
    red = _made("sale_invoice", ("lines", "1"), ("source_type", "sale_return"))
    return (
        apply_step(_GEN, "rs_red", _RET, red),
        keep_step("rs_inv2"),
        # 保存后、复核前订单行 iKPQuantity 已净减 1（保存时回写）；复核、弃复都不再变；红票 iVTid 同蓝字。
        pred_step("rs_kp", "shift:-1:rs_kp", _RET),
        pred_step("rs_vt", "eq:1", _INV2),
        call_from(_VERIFY, _ref("sale_invoice", ("action", "verify")), _INV2, _ON),
        pred_step("rs_kp", "shift:-1:rs_kp", _RET),
        call_from(_DELETE, _ref("sale_invoice"), _INV2, (), _CONFLICT),
        call_from(_VERIFY, _ref("sale_invoice", ("action", "unverify")), _INV2, _OFF),
        pred_step("rs_kp", "shift:-1:rs_kp", _RET),
        # 实测：红字发票弃复后 U8 允许删除（与蓝字不同），删完回写归零，再弃审并删除退货单。
        call_from(_DELETE, _ref("sale_invoice"), _INV2, _OK_GONE),
        pred_step("rs_set", "eq:0.0000", _RET),
        pred_step("rs_kp", "same:rs_kp", _RET),
        call_from(_VERIFY, _ref("sale_return", ("action", "unverify")), _RET, _OFF),
        call_from(_DELETE, _ref("sale_return"), _RET, _OK_GONE),
    )


def _case() -> CaseSpec:
    title = "退货单生成红字销售发票：退货行、订单行开票回写，删除回退；复核后删除 409、弃复后删除并删退货单"
    tags = frozenset(
        (
            "new:sale_return", "new:dispatch", "new:sale_invoice", "src:dispatch:rs_src",
            "w:sale_return", "w:sale_invoice", "stock",
        )
    )
    steps = _return_steps() + _first_invoice_steps() + _second_invoice_steps()
    needs = ("rs_src", "rs_line")
    return CaseSpec(
        "sale-red-invoice", title, needs, steps, True, optional=True, tags=tags, routes=(_GEN, _VERIFY, _DELETE)
    )


# ---- 补偿 ----


def _send(env: Env, route: str, extra: dict) -> int:
    # steps 经 wiring 引用本模块的构造器，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.steps import auth_fields, invoke

    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, route, fields)[0]


def _drop(env: Env, key: str, kind: str, unverify: bool) -> str:
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


def rs_compensate(env: Env, _case_name: str) -> str:
    # 尽力而为：先红票（已复核的先弃复），再弃审并删除退货单。红票删不掉时退货单也删不掉，留给快照还原。
    plan = (("rs_inv2", "sale_invoice", True), ("rs_inv1", "sale_invoice", True), ("rs_ret", "sale_return", True))
    notes = [_drop(env, key, kind, unverify) for key, kind, unverify in plan]
    text = "；".join(note for note in notes if note)
    return text or "没有新建的单据"


RS_CLEANUP = {"sale-red-invoice": rs_compensate}


def rs_cases() -> list[CaseSpec]:
    return [_case()]
