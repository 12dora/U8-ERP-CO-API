"""生产订单新增（U8API MOrderAdd，自动编号、按标准 BOM 展开子件）、审核后删除 409、弃审后删除、读取 404；
以及新增的拒绝路径（不存在的存货、完工早于开工）。

挑单与回读 SQL 在 mo_create_sql（存货、部门、订单类别都从测试账套挑，不写死）。
接线：MOC_BUILDERS / MOC_PACKS / has_moc / moc_sql / moc_samples 汇到 wiring 模块，moc_cases() 进 catalog；
MOC_CLEANUP 是用例中途失败时的补偿（弃审 + 按新建的 id 删除），由 runner 在该用例不是 PASS 时调用。
"""

from __future__ import annotations

import datetime as dt

from co.client.u8co_client import U8CoError
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, call_made, pred_step, stash_step
from co.e2e.casespec.mo_create_sql import MOC_STASH

_KIND = "production_order"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_LOAD = "/v1/vouchers/load"
_NEEDS = ("moc_inv", "moc_dept", "moc_type")
# 测试账套里不会有的存货编码（桥在调用 U8 之前按 Inventory 查，400）。
_NO_INV = "U8CO-NO-INV"
MEMO = "u8co e2e 生产订单"


# ---- 正文 ----


def _day(env: Env, shift: int) -> str:
    return (dt.date.fromisoformat(env.today) + dt.timedelta(days=shift)).isoformat()


def _line(env: Env, inv: str, due_shift: int) -> dict:
    return {
        "inv_code": inv,
        "qty": 1,
        "start_date": env.today,
        "due_date": _day(env, due_shift),
        "mo_type": env.bag["moc_type"],
        "dept_code": env.bag["moc_dept"],
    }


def _bom_ok(env: Env) -> None:
    # 挑单拿不到 env.today：挑到的标准 BOM 有效期在这里按开工日期（env.today）核对，不在期内就不建单。
    start, end = env.bag.get("moc_bom_from", ""), env.bag.get("moc_bom_to", "")
    if not (start and end and start <= env.today <= _day(env, 2) <= end):
        raise ValueError(f"挑到的标准 BOM 有效期 {start or '?'}～{end or '?'} 不含 {env.today}")


def moc_new(env: Env) -> dict:
    # 不带 mo_code：U8 按 MO21 规则自动编号。
    _bom_ok(env)
    return {"type": _KIND, "head": {"remark": MEMO}, "lines": [_line(env, env.bag["moc_inv"], 2)]}


def moc_bad_inv(env: Env) -> dict:
    return {"type": _KIND, "head": {}, "lines": [_line(env, _NO_INV, 2)]}


def moc_bad_due(env: Env) -> dict:
    return {"type": _KIND, "head": {}, "lines": [_line(env, env.bag["moc_inv"], -1)]}


MOC_BUILDERS = {"moc_new": moc_new, "moc_bad_inv": moc_bad_inv, "moc_bad_due": moc_bad_due}


# ---- 用例 ----


def _ref() -> tuple:
    return (("type", _KIND), ("id", "$id"))


def _create_delete() -> CaseSpec:
    # 新增（自动编号、未审核、子件已展开）→ 读取 → 审核 → 删除 409 → 弃审 → 删除 → 读取 404，库里不留行。
    made = (
        ("type", _KIND),
        ("code", "$nonempty"),
        ("lines", "1"),
        ("allocates", "$gt0"),
        ("state.verified", "false"),
    )
    ref = _ref()
    steps = (
        Step("create", sql_name="moc_new", checks=made),
        call_made(_LOAD, ref, (("state.verified", "false"),)),
        pred_step("moc_open", "eq:1", "made"),
        pred_step("moc_alloc", "gt0", "made"),
    )
    # 删除前记下明细和子件主键，删除后按它们查 mom_moallocate / mom_moallocatesub 残留。
    steps += tuple(stash_step(name, name) for name in MOC_STASH)
    steps += (
        call_made(_VERIFY, ref + (("action", "verify"),), (("state.verified", "true"),)),
        call_made(_DELETE, ref, (), 409, "state_mismatch"),
        call_made(_VERIFY, ref + (("action", "unverify"),), (("state.verified", "false"),)),
        pred_step("moc_open", "eq:1", "made"),
        call_made(_DELETE, ref, (("deleted", "true"),)),
        call_made(_LOAD, ref, (), 404, "not_found"),
        pred_step("moc_gone", "eq:0", "made"),
    )
    title = "生产订单新增（自动编号、BOM 展开）、审核后删除 409、弃审后删除"
    # bom：按 BOM 展开的生产订单与改 BOM 版本（bom-lifecycle 会改来源版本失效日期）不同时跑。
    return CaseSpec("mo-create-delete", title, _NEEDS, steps, True, optional=True, tags=frozenset(("bom",)))


def _create_bad() -> CaseSpec:
    # 调用 U8 之前就拒绝，不会建出订单。
    steps = (
        Step("create", status=400, code="bad_request", sql_name="moc_bad_inv"),
        Step("create", status=400, code="bad_request", sql_name="moc_bad_due"),
    )
    title = "生产订单新增拒绝：存货不存在、完工日期早于开工日期"
    # 被拒的新建也按新建生产订单保守标记（万一桥放行会占单据号）。
    tags = frozenset(("bom", "new:production_order", "w:production_order", "stock"))
    return CaseSpec("mo-create-bad", title, _NEEDS, steps, True, optional=True, tags=tags)


# ---- 补偿 ----


def _send(env: Env, route: str, extra: dict) -> int:
    # steps 经 wiring 引用本模块的构造器，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.steps import auth_fields, invoke

    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, route, fields)[0]


def moc_compensate(env: Env, case: str) -> str:
    # 尽力而为：用例中途失败时按新建的 MoId 弃审（未审核时 409，忽略）再删除，返回一句说明拼进失败信息。
    made = env.made.get(case, "")
    if not made.isdigit() or made == "0":
        return "没有新建的生产订单"
    ref = {"type": _KIND, "id": int(made)}
    try:
        if _send(env, _LOAD, ref) == 404:
            return "生产订单 " + made + " 已不在"
        _send(env, _VERIFY, dict(ref, action="unverify"))
        status = _send(env, _DELETE, ref)
    except (U8CoError, OSError, RuntimeError, ValueError) as exc:
        return "补偿删除生产订单 " + made + " 出错：" + str(exc)[:80]
    if status == 200:
        return "已补偿删除生产订单 " + made
    return f"补偿删除生产订单 {made} 返回 {status}，留给快照还原"


MOC_CLEANUP = {"mo-create-delete": moc_compensate}


def moc_cases() -> list[CaseSpec]:
    return [_create_delete(), _create_bad()]
