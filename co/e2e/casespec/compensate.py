"""用例中途失败时的补偿：按记下的主键，从下游到上游尽力弃审、删除。qm_write、pu_red_chain 共用。"""

from __future__ import annotations

import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.env import Env

_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")


def _send(env: Env, route: str, extra: dict) -> int:
    # steps 经 wiring 引用各模块的构造器，放到调用时再引入，避免循环引用。
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


def compensate(env: Env, plan: tuple[tuple[str, str, bool], ...]) -> str:
    """plan 每项（bag 键, 单据类型, 是否先弃审），按下游到上游排好；返回一句说明拼进失败信息。"""
    notes = [_drop(env, key, kind, unverify) for key, kind, unverify in plan]
    text = "；".join(note for note in notes if note)
    return text or "没有新建的单据"
