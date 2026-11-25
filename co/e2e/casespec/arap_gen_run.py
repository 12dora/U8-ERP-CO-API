"""自定义步骤（Step.kind == "core"，sql_name 是操作名）：回读核对单号和金额、列表断言、并发请求。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.bodies import other_in_body
from co.e2e.casespec.arap_gen_bodies import AMOUNT
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arap_gen import BURST_LOADS
from co.e2e.casespec.doc_list import LIST_OPS
from co.e2e.casespec.steps import auth_fields, focus_id, invoke

BURST_CREATES = 3


def _amount(payload: dict) -> str:
    state = payload.get("state")
    if isinstance(state, dict) and state.get("amount") not in (None, ""):
        return str(state.get("amount"))
    head = payload.get("head")
    if isinstance(head, dict):
        for key, val in head.items():
            if str(key).lower() == "iamount":
                return str(val)
    return ""


def load_same(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    # 回读刚建的单据：单号等于新建时返回的单号，金额等于正文里的金额。
    kind = dict(step.fields)["type"]
    want = env.last_code
    fields = auth_fields(env)
    fields.update({"type": kind, "id": int(focus_id(env, spec, step))})
    status, payload = invoke(env.client, "/v1/vouchers/load", fields)
    if status != 200:
        return "FAIL", f"{status} {payload.get('code')} {payload.get('message')}"[:240]
    code = str(payload.get("code") or "")
    if want == "" or code != want:
        return "FAIL", "回读单号 " + code + " 期望 " + want
    try:
        same = Decimal(_amount(payload)) == Decimal(AMOUNT)
    except InvalidOperation:
        same = False
    if not same:
        return "FAIL", "回读金额 " + _amount(payload)
    return "PASS", code


def load_code(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    # 回读刚生成的单据：单号等于 bag 里记下的号（例如采购发票号），表头不空。
    args = dict(step.fields)
    # key 为 $last 时比对生单响应里的单号。
    want = env.last_code if args["key"] == "$last" else env.bag.get(args["key"], "")
    fields = auth_fields(env)
    fields.update({"type": args["type"], "id": int(focus_id(env, spec, step))})
    status, payload = invoke(env.client, "/v1/vouchers/load", fields)
    if status != 200:
        return "FAIL", f"{status} {payload.get('code')} {payload.get('message')}"[:240]
    code = str(payload.get("code") or "")
    if want == "" or code != want:
        return "FAIL", "回读单号 " + code + " 期望 " + want
    if not isinstance(payload.get("head"), dict) or not payload.get("head"):
        return "FAIL", "回读表头为空"
    return "PASS", code


def _send(env: Env, route: str, extra: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(extra)
    try:
        return invoke(env.client, route, fields)
    except (ValueError, OSError) as exc:
        return 0, {"ok": False, "code": "client", "message": str(exc)[:120]}


def fire(env: Env, jobs: list[tuple[str, dict]]) -> list[tuple[int, dict]]:
    # 所有请求同时发出；结果按 jobs 的顺序返回。
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(_send, env, route, extra) for route, extra in jobs]
        return [item.result() for item in futures]


def _burst_jobs(env: Env) -> list[tuple[str, dict]]:
    jobs = [("/v1/vouchers/load", {"type": kind, "id": int(env.bag["load_" + kind])}) for kind in BURST_LOADS]
    for index in range(BURST_CREATES):
        body = other_in_body(env)
        body["head"]["cMemo"] = "CO桥并发" + str(index + 1)
        jobs.append(("/v1/vouchers/create", body))
    return jobs


def _bad(status: int, payload: dict) -> str:
    if status == 200 and payload.get("ok") is True:
        return ""
    return f"{status} {payload.get('code')} {payload.get('message')}"[:160]


def _load_problem(extra: dict, status: int, payload: dict) -> str:
    bad = _bad(status, payload)
    if bad:
        return extra["type"] + " " + bad
    if str(payload.get("id")) != str(extra["id"]) or not payload.get("code"):
        return extra["type"] + " 读回的单据不对"
    return ""


def _made(results: list[tuple[int, dict]]) -> list[int]:
    found = []
    for status, payload in results:
        raw = payload.get("id")
        if status == 200 and isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            found.append(raw)
    return found


def _creates_problem(results: list[tuple[int, dict]]) -> str:
    for status, payload in results:
        bad = _bad(status, payload)
        if bad:
            return "新建 " + bad
    codes = {str(payload.get("code") or "") for _status, payload in results}
    ids = set(_made(results))
    if "" in codes or len(codes) != len(results) or len(ids) != len(results):
        return "并发新建的单号或 id 重复"
    return ""


def _drop(env: Env, ids: list[int]) -> str:
    # 并发删除本步骤新建的单据，再逐张确认读不到。
    if not ids:
        return ""
    jobs = [("/v1/vouchers/delete", {"type": "other_in", "id": num}) for num in ids]
    for status, payload in fire(env, jobs):
        bad = _bad(status, payload)
        if bad or payload.get("deleted") is not True:
            return "删除 " + (bad or "没有 deleted")
    for num in ids:
        status, payload = _send(env, "/v1/vouchers/load", {"type": "other_in", "id": num})
        if status != 404 or payload.get("code") != "not_found":
            return f"删除后仍能读取 {num}"
    return ""


def burst(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    jobs = _burst_jobs(env)
    results = fire(env, jobs)
    loads = len(BURST_LOADS)
    made = _made(results[loads:])
    problem = ""
    for (_route, extra), (status, payload) in zip(jobs[:loads], results[:loads]):
        problem = problem or _load_problem(extra, status, payload)
    problem = problem or _creates_problem(results[loads:])
    # 不论前面是否通过，都删掉本步骤建出的单据。
    dropped = _drop(env, made)
    problem = problem or dropped
    if problem:
        return "FAIL", problem
    return "PASS", f"{len(jobs)} 个并发请求，新建 {len(made)} 张并已删除"


_OPS = {"load_same": load_same, "load_code": load_code, "burst": burst}
_OPS.update(LIST_OPS)


def step_core(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    op = _OPS.get(step.sql_name)
    if op is None:
        return "FAIL", "未知步骤"
    try:
        return op(env, spec, step)
    except (KeyError, RuntimeError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
