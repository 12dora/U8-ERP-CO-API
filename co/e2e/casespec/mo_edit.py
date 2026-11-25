"""已审核、已领料的生产订单修改（照 U8 客户端「变更」，不弃审）。

新建一张按标准 BOM 展开的生产订单（moc_new）→ 审核，然后：
- mo-update-verified-nodown：没下游时数量翻倍，行仍已审核，子件全部重插（新 AllocateId）、按基本用量重算、关联列不变；
- mo-update-verified-issued：领一个子件（材料出库）后数量翻倍，出库行指向新子件、已领量不变；删掉出库单后已领量回到 0；
- mo-update-issued-below-409：数量 2 时领满一个子件，再改回 1（需求少于已领）409，出库行不动；
- mo-update-closed-409：关闭后修改 409，打开后清理。
各用例最后弃审、删除订单（领了料的先删出库单）。回读 SQL 在 mo_edit_sql，经 mo_update 的 MOU_BUILDERS /
_READBACK / mou_cases / MOU_CLEANUP 接线。
"""

from __future__ import annotations

import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.assertutil import as_num
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_from, call_made, keep_step, pred_step, stash_step
from co.e2e.casespec.mo_create import moc_compensate
from co.e2e.testdata import MATERIAL_OUT_RD
from co.e2e.testdata import value as td

_KIND = "production_order"
_UPDATE = "/v1/vouchers/update"
_DELETE = "/v1/vouchers/delete"
_VERIFY = "/v1/vouchers/verify"
_CLOSE = "/v1/vouchers/close"
_GEN = "/v1/vouchers/generate"
_NEEDS = ("moc_inv", "moc_dept", "moc_type")
MEMO_ISSUE = "u8co e2e 生产订单修改领料"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_NUM = re.compile(r"^[0-9]+(\.[0-9]+)?\Z")
_WH = re.compile(r"^[0-9A-Za-z._-]{1,20}\Z")
_NAMES = ("mo-update-verified-nodown", "mo-update-verified-issued", "mo-update-issued-below-409", "mo-update-closed-409")


# ---- 正文 ----


def _id(raw: str, what: str) -> int:
    if _ID.fullmatch(raw or "") is None or int(raw) > 2147483647:
        raise ValueError(what)
    return int(raw)


def wh_decode(raw: str) -> str:
    # mo_edit_sql 把仓库编码按字符 3 位 ASCII 码写进 bag，这里还原并核对字符集。
    text = (raw or "").strip()
    if not text.isdigit() or len(text) % 3 != 0:
        raise ValueError("仓库编码不合法")
    code = "".join(chr(int(text[i : i + 3])) for i in range(0, len(text), 3))
    if _WH.fullmatch(code) is None:
        raise ValueError("仓库编码不合法")
    return code


def _issue(prefix: str):
    # 按挑出的子件（<prefix>aid）、数量（<prefix>qty，领满整个子件）、仓库（<prefix>wh）从本订单生成材料出库单。
    def build(env: Env) -> dict:
        qty = (env.bag.get(prefix + "qty") or "").strip()
        if _NUM.fullmatch(qty) is None or float(qty) <= 0:
            raise ValueError("数量不合法")
        line = {"source_line_id": _id(env.bag.get(prefix + "aid", ""), "子件 id 不合法"), "quantity": as_num(qty)}
        head = {"cWhCode": wh_decode(env.bag.get(prefix + "wh", "")), "cRdCode": td(MATERIAL_OUT_RD), "dDate": env.today,
                "cMemo": MEMO_ISSUE}
        return {"type": "material_out", "id": _id(env.focus, "单据 id 不合法"), "head": head, "lines": [line]}

    return build


def _close(action: str):
    def build(env: Env) -> dict:
        line = _id(env.bag.get("mou_line", ""), "行 id 不合法")
        return {"type": _KIND, "id": _id(env.focus, "单据 id 不合法"), "action": action, "line_ids": [line]}

    return build


def mou_one(env: Env) -> dict:
    # 数量改回 1（领料时是 2）：子件需求减半，少于已领。
    line = {"op": "update", "line_id": _id(env.bag.get("mou_line", ""), "行 id 不合法"), "qty": 1}
    return {"type": _KIND, "id": _id(env.focus, "单据 id 不合法"), "lines": [line]}


MOE_BUILDERS = {
    "mou_one": mou_one,
    "moi_issue": _issue("moi_"),
    "mob_issue": _issue("mob_"),
    "mou_close": _close("close"),
    "mou_open": _close("open"),
}


# ---- 用例 ----


def _ref() -> tuple:
    return (("type", _KIND), ("id", "$id"))


_SAVED = (("ok", "true"), ("changed", "1"), ("allocates", "$gt0"), ("state.verified", "true"))
_MADE = (("type", _KIND), ("code", "$nonempty"), ("lines", "1"), ("allocates", "$gt0"))
_OUT = (("type", "material_out"), ("code", "$nonempty"))


def _start() -> tuple[Step, ...]:
    return (
        Step("create", sql_name="moc_new", checks=_MADE),
        call_made(_VERIFY, _ref() + (("action", "verify"),), (("state.verified", "true"),)),
        stash_step("mou_line", "mou_line"),
    )


def _finish(src: str) -> tuple[Step, ...]:
    # 弃审、删除订单，回读已删；src 是订单 id 的来源（made 或 bag:<键>）。
    return (
        call_from(_VERIFY, _ref() + (("action", "unverify"),), src, (("state.verified", "false"),)),
        call_from(_DELETE, _ref(), src, (("deleted", "true"),)),
        pred_step("mou_gone", "eq:0", src),
    )


def _pick(prefix: str) -> tuple[Step, ...]:
    return (
        stash_step("mou_pick_aid", prefix + "aid"),
        stash_step("mou_pick_qty", prefix + "qty"),
        stash_step("mou_pick_wh", prefix + "wh"),
    )


def _issue_steps(prefix: str) -> tuple[Step, ...]:
    # 领料前记下订单 id；生成后 made 变成出库单，记下它；之后订单一律按 bag:<prefix>mo 取。
    return (
        apply_step(_GEN, prefix + "issue", "bag:" + prefix + "mo", _OUT),
        keep_step(prefix + "out"),
    )


def _nodown() -> CaseSpec:
    steps = _start() + (
        stash_step("mou_qty", "mou_qty"),
        stash_step("mou_acnt", "mou_acnt"),
        stash_step("mou_digest", "mou_digest"),
        stash_step("mou_maxid", "mou_maxid"),
        apply_step(_UPDATE, "mou_double", "made", _SAVED),
        pred_step("mou_verified", "eq:1"),
        pred_step("mou_qty", "double:mou_qty"),
        pred_step("mou_acnt", "same:mou_acnt"),
        pred_step("mou_scaled", "eq:1"),
        pred_step("mou_digest", "same:mou_digest"),
        pred_step("mou_newids", "eq:1"),
    ) + _finish("made")
    title = "已审核生产订单修改（没下游）：数量翻倍，仍已审核，子件重插并按基本用量重算"
    return CaseSpec(_NAMES[0], title, _NEEDS, steps, True, optional=True, tags=frozenset(("bom",)))


def _issued() -> CaseSpec:
    mo = "bag:moi_mo"
    out_ref = (("type", "material_out"), ("id", "$id"))
    steps = (_start()[0], keep_step("moi_mo")) + _start()[1:] + (stash_step("mou_qty", "mou_qty"),) + _pick("moi_")
    steps += _issue_steps("moi_") + (
        stash_step("mou_iss", "moi_iss", mo),
        pred_step("mou_iss", "gt0", mo),
        pred_step("moi_keep", "eq:1", mo),
        apply_step(_UPDATE, "mou_double", mo, _SAVED),
        pred_step("moi_link", "eq:1", mo),
        pred_step("mou_iss", "same:moi_iss", mo),
        pred_step("mou_qty", "double:mou_qty", mo),
        pred_step("mou_verified", "eq:1", mo),
        call_from(_DELETE, out_ref, "bag:moi_out", (("deleted", "true"),)),
        pred_step("mou_iss0", "eq:1", mo),
    ) + _finish(mo)
    title = "已审核、已领料生产订单修改：数量翻倍后材料出库行指向新子件、已领量不变，删出库单后已领量回 0"
    tags = frozenset(("bom", "new:material_out", "w:material_out", "stock"))
    return CaseSpec(_NAMES[1], title, _NEEDS, steps, True, optional=True, tags=tags, data=(MATERIAL_OUT_RD,))


def _below() -> CaseSpec:
    mo = "bag:mob_mo"
    out_ref = (("type", "material_out"), ("id", "$id"))
    steps = (_start()[0], keep_step("mob_mo")) + _start()[1:]
    steps += (apply_step(_UPDATE, "mou_double", "made", _SAVED),) + _pick("mob_") + _issue_steps("mob_")
    steps += (
        apply_step(_UPDATE, "mou_one", mo, (), (409, "state_mismatch")),
        pred_step("mob_keep", "eq:1", mo),
        call_from(_DELETE, out_ref, "bag:mob_out", (("deleted", "true"),)),
    ) + _finish(mo)
    title = "已领料生产订单改小数量（需求少于已领）409，材料出库行不动"
    tags = frozenset(("bom", "new:material_out", "w:material_out", "stock"))
    return CaseSpec(_NAMES[2], title, _NEEDS, steps, True, optional=True, tags=tags, data=(MATERIAL_OUT_RD,))


def _closed() -> CaseSpec:
    steps = _start() + (
        apply_step(_CLOSE, "mou_close", "made", (("action", "close"),)),
        apply_step(_UPDATE, "mou_double", "made", (), (409, "state_mismatch")),
        apply_step(_CLOSE, "mou_open", "made", (("action", "open"),)),
    ) + _finish("made")
    title = "已关闭生产订单修改 409（先打开），打开后弃审删除"
    return CaseSpec(_NAMES[3], title, _NEEDS, steps, True, optional=True, tags=frozenset(("bom",)))


# ---- 补偿 ----


def _send(env: Env, route: str, extra: dict) -> int:
    # steps 经 wiring 引用本模块的构造器，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.steps import auth_fields, invoke

    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, route, fields)[0]


def _drop_out(prefix: str):
    # 领了料的用例：先删本用例生成的材料出库单（bag <prefix>out），再按订单 id（bag <prefix>mo）弃审、删除。
    def compensate(env: Env, case: str) -> str:
        notes = []
        out = env.bag.get(prefix + "out", "")
        if _ID.fullmatch(out or ""):
            try:
                status = _send(env, _DELETE, {"type": "material_out", "id": int(out)})
                notes.append(f"删除材料出库单 {out} 返回 {status}")
            except (U8CoError, OSError, RuntimeError, ValueError) as exc:
                notes.append("删除材料出库单 " + out + " 出错：" + str(exc)[:60])
        mo = env.bag.get(prefix + "mo", "")
        if _ID.fullmatch(mo or ""):
            env.made[case] = mo
        notes.append(moc_compensate(env, case))
        return "；".join(notes)

    return compensate


def _reopen(env: Env, case: str) -> str:
    # 关闭用例：先尽力打开（未关闭时 409，忽略），再弃审、删除。
    made, line = env.made.get(case, ""), env.bag.get("mou_line", "")
    if _ID.fullmatch(made or "") and _ID.fullmatch(line or ""):
        try:
            _send(env, _CLOSE, {"type": _KIND, "id": int(made), "action": "open", "line_ids": [int(line)]})
        except (U8CoError, OSError, RuntimeError, ValueError):
            pass
    return moc_compensate(env, case)


MOE_CLEANUP = {
    _NAMES[0]: moc_compensate,
    _NAMES[1]: _drop_out("moi_"),
    _NAMES[2]: _drop_out("mob_"),
    _NAMES[3]: _reopen,
}


def moe_cases() -> list[CaseSpec]:
    return [_nodown(), _issued(), _below(), _closed()]
