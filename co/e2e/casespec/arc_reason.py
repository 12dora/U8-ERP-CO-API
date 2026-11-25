"""原因码档案写入（桥 ArcReason）：新增、修改、删除都走 U8 官方 EAI（U8SrvTrans.IClsCommon，根标签 reason）。

编码用 T 开头的时间戳（9 个字符，原因码编码最长 10），先读一次确认不存在，依次：新增（所属类型 1 不良品原因）→ 读取 →
改名称和说明 → 读取 → 删除 → 读取 404。新编码没有任何单据引用，删除前的引用检查必然放行。
步骤 kind 为 "custom"，sql_name 是操作名（REASON_OPS 并入 ops_run）。中途失败时尽力删掉本用例建的原因码。
"""

from __future__ import annotations

import datetime as dt

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_write_more import _cleanup, _expect, _read_problem, _send

_ROUTES = tuple("/v1/archives/" + op for op in ("create", "get", "update", "delete"))
_ARCHIVE = "reason"
_TYPE = "Reasontype"
_MEMO = "ReasonMemo"


def _stamp() -> str:
    return "T" + dt.datetime.now().strftime("%d%H%M%S")


def _reads(env: Env, key: dict, wants: dict) -> str:
    for column, want in wants.items():
        bad = _read_problem(env, key, column, want)
        if bad:
            return bad
    return ""


def _cycle(env: Env, key: dict) -> str:
    made = {"name": "测试不良原因", _TYPE: 1, _MEMO: "e2e 新增"}
    status, payload = _send(env, "create", dict(key, fields=made))
    bad = _expect(status, payload, 200, "新增") or _reads(env, key, {"name": made["name"], _TYPE: 1})
    if bad:
        return bad
    changed = {"name": "测试不良原因改", _MEMO: "e2e 修改"}
    status, payload = _send(env, "update", dict(key, fields=changed))
    bad = _expect(status, payload, 200, "修改") or _reads(env, key, {"name": changed["name"], _MEMO: changed[_MEMO], _TYPE: 1})
    if bad:
        return bad
    status, payload = _send(env, "delete", key)
    bad = _expect(status, payload, 200, "删除")
    if bad or payload.get("deleted") is not True:
        return bad or "删除响应缺 deleted"
    status, payload = _send(env, "get", key)
    return _expect(status, payload, 404, "删除后读取")


def reason_write(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del spec, step
    key = {"archive": _ARCHIVE, "code": _stamp()}
    status, payload = _send(env, "get", key)
    if status != 404:
        return "FAIL", f"新编码 {key['code']} 读取应 404，实际 {status} {payload.get('code')}"[:200]
    problem = _cycle(env, key)
    if problem:
        return "FAIL", (_cleanup(env, _ARCHIVE, key["code"]) + "；" + problem)[:240]
    return "PASS", f"reason {key['code']}"


REASON_OPS = {"reason_write": reason_write}


def reason_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name="reason_write", id_from="none")
    title = "原因码新增、读取、修改、删除（EAI）"
    tags = frozenset({"arc:" + _ARCHIVE})
    return [CaseSpec("archive-reason-write", title, (), (step,), True, tags=tags, routes=_ROUTES)]
