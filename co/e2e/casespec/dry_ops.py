"""写预演自定义步骤（Step.kind 为 "custom"，sql_name 是操作名；DRY_RUN_OPS 并入 ops_run）。

dry_op：写预演。预演前读不到 @w 的单据就 FAIL（不能比较）；万一预演真写了，漏出去的新单据、表头最大主键、锁定的单据
  记进本用例的 bag 键（dry_steps.leak_key 等），失败时 dry_cleanup 按它撤回。
按 fields 拼正文加 dry_run: true 调 step.route，核对 200、dry_run、mode、warnings、docs（写法见 dry_judge），
  再核对「什么都没变」：@w 列的单据预演前后读取完全相同；rollback 预演给出的新单据读取 404；新建 / 生单时该类型表头的
  行数和最大主键（dry_sql.HEADS）预演前后相同。
dry_refuse：按 fields 拼正文（@dry=1 时加 dry_run，@key=1 时加随机幂等键）调 step.route，要求 step.status / step.code，
  message 含 @t，@f 给出时 field 等于它（结构化错误）。
本模块引用 steps，不能被 wiring 链引用。
"""

from __future__ import annotations

import os
import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.assertutil import first_cell, status_problem
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_judge import (
    EXPECTS,
    MODES,
    Want,
    canon,
    cancel_nos,
    first_diff,
    is_creating,
    new_ids,
    parse_watch,
    preview_problem,
    split_fields,
)
from co.e2e.casespec.dry_sql import HEADS, count_sql
from co.e2e.casespec.dry_steps import add_item, hx_key, leak_key, lock_key, max_key
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import _make, auth_fields, focus_id, resolve_token

_LOAD = "/v1/vouchers/load"
_WF_STATE = "/v1/workflow/state"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
LAST_DETAIL = "dry_last_detail"


def send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    """调桥；错误时把 field / hint 也放进返回的字典（steps.invoke 只留 code、message）。"""
    fields = auth_fields(env)
    fields.update(body)
    try:
        return 200, env.client.call(route, fields)
    except U8CoError as exc:
        error = {"ok": False, "code": exc.code, "message": exc.message}
        error.update({"field": getattr(exc, "field", ""), "hint": getattr(exc, "hint", "")})
        return exc.status, error


def build_body(env: Env, spec: CaseSpec, step: Step, pairs: tuple, builder: str) -> dict:
    if step.id_from != "none":
        focus_id(env, spec, step)
    body = {key: resolve_token(env, spec, step, value) for key, value in pairs}
    if builder:
        body.update(_make(builder)(env))
    return body


def _src_id(env: Env, src: str) -> int:
    raw = env.focus if src == "$id" else env.bag.get(src[len("bag:") :], "")
    if _ID.fullmatch(raw or "") is None:
        raise ValueError("@w 的单据 id 不合法：" + src)
    return int(raw)


def read_doc(env: Env, kind: str, doc_id: int) -> tuple[int, dict]:
    # wf.<类型> 读审批状态，其余读单据。
    if kind.startswith("wf."):
        return send(env, _WF_STATE, {"type": kind[3:], "id": doc_id})
    return send(env, _LOAD, {"type": kind, "id": doc_id})


def _count(env: Env, spec: CaseSpec, kind: str) -> str:
    cell = first_cell(run_guarded(env, count_sql(kind), spec.name)).strip()
    if "|" not in cell:
        raise RuntimeError(kind + " 表头计数读不到")
    return cell


def _want(args: dict[str, str], body: dict, route: str) -> Want:
    mode, expect = args.get("@m", ""), args.get("@x", "any")
    if mode not in MODES or expect not in EXPECTS:
        raise ValueError("@m / @x 不合法")
    target = body.get("id")
    target = target if isinstance(target, int) and not isinstance(target, bool) else 0
    return Want(mode, expect, str(body.get("type") or ""), target, is_creating(route), args.get("@d", ""))


def _watch_problem(env: Env, watch: list[tuple[str, int]], before: list[tuple[int, dict]]) -> str:
    for (kind, doc_id), (was, old) in zip(watch, before):
        now, new = read_doc(env, kind, doc_id)
        if now != was:
            return f"{kind} {doc_id} 预演后读取 {now}（预演前 {was}）"
        diff = first_diff(old, new)
        if diff:
            return f"{kind} {doc_id} 预演前后不同：{diff}"
    return ""


def _left_problem(env: Env, spec: CaseSpec, want: Want, payload: dict, count0: str) -> str:
    if want.mode == "rollback":
        for doc_id in new_ids(payload, want.kind, want.target):
            status, _payload = read_doc(env, want.kind, doc_id)
            if status != 404:
                return f"预演给出的 {want.kind} {doc_id} 预演后读取 {status}，期望 404"
    if count0:
        count1 = _count(env, spec, want.kind)
        if count1 != count0:
            return f"{want.kind} 表头（行数|最大主键）{count0} → {count1}"
    return ""


def _note_before(env: Env, spec: CaseSpec, want: Want, body: dict, count0: str) -> None:
    # 发预演之前记下：新建 / 生单前表头的最大主键（只记第一次），锁定预演的单据。万一预演真写了（含 504），补偿按它撤回。
    if count0:
        env.bag.setdefault(max_key(spec.name, want.kind), count0.split("|")[-1])
    if body.get("action") == "lock" and want.target:
        env.bag[lock_key(spec.name)] = f"{want.kind}:{want.target}"


def _note_after(env: Env, spec: CaseSpec, want: Want, payload: dict) -> None:
    # 核销预演的 detail 给出的核销号也记下（万一真核销了，失败时取消）。
    for cancel_no in cancel_nos(payload.get("detail")):
        if cancel_no.startswith(("HXAR", "HXAP")):
            add_item(env.bag, hx_key(spec.name), cancel_no)
    if want.creating:
        for doc_id in new_ids(payload, want.kind, want.target):
            add_item(env.bag, leak_key(spec.name), f"{want.kind}:{doc_id}")


def _before_problem(watch: list[tuple[str, int]], before: list[tuple[int, dict]]) -> str:
    # 预演前读不到（忙、许可满、id 不对）就不能比较，不能当成「前后相同」。
    for (kind, doc_id), (status, payload) in zip(watch, before):
        if status != 200:
            return f"预演前读取 {kind} {doc_id} 得 {status} {payload.get('code')}，无法比较"
    return ""


def dry_step(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    args, pairs = split_fields(step.fields)
    body = build_body(env, spec, step, pairs, args.get("@b", ""))
    body["dry_run"] = True
    want = _want(args, body, step.route)
    watch = [(kind, _src_id(env, src)) for kind, src in parse_watch(args.get("@w", ""))]
    before = [read_doc(env, kind, doc_id) for kind, doc_id in watch]
    unread = _before_problem(watch, before)
    if unread:
        return "FAIL", unread[:240]
    count0 = _count(env, spec, want.kind) if want.creating and want.kind in HEADS else ""
    _note_before(env, spec, want, body, count0)
    status, payload = send(env, step.route, body)
    if status == 200:
        _note_after(env, spec, want, payload)
    if status != 200:
        return "FAIL", f"预演 {status} {payload.get('code')} {payload.get('message')}"[:240]
    problem = preview_problem(payload, want)
    problem = problem or _watch_problem(env, watch, before) or _left_problem(env, spec, want, payload, count0)
    if problem:
        return "FAIL", problem[:240]
    # 预演的 detail 记下来，后面的步骤可以按它再查（例如核销预演给出的核销号查不到核销记录）。
    env.bag[LAST_DETAIL] = canon(payload.get("detail") or {})
    return "PASS", f"{want.mode} {want.kind or step.route}"


def refuse_step(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    args, pairs = split_fields(step.fields)
    body = build_body(env, spec, step, pairs, args.get("@b", ""))
    if args.get("@dry") == "1":
        body["dry_run"] = True
    if args.get("@key") == "1":
        body["idempotency_key"] = "e2e-dry-" + os.urandom(8).hex()
    status, payload = send(env, step.route, body)
    bad = status_problem(status, payload, step.status, step.code)
    if bad:
        return "FAIL", bad[:240]
    message = str(payload.get("message") or "")
    if args.get("@t") and args["@t"] not in message:
        return "FAIL", "消息不含「" + args["@t"] + "」：" + message[:160]
    want_field = args.get("@f", "")
    if want_field and payload.get("field") != want_field:
        return "FAIL", f"field={payload.get('field')!r}，期望 {want_field}"
    return "PASS", f"{status} {payload.get('code')} {payload.get('field') or ''}".strip()


DRY_RUN_OPS = {"dry_op": dry_step, "dry_refuse": refuse_step}
