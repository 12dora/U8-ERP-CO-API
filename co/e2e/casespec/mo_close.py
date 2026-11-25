"""生产订单按行关闭、再关 409、打开，回读行状态和关闭人，其他行不动。

挑单与回读 SQL 在 mo_close_sql；接线（catalog、picks、steps._make、steps_sql 的回读）由调用方统一做：
mo_cases() 进用例登记，MO_BUILDERS 进正文构造器，mo_close_sql 的 MO_PACKS / has_mo / mo_sql / mo_samples 同 pu_return_gen。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, apply_step, pred_step, stash_step
from co.e2e.casespec.mo_close_sql import safe_id

_CLOSE = "/v1/vouchers/close"
_KIND = "production_order"


# ---- 正文 ----


def _line_body(env: Env, action: str) -> dict:
    line = int(safe_id(env.bag.get("mo_line", "")))
    return {"type": _KIND, "id": int(safe_id(env.focus)), "action": action, "line_ids": [line]}


def mo_close_line(env: Env) -> dict:
    return _line_body(env, "close")


def mo_open_line(env: Env) -> dict:
    return _line_body(env, "open")


MO_BUILDERS = {"mo_close_line": mo_close_line, "mo_open_line": mo_open_line}


# ---- 用例 ----


def _mo_close_cycle() -> CaseSpec:
    # 关闭前行是已审核未关闭；关闭后 Status=4、关闭人有值；再关 409；打开后 Status=3、关闭人清空；其他行前后不变。
    shut = (("type", _KIND), ("action", "close"), ("changed", "1"), ("closed_by", "$nonempty"))
    opened = (("type", _KIND), ("action", "open"), ("changed", "1"))
    steps = (
        stash_step("mo_others", "mo_others", "need"),
        pred_step("mo_open", "eq:1", "need"),
        apply_step(_CLOSE, "mo_close_line", "need", shut),
        pred_step("mo_closed", "eq:1", "need"),
        apply_step(_CLOSE, "mo_close_line", "need", (), (409, "state_mismatch")),
        apply_step(_CLOSE, "mo_open_line", "need", opened),
        pred_step("mo_open", "eq:1", "need"),
        pred_step("mo_others", "same:mo_others", "need"),
    )
    title = "生产订单按行关闭、再关 409、打开，行状态和关闭人回读"
    return CaseSpec("production-order-close-cycle", title, ("mo_src", "mo_line"), steps, True, optional=True)


def mo_cases() -> list[CaseSpec]:
    return [_mo_close_cycle()]
