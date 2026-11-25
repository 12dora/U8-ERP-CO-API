"""调拨单参照调拨申请单（vouchers/generate，type=transfer，source_type=transfer_request），全程自建自删。

transfer-from-request：新建调拨申请单（申请 3、核准 2）→ 审核 → 超过核准数量生单 409 → 生单 1 个（响应 source_type、
  调拨单行带 iTRIds，申请单行累计调拨 1）→ 申请单有下游时弃审 409 → 删除调拨单（累计调拨回 0）→ 弃审并删除申请单。

挑单与回读 SQL 在 transfer_gen_sql。接线：TG_BUILDERS / TG_PACKS / has_tg / tg_sql / tg_samples 汇到 wiring 模块，
tg_cases() 进 catalog。申请单表头带测试数据里的收发类别（stock.transfer_out_rd / transfer_in_rd，没配置记 SKIP），
生成的调拨单沿用；TG_CLEANUP 是用例中途失败时的补偿：先删指向申请单的全部调拨单（按 iTRIds 查，含意外生成的），
再弃审并删除申请单。
"""

from __future__ import annotations

import re

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_from, call_made, keep_step, pred_step, stash_step
from co.e2e.casespec.qm_write import _compensate
from co.e2e.testdata import value as td

_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_CREATE = "/v1/vouchers/create"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_TOKEN = re.compile(r"^[0-9A-Za-z._-]{1,30}\Z")
_INT_MAX = 2147483647
_REQ, _TV = "bag:tg_req", "bag:tg_tv"
MEMO = "CO桥回归"


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


# 申请单的收发类别从测试数据取（各账套编码不同，桥不补缺省）；生成的调拨单沿用申请单的。没配置时用例 SKIP。
_RD_DATA = ("stock.transfer_out_rd", "stock.transfer_in_rd")


def tg_req_new(env: Env) -> dict:
    head = {"dTVDate": env.today, "cOWhCode": _code(env, "tg_tr"), "cIWhCode": _code(env, "tg_tr_in"), "cTVMemo": MEMO}
    head.update({"cORdCode": td(_RD_DATA[0]), "cIRdCode": td(_RD_DATA[1])})
    line = {"cInvCode": _code(env, "tg_tr_inv"), "iTVQuantity": 3, "iTvChkQuantity": 2}
    return {"type": "transfer_request", "head": head, "lines": [line]}


def _gen(env: Env, qty: int) -> dict:
    line = {"source_line_id": _id(env.bag.get("tg_reql", ""), "行 id 不合法"), "quantity": qty}
    body = {"type": "transfer", "id": _id(env.focus, "单据 id 不合法"), "source_type": "transfer_request"}
    body.update({"head": {"dTVDate": env.today, "cMemo": MEMO}, "lines": [line]})
    return body


def tg_gen(env: Env) -> dict:
    return _gen(env, 1)


def tg_gen_over(env: Env) -> dict:
    # 核准 2、本次 3：超过可调拨数量，桥应 409。
    return _gen(env, 3)


TG_BUILDERS = {"tg_req_new": tg_req_new, "tg_gen": tg_gen, "tg_gen_over": tg_gen_over}


# ---- 用例 ----


def _ref(kind: str, *extra: tuple[str, str]) -> tuple:
    return (("type", kind), ("id", "$id")) + extra


_OK = (("ok", "true"),)
_GONE = (("deleted", "true"),)
_CONFLICT = (409, "state_mismatch")


def _case() -> CaseSpec:
    made = (("type", "transfer"), ("code", "$nonempty"), ("source_type", "transfer_request"), ("lines", "1"))
    steps = (
        Step("create", sql_name="tg_req_new", checks=(("type", "transfer_request"), ("code", "$nonempty"))),
        keep_step("tg_req"),
        stash_step("tg_reql", "tg_reql", _REQ),
        pred_step("tg_chk", "eq:2.0000", _REQ),
        call_made(_VERIFY, _ref("transfer_request", ("action", "verify")), _OK),
        apply_step(_GEN, "tg_gen_over", _REQ, (), _CONFLICT),
        apply_step(_GEN, "tg_gen", _REQ, made),
        keep_step("tg_tv"),
        pred_step("tg_link", "eq:1", _TV),
        pred_step("tg_sum", "eq:1.0000", _REQ),
        call_from(_VERIFY, _ref("transfer_request", ("action", "unverify")), _REQ, (), _CONFLICT),
        call_from(_DELETE, _ref("transfer"), _TV, _GONE),
        pred_step("tg_sum", "eq:0.0000", _REQ),
        call_from(_VERIFY, _ref("transfer_request", ("action", "unverify")), _REQ, _OK),
        call_from(_DELETE, _ref("transfer_request"), _REQ, _GONE),
        pred_step("sm_tr_gone", "eq:0", _REQ),
    )
    title = "调拨单参照调拨申请单：超核准 409、回写累计调拨、有下游不能弃审、删调拨单后回退、删申请单"
    tags = frozenset(("new:transfer_request", "w:transfer_request", "new:transfer", "w:transfer", "stock"))
    needs = ("tg_tr", "tg_tr_in", "tg_tr_inv")
    routes = (_CREATE, _GEN, _VERIFY, _DELETE)
    opts = {"optional": True, "data": _RD_DATA, "tags": tags, "routes": routes}
    return CaseSpec("transfer-from-request", title, needs, steps, True, **opts)


# ---- 补偿 ----


def _kids(env: Env, req: str) -> list[str]:
    # steps 经 wiring 引用本模块，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.assertutil import first_cell
    from co.e2e.casespec.transfer_gen_sql import tg_kids_sql
    from co.e2e.casespec.sqlexec import run_guarded

    cell = first_cell(run_guarded(env, tg_kids_sql(req), "transfer-from-request"))
    return [item for item in cell.split(",") if _ID.fullmatch(item.strip())]


def tg_compensate(env: Env, _case: str) -> str:
    # 先删指向申请单的全部调拨单（未审核，含超量那次意外生成的），再弃审并删除申请单。
    plan = []
    req = env.bag.get("tg_req", "")
    note = ""
    if _ID.fullmatch(req or ""):
        try:
            kids = _kids(env, req)
        except (RuntimeError, ValueError, OSError) as exc:
            kids = []
            note = f"；查调拨单出错：{str(exc)[:60]}"
        for n, raw in enumerate(kids):
            key = f"tg_x{n}"
            env.bag[key] = raw.strip()
            plan.append((key, "transfer", False))
    plan.append(("tg_req", "transfer_request", True))
    return _compensate(env, tuple(plan)) + note


TG_CLEANUP = {"transfer-from-request": tg_compensate}


def tg_cases() -> list[CaseSpec]:
    return [_case()]
