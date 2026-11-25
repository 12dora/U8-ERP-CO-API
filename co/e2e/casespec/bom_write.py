"""物料清单（bom）新增版本（行 10 带一条替代料）、读取、修改（改用量 + 删一行 + 加一行，不送替代料：保留）、
替换替代料、清空替代料、加回替代料和定位符、审核、审核后删除 409、弃审、删除（替代料、定位符不留）、读取 404；
以及新增的拒绝路径（不存在的子件 400、重复的版本号 409），都在调用 U8 之前拒绝。

挑单与回读 SQL 在 bom_write_sql（母件和三个子件从它现有的最新标准 BOM 挑，不写死）。新版本用远期生效日期，
审核时 U8 顺延旧版本失效日期也只影响那之后；弃审时 U8 按删除版本的规则把它恢复（BOStateProcess._Unauditing →
__UpdVersionEndDateByDel），用例在新建后记下来源版本（挑单的最新已审核版本）的失效日期，删除后核对没变。
拒绝路径 `bom-create-bad` 用另一个远期日期，生命周期用例留下的版本不会让它先撞上生效日期 409。
接线：BOMW_BUILDERS / BOMW_PACKS / has_bomw / bomw_sql / bomw_samples 汇到 wiring 模块，bomw_cases() 进 catalog；
BOMW_CLEANUP 是用例中途失败时的补偿（弃审 + 按新建的 BomId 删除），由 runner 在该用例不是 PASS 时调用。
"""

from __future__ import annotations

import datetime as dt

from co.client.u8co_client import U8CoError
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_made, pred_step, stash_step
from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.bom_write_sql import BOMW_SRC_STASH, BOMW_STASH

_KIND = "bom"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_LOAD = "/v1/vouchers/load"
_UPDATE = "/v1/vouchers/update"
_NEEDS = ("bomw_parent", "bomw_c1", "bomw_c2", "bomw_c3", "bomw_ver", "bomw_sub", "bomw_subp", "bomw_c2p")
# 测试账套里不会有的存货编码（桥在调用 U8 之前按 Inventory 查，400）。
_NO_INV = "U8CO-NO-INV"
# 新版本的生效日期：登录日期之后约 8 年，挑单保证母件现有版本都早于它。
_AHEAD = 3000
# 拒绝用例的生效日期：与生命周期用例错开一周。
_AHEAD_BAD = _AHEAD + 7
MEMO = "u8co e2e 物料清单"
# 加回替代料时同时加的定位符（测试数据占位文本）。
LOC = "DEMO-001"


# ---- 正文 ----


def _eff(env: Env, days: int = _AHEAD) -> str:
    return (dt.date.fromisoformat(env.today) + dt.timedelta(days=days)).isoformat()


def _head(env: Env, days: int = _AHEAD, **extra: object) -> dict:
    head = {"inv_code": env.bag["bomw_parent"], "version_desc": MEMO, "eff_date": _eff(env, days)}
    head.update(extra)
    return head


def _lines(env: Env, second: str) -> list[dict]:
    return [
        {"inv_code": env.bag["bomw_c1"], "base_qty_n": 1, "base_qty_d": 1, "sort_seq": 10},
        {"inv_code": second, "base_qty_n": 1, "base_qty_d": 1, "sort_seq": 20},
    ]


def bomw_new(env: Env) -> dict:
    # 不带 version：桥取该母件的最大版本加版本增量。行 10 带一条替代料（优先级、系数取缺省 1）。
    lines = _lines(env, env.bag["bomw_c2"])
    lines[0]["substitutes"] = [{"inv_code": env.bag["bomw_sub"]}]
    return {"type": _KIND, "head": _head(env), "lines": lines}


def bomw_swap(env: Env) -> dict:
    # 替换行 10 的替代料：换成子件 2（修改时它的行已删），优先级 2、系数 2。
    sub = {"inv_code": env.bag["bomw_c2"], "sequence": 2, "factor": 2}
    lines = [{"op": "update", "sort_seq": 10, "substitutes": [sub]}]
    return {"type": _KIND, "id": int(safe_id(env.focus)), "lines": lines}


def bomw_clear(env: Env) -> dict:
    # 清空行 10 的替代料（空数组）。
    lines = [{"op": "update", "sort_seq": 10, "substitutes": []}]
    return {"type": _KIND, "id": int(safe_id(env.focus)), "lines": lines}


def bomw_readd(env: Env) -> dict:
    # 清空后重新给行 10 加回原替代料和一个定位符，审核、删除时清单上带着替代料和定位符。
    sub = {"inv_code": env.bag["bomw_sub"]}
    lines = [{"op": "update", "sort_seq": 10, "substitutes": [sub], "locators": [{"loc": LOC}]}]
    return {"type": _KIND, "id": int(safe_id(env.focus)), "lines": lines}


def bomw_edit(env: Env) -> dict:
    # 行 10 用量分子 1 → 2，删行 20，加第三个子件（行号缺省接在 20 后面，即 30）。
    lines = [
        {"op": "update", "sort_seq": 10, "base_qty_n": 2},
        {"op": "delete", "sort_seq": 20},
        {"op": "add", "inv_code": env.bag["bomw_c3"], "base_qty_n": 1},
    ]
    return {"type": _KIND, "id": int(safe_id(env.focus)), "head": {"version_desc": MEMO + " 改"}, "lines": lines}


def bomw_bad_inv(env: Env) -> dict:
    return {"type": _KIND, "head": _head(env, _AHEAD_BAD), "lines": _lines(env, _NO_INV)}


def bomw_dup_ver(env: Env) -> dict:
    version = int(safe_id(env.bag["bomw_ver"]))
    return {"type": _KIND, "head": _head(env, _AHEAD_BAD, version=version), "lines": _lines(env, env.bag["bomw_c2"])}


BOMW_BUILDERS = {
    "bomw_new": bomw_new,
    "bomw_edit": bomw_edit,
    "bomw_swap": bomw_swap,
    "bomw_clear": bomw_clear,
    "bomw_readd": bomw_readd,
    "bomw_bad_inv": bomw_bad_inv,
    "bomw_dup_ver": bomw_dup_ver,
}


# ---- 用例 ----


def _ref() -> tuple:
    return (("type", _KIND), ("id", "$id"))


# 新建后读取：行 10 带一条替代料，行 20 没有。
_SUBS_LOADED = (("lines.0.substitutes#len", "1"), ("lines.1.substitutes#len", "0"))
# 加回后读取：行 10 一条替代料、一个定位符。
_READDED = (("lines.0.substitutes#len", "1"), ("lines.0.locators#len", "1"), ("lines.0.locators.0.loc", LOC))


def _lifecycle() -> CaseSpec:
    made = (
        ("type", _KIND),
        ("code", "$nonempty"),
        ("version", "$gt0"),
        ("lines", "2"),
        ("state.verified", "false"),
    )
    ref = _ref()
    steps = (
        Step("create", sql_name="bomw_new", checks=made),
    )
    # 新建之后、审核之前记下来源版本的 BomId 和失效日期（审核新版本时 U8 会改它，弃审应当恢复）。
    steps += tuple(stash_step(name, name) for name in BOMW_SRC_STASH)
    steps += (
        call_made(_LOAD, ref, (("state.verified", "false"),) + _SUBS_LOADED),
        pred_step("bomw_status", "eq:1", "made"),
        pred_step("bomw_sub_kept", "eq:1", "made"),
        apply_step(_UPDATE, "bomw_edit", "made", (("type", _KIND), ("lines", "2"))),
        pred_step("bomw_edited", "eq:1", "made"),
        pred_step("bomw_sub_kept", "eq:1", "made"),
        apply_step(_UPDATE, "bomw_swap", "made", (("type", _KIND), ("lines", "2"))),
        pred_step("bomw_sub_swap", "eq:1", "made"),
        apply_step(_UPDATE, "bomw_clear", "made", (("type", _KIND), ("lines", "2"))),
        pred_step("bomw_sub_n", "eq:0", "made"),
        apply_step(_UPDATE, "bomw_readd", "made", (("type", _KIND), ("lines", "2"))),
        call_made(_LOAD, ref, _READDED),
        pred_step("bomw_sub_kept", "eq:1", "made"),
        pred_step("bomw_loc_n", "eq:1", "made"),
    )
    # 删除前记下子件选项行的主键范围，删除后按它查 bom_opcomponentopt 残留。
    steps += tuple(stash_step(name, name) for name in BOMW_STASH)
    steps += (
        call_made(_VERIFY, ref + (("action", "verify"),), (("state.verified", "true"),)),
        pred_step("bomw_status", "eq:3", "made"),
        call_made(_DELETE, ref, (), 409, "state_mismatch"),
        call_made(_VERIFY, ref + (("action", "unverify"),), (("state.verified", "false"),)),
        pred_step("bomw_status", "eq:1", "made"),
        call_made(_DELETE, ref, (("deleted", "true"),)),
        call_made(_LOAD, ref, (), 404, "not_found"),
        pred_step("bomw_gone", "eq:0", "made"),
        pred_step("bomw_src_end", "same:bomw_send", "made"),
    )
    title = (
        "物料清单新增版本（带替代料）、修改（改用量、删行、加行，替代料保留）、替换、清空、加回替代料和定位符、"
        "带替代料审核后删除 409、弃审后删除（替代料、定位符不留），来源版本失效日期复原"
    )
    return CaseSpec("bom-lifecycle", title, _NEEDS, steps, True, optional=True, tags=frozenset(("bom",)))


def _create_bad() -> CaseSpec:
    # 调用 U8 之前就拒绝，不会建出版本。
    steps = (
        Step("create", status=400, code="bad_request", sql_name="bomw_bad_inv"),
        Step("create", status=409, code="state_mismatch", sql_name="bomw_dup_ver"),
    )
    title = "物料清单新增拒绝：子件不存在 400、版本号重复 409"
    return CaseSpec("bom-create-bad", title, _NEEDS, steps, True, optional=True, tags=frozenset(("bom", "new:bom", "w:bom")))


# ---- 补偿 ----


def _send(env: Env, route: str, extra: dict) -> int:
    # steps 经 wiring 引用本模块的构造器，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.steps import auth_fields, invoke

    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, route, fields)[0]


def bomw_compensate(env: Env, case: str) -> str:
    # 尽力而为：用例中途失败时按新建的 BomId 弃审（未审核时 409，忽略）再删除，返回一句说明拼进失败信息。
    made = env.made.get(case, "")
    if not made.isdigit() or made == "0":
        return "没有新建的物料清单"
    ref = {"type": _KIND, "id": int(made)}
    try:
        if _send(env, _LOAD, ref) == 404:
            return "物料清单 " + made + " 已不在"
        _send(env, _VERIFY, dict(ref, action="unverify"))
        status = _send(env, _DELETE, ref)
    except (U8CoError, OSError, RuntimeError, ValueError) as exc:
        return "补偿删除物料清单 " + made + " 出错：" + str(exc)[:80]
    if status == 200:
        return "已补偿删除物料清单 " + made
    return f"补偿删除物料清单 {made} 返回 {status}，留给快照还原"


BOMW_CLEANUP = {"bom-lifecycle": bomw_compensate}


def bomw_cases() -> list[CaseSpec]:
    return [_lifecycle(), _create_bad()]
