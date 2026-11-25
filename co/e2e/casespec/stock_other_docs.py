"""形态转换单、调拨申请单、盘点单（USERPCO.VoucherCO），全程自建自删。

shape-change-cycle：新建（转换前 / 转换后各一行，同组）→ 改表头和转换后行的备注 → 审核（生成其他出库、其他入库各一张，
  cSource=形态转换）→ 已审核删除 409 → 弃审（生成的两张随之删除）→ 删除。
transfer-request-cycle：新建 → 改数量并加一行 → 审核（不生单）→ 已审核删除 409 → 弃审 → 删除。
stock-check-create-delete：新建（账面数量不给，桥按现存量填）→ 同一仓库再建 409（U8 一个仓库只能有一张未审核的盘点单）→ 删除。
  盘点单审核会按盈亏生成其他出入库单，这里不测（待实测后再加）。

挑单与回读 SQL 在 stock_other_docs_sql。接线：SM_BUILDERS / SM_PACKS / has_sm / sm_sql / sm_samples
汇到 wiring 模块，sm_cases() 进 catalog；SM_CLEANUP 是用例中途失败时的补偿（弃审 + 按新建的主键删除）。
"""

from __future__ import annotations

import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_made, keep_step, pred_step, stash_step
from co.e2e.casespec.qm_write import _compensate

_CREATE = "/v1/vouchers/create"
_UPDATE = "/v1/vouchers/update"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_ROUTES = (_CREATE, _UPDATE, _VERIFY, _DELETE)
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_TOKEN = re.compile(r"^[0-9A-Za-z._-]{1,30}\Z")
_INT_MAX = 2147483647
MEMO = "CO桥回归"
MEMO_UPD = "CO桥回归改"


# ---- 正文 ----


def _code(env: Env, key: str) -> str:
    raw = (env.bag.get(key) or "").strip()
    if _TOKEN.fullmatch(raw) is None:
        raise ValueError("挑单编码不合法：" + key)
    return raw


def _id(raw: str, label: str) -> int:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError(label)
    return int(raw)


def sm_av_new(env: Env) -> dict:
    wh = _code(env, "sm_av")
    head = {"dAVDate": env.today, "cDepCode": _code(env, "sm_av_dep"), "cAVMemo": MEMO}
    before = {"cInvCode": _code(env, "sm_av_from"), "cWhCode": wh, "bAVType": "转换前", "iGroupNO": 1, "iAVQuantity": 1}
    after = {"cInvCode": _code(env, "sm_av_to"), "cWhCode": wh, "bAVType": "转换后", "iGroupNO": 1, "iAVQuantity": 1}
    return {"type": "shape_change", "head": head, "lines": [before, after]}


def sm_av_upd(env: Env) -> dict:
    line = {"op": "update", "line_id": _id(env.bag.get("sm_av_line", ""), "行 id 不合法"), "cbMemo": MEMO_UPD}
    return {"type": "shape_change", "id": _id(env.focus, "单据 id 不合法"), "head": {"cAVMemo": MEMO_UPD}, "lines": [line]}


def sm_tr_new(env: Env) -> dict:
    head = {"dTVDate": env.today, "cOWhCode": _code(env, "sm_tr"), "cIWhCode": _code(env, "sm_tr_in"), "cTVMemo": MEMO}
    return {"type": "transfer_request", "head": head, "lines": [{"cInvCode": _code(env, "sm_tr_inv"), "iTVQuantity": 1}]}


def sm_tr_upd(env: Env) -> dict:
    changed = {"op": "update", "line_id": _id(env.bag.get("sm_tr_line", ""), "行 id 不合法"), "iTVQuantity": 2}
    added = {"op": "add", "cInvCode": _code(env, "sm_tr_inv"), "iTVQuantity": 1}
    return {"type": "transfer_request", "id": _id(env.focus, "单据 id 不合法"), "lines": [changed, added]}


def sm_cv_new(env: Env) -> dict:
    # 只给实盘数量，账面数量由桥按现存量填。
    head = {"cWhCode": _code(env, "sm_cv"), "dCVDate": env.today, "cCVMemo": MEMO}
    return {"type": "stock_check", "head": head, "lines": [{"cInvCode": _code(env, "sm_cv_inv"), "iCVCQuantity": 1}]}


SM_BUILDERS = {
    "sm_av_new": sm_av_new,
    "sm_av_upd": sm_av_upd,
    "sm_tr_new": sm_tr_new,
    "sm_tr_upd": sm_tr_upd,
    "sm_cv_new": sm_cv_new,
}


# ---- 用例 ----


def _ref(kind: str, *extra: tuple[str, str]) -> tuple:
    return (("type", kind), ("id", "$id")) + extra


def _made(kind: str) -> tuple:
    return (("type", kind), ("code", "$nonempty"), ("state.verified", "false"))


_OK = (("ok", "true"),)
_GONE = (("deleted", "true"),)


def _shape_case() -> CaseSpec:
    kind = "shape_change"
    steps = (
        Step("create", sql_name="sm_av_new", checks=_made(kind)),
        keep_step("sm_av_id"),
        pred_step("sm_av_rows", "eq:2"),
        stash_step("sm_av_line", "sm_av_line"),
        apply_step(_UPDATE, "sm_av_upd", "made", _OK),
        pred_step("sm_av_memo", "eq:1"),
        call_made(_VERIFY, _ref(kind, ("action", "verify")), (("generated#types", "other_out,other_in"),)),
        pred_step("sm_av_kids", "eq:2"),
        call_made(_DELETE, _ref(kind), (), 409, "state_mismatch"),
        call_made(_VERIFY, _ref(kind, ("action", "unverify")), _OK),
        pred_step("sm_av_kids", "eq:0"),
        call_made(_DELETE, _ref(kind), _GONE),
        pred_step("sm_av_gone", "eq:0"),
    )
    title = "形态转换单新建、修改、审核生成其他出入库、已审核删除 409、弃审后删除"
    # 审核生成并扣减现存量（stock），占其他出入库的单据号。
    tags = frozenset(("new:shape_change", "w:shape_change", "new:other_in", "new:other_out", "stock"))
    needs = ("sm_av", "sm_av_from", "sm_av_to", "sm_av_dep")
    return CaseSpec("shape-change-cycle", title, needs, steps, True, optional=True, tags=tags, routes=_ROUTES)


def _request_case() -> CaseSpec:
    kind = "transfer_request"
    steps = (
        Step("create", sql_name="sm_tr_new", checks=_made(kind)),
        keep_step("sm_tr_id"),
        stash_step("sm_tr_line", "sm_tr_line"),
        apply_step(_UPDATE, "sm_tr_upd", "made", _OK),
        pred_step("sm_tr_rows", "eq:2"),
        pred_step("sm_tr_qty", "eq:3.0000"),
        call_made(_VERIFY, _ref(kind, ("action", "verify")), (("generated#types", ""),)),
        call_made(_DELETE, _ref(kind), (), 409, "state_mismatch"),
        call_made(_VERIFY, _ref(kind, ("action", "unverify")), _OK),
        call_made(_DELETE, _ref(kind), _GONE),
        pred_step("sm_tr_gone", "eq:0"),
    )
    title = "调拨申请单新建、改数量加行、审核（不生单）、已审核删除 409、弃审后删除"
    tags = frozenset(("new:transfer_request", "w:transfer_request"))
    needs = ("sm_tr", "sm_tr_in", "sm_tr_inv")
    return CaseSpec("transfer-request-cycle", title, needs, steps, True, optional=True, tags=tags, routes=_ROUTES)


def _check_case() -> CaseSpec:
    steps = (
        Step("create", sql_name="sm_cv_new", checks=_made("stock_check")),
        keep_step("sm_cv_id"),
        pred_step("sm_cv_book", "eq:1"),
        Step("create", status=409, code="state_mismatch", sql_name="sm_cv_new"),
        call_made(_DELETE, _ref("stock_check"), _GONE),
        pred_step("sm_cv_gone", "eq:0"),
    )
    title = "盘点单新建（账面数量按现存量）、同仓库再建 409、删除"
    # 盘点期间 U8 可能按仓库冻结出入库，保守带上 stock。
    tags = frozenset(("new:stock_check", "w:stock_check", "stock"))
    needs = ("sm_cv", "sm_cv_inv")
    return CaseSpec("stock-check-create-delete", title, needs, steps, True, optional=True, tags=tags, routes=_ROUTES)


# ---- 补偿 ----


def sm_av_compensate(env: Env, _case: str) -> str:
    return _compensate(env, (("sm_av_id", "shape_change", True),))


def sm_tr_compensate(env: Env, _case: str) -> str:
    return _compensate(env, (("sm_tr_id", "transfer_request", True),))


def _stray_checks(env: Env) -> list[str]:
    # 第二次新建本应 409；万一成功了，它的 id 没记下。挑单保证该仓库原本没有未审核的盘点单，
    # 所以按仓库列出本操作员建的未审核盘点单（新建的那张除外），一并删掉，免得堵住这个仓库。
    from co.e2e.casespec.steps import auth_fields, invoke

    wh = (env.bag.get("sm_cv") or "").strip()
    if _TOKEN.fullmatch(wh) is None:
        return []
    fields = auth_fields(env)
    fields.update({"type": "stock_check", "filter": {"wh_code": wh, "verified": False}, "limit": 20})
    status, payload = invoke(env.client, "/v1/vouchers/list", fields)
    items = payload.get("items") if status == 200 else None
    if not isinstance(items, list):
        return []
    made = env.bag.get("sm_cv_id", "")
    name = env.operator_name.strip()
    found = []
    for item in items:
        raw = str(item.get("id", "")) if isinstance(item, dict) else ""
        maker = str(item.get("maker") or "").strip() if isinstance(item, dict) else ""
        if _ID.fullmatch(raw) and raw != made and (name == "" or maker == name):
            found.append(raw)
    return found


def sm_cv_compensate(env: Env, _case: str) -> str:
    plan = [("sm_cv_id", "stock_check", False)]
    try:
        strays = _stray_checks(env)
    except (U8CoError, OSError, RuntimeError, ValueError) as exc:
        strays = []
        plan_note = f"列出多建的盘点单出错：{str(exc)[:60]}"
    else:
        plan_note = ""
    for n, raw in enumerate(strays):
        key = f"sm_cv_x{n}"
        env.bag[key] = raw
        plan.append((key, "stock_check", False))
    text = _compensate(env, tuple(plan))
    return text + ("；" + plan_note if plan_note else "")


SM_CLEANUP = {
    "shape-change-cycle": sm_av_compensate,
    "transfer-request-cycle": sm_tr_compensate,
    "stock-check-create-delete": sm_cv_compensate,
}


def sm_cases() -> list[CaseSpec]:
    return [_shape_case(), _request_case(), _check_case()]
