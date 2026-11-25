"""总账红字冲销（gl/vouchers/reverse，桥 GlReverse 经凭证导入保存红字凭证）。

gl-voucher-reverse-cycle：照 gl-post 在本年第一个未结账期间新建转账凭证 → 审核 → 记账 → 预演冲销（validate，不写）
  → 冲销（红字凭证日期 = 原凭证日期，同一期间）→ 响应 reversal_of 指向原凭证、out_no 非空 → 读红字凭证：未审核、未记账，
  行数相同，逐行科目相同、借贷取负，摘要以「[冲销yyyy.MM.dd 类别-NNNN号凭证]」开头，blue_out_no 等于原凭证的 out_no
  → 再冲销原凭证 409 state_mismatch（已被冲销）→ 冲销红字凭证 409（未记账）→ 作废、删除红字凭证 → 再读 404。
  原凭证已记账，同 gl-post 留给快照还原（co/e2e/README.md「已知的还原差异」）；红字凭证自己删除。
  记账之前失败时尽力弃审、作废、删除原凭证；冲销之后的核对失败时尽力作废、删除红字凭证。
  N/A 条件同 gl-post（第一个未结账期间在登录月份之后、上年度未结账、总账要求主管签字）。
gl-voucher-reverse-bad-body：不带 sign、no，登录前 400。

步骤 kind 为 "custom"，sql_name 是操作名（GL_REVERSE_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.gl_voucher_bodies import GL_DATA
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, call
from co.e2e.casespec.gl_post import _cell, _create, _drop, _first_open, _send, _want
from co.e2e.casespec.gl_post_sql import master_sign_sql, prior_open_sql
from co.e2e.casespec.gl_voucher_sql import GlKey, gl_key

_ROOT = "/v1/gl/vouchers/"
_REVERSE = _ROOT + "reverse"
_ROUTES = tuple(_ROOT + op for op in ("create", "verify", "post", "reverse", "load", "unverify", "void", "delete"))


class _Fail(Exception):
    pass


def _ident(key: GlKey) -> dict:
    return {"period": key.period, "sign": key.sign, "no": key.no}


def _must(status: int, payload: dict, expect: int, what: str) -> dict:
    bad = _want(status, payload, expect, what)
    if bad:
        raise _Fail(bad)
    return payload


def _load(env: Env, key: GlKey) -> dict:
    return _must(*_send(env, "load", _ident(key)), 200, f"读取 {key.sign}-{key.no}")


def _na_reason(env: Env, spec: CaseSpec, period: int) -> str:
    if period == 0 or period > int(env.today[5:7]):
        return f"本年第一个未结账期间是 {period}，不在登录月份之前或当月"
    if period == 1 and _cell(env, spec, prior_open_sql(env.today[:4])) != "0":
        return "本年第一个未结账期间是 1 期，上年度总账未结账，桥不许记账"
    if _cell(env, spec, master_sign_sql()).lower() in ("1", "true"):
        return "总账选项要求主管签字，桥不做主管签字"
    return ""


def _posted_source(env: Env, period: int) -> GlKey:
    key, bad = _create(env, period)
    if key is None:
        raise _Fail(bad)
    for op, body in (("verify", _ident(key)), ("post", {"period": key.period, "vouchers": [{"sign": key.sign, "no": key.no}]})):
        status, payload = _send(env, op, body)
        bad = _want(status, payload, 200, "审核" if op == "verify" else "记账")
        if bad:
            # 记账之前失败（或记账本身失败、已回滚）：尽力清理原凭证；真记上了的话弃审会被拒，留给快照还原。
            raise _Fail((bad + "；" + _drop(env, key))[:240])
    return key


def _reverse_body(key: GlKey, date: str) -> dict:
    body = _ident(key)
    body["voucher_date"] = date
    return body


def _dry_problem(env: Env, key: GlKey, date: str) -> str:
    body = _reverse_body(key, date)
    body["dry_run"] = True
    status, payload = _send(env, "reverse", body)
    if status != 200 or payload.get("dry_run") is not True or payload.get("mode") != "validate":
        return f"预演冲销应 200 validate，实际 {status} {payload.get('code')} {payload.get('mode')}"[:240]
    return ""


def _link_problem(payload: dict, key: GlKey, blue: str) -> str:
    source = payload.get("reversal_of") if isinstance(payload.get("reversal_of"), dict) else {}
    if (source.get("period"), source.get("sign"), source.get("no")) != (key.period, key.sign, key.no):
        return f"reversal_of 不是原凭证：{source}"[:240]
    if not str(payload.get("out_no") or "").strip() or str(source.get("out_no") or "").strip() == "":
        return f"冲销响应缺外部业务号：{payload}"[:240]
    if blue and source.get("out_no") != blue:
        return f"原凭证外部业务号应为 {blue}，响应 {source.get('out_no')}"
    return ""


def _line_problem(src: dict, red: dict, prefix: str, index: int) -> str:
    at = f"第 {index} 行"
    if red.get("account") != src.get("account"):
        return f"{at}科目 {red.get('account')}，应为 {src.get('account')}"
    for side in ("debit", "credit"):
        if abs(float(red.get(side) or 0) + float(src.get(side) or 0)) > 0.005:
            return f"{at}{side} {red.get(side)}，应为 {src.get(side)} 取负"
    if not str(red.get("digest") or "").startswith(prefix):
        return f"{at}摘要 {red.get('digest')} 不以 {prefix} 开头"
    return ""


def _red_problem(source: dict, red: dict, key: GlKey) -> str:
    head = red.get("voucher") or {}
    if head.get("posted") is not False or str(head.get("checker") or "") != "":
        return f"红字凭证应未审核、未记账：{head}"[:240]
    blue = str((source.get("voucher") or {}).get("out_no") or "")
    if not blue or head.get("blue_out_no") != blue:
        return f"红字凭证 blue_out_no {head.get('blue_out_no')}，应为原凭证 out_no {blue}"
    date = str((source.get("voucher") or {}).get("date") or "")
    prefix = f"[冲销{date.replace('-', '.')} {key.sign}-{key.no:04d}号凭证]"
    src_lines, red_lines = source.get("lines") or [], red.get("lines") or []
    if len(red_lines) != len(src_lines) or not src_lines:
        return f"红字凭证 {len(red_lines)} 行，原凭证 {len(src_lines)} 行"
    for index, (one, two) in enumerate(zip(src_lines, red_lines, strict=True), start=1):
        bad = _line_problem(one, two, prefix, index)
        if bad:
            return bad
    return ""


def _refusals(env: Env, key: GlKey, red: GlKey, date: str) -> str:
    for what, target in (("再冲销原凭证", key), ("冲销红字凭证", red)):
        status, payload = _send(env, "reverse", _reverse_body(target, date))
        if status != 409 or payload.get("code") != "state_mismatch":
            return _want(status, payload, 409, what) or f"{what}应 state_mismatch，实际 {payload.get('code')}"
    return ""


def _drop_red(env: Env, red: GlKey) -> str:
    for op in ("void", "delete"):
        status, payload = _send(env, op, _ident(red))
        bad = _want(status, payload, 200, "作废红字凭证" if op == "void" else "删除红字凭证")
        if bad:
            return bad
    status, payload = _send(env, "load", _ident(red))
    return _want(status, payload, 404, "删除后读取红字凭证")


def _check_red(env: Env, key: GlKey, red: GlKey, source: dict) -> None:
    date = str((source.get("voucher") or {}).get("date") or env.today)
    problem = _red_problem(source, _load(env, red), key) or _refusals(env, key, red, date)
    if problem:
        raise _Fail(problem + "；清理：" + (_drop_red(env, red) or "红字凭证已删除"))
    problem = _drop_red(env, red)
    if problem:
        raise _Fail(problem)


def _red_key(payload: dict) -> GlKey:
    try:
        return gl_key(payload.get("period"), payload.get("sign"), payload.get("no"))
    except ValueError as exc:
        raise _Fail(f"冲销响应没有合法的红字凭证键：{payload}") from exc


def _cycle(env: Env, period: int) -> str:
    key = _posted_source(env, period)
    source = _load(env, key)
    date = str((source.get("voucher") or {}).get("date") or env.today)
    bad = _dry_problem(env, key, date)
    if bad:
        raise _Fail(bad)
    payload = _must(*_send(env, "reverse", _reverse_body(key, date)), 200, "冲销")
    red = _red_key(payload)
    bad = _link_problem(payload, key, str((source.get("voucher") or {}).get("out_no") or ""))
    if bad:
        raise _Fail(bad + "；清理：" + (_drop_red(env, red) or "红字凭证已删除"))
    _check_red(env, key, red, _load(env, key))
    return f"{key.period}期 {key.sign}-{key.no} 已记账并冲销（红字 {red.sign}-{red.no} 已删除，原凭证留给快照还原）"


def gl_reverse_cycle(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    period = _first_open(env, spec)
    reason = _na_reason(env, spec, period)
    if reason:
        return "N/A", reason
    try:
        return "PASS", _cycle(env, period)
    except _Fail as exc:
        return "FAIL", str(exc)[:240]


GL_REVERSE_OPS = {"gl_reverse_cycle": gl_reverse_cycle}


def gl_reverse_cases() -> list[CaseSpec]:
    # 总账写彼此互斥（gl）；新建转账凭证（new:gl:转），冲销同样按类别编号。
    tags = frozenset(("gl", "new:gl:转"))
    cycle = Step("custom", sql_name="gl_reverse_cycle", id_from="none")
    bad = call(_REVERSE, (("period", "9"),), (), 400, "bad_request")
    title = "红字冲销：新建、审核、记账、冲销，红字凭证逐行取负并关联原凭证，再冲销 409，删除红字凭证（原凭证快照还原）"
    return [
        CaseSpec("gl-voucher-reverse-cycle", title, (), (cycle,), True, data=GL_DATA, tags=tags, routes=_ROUTES),
        CaseSpec("gl-voucher-reverse-bad-body", "红字冲销不带凭证号：登录前 400", (), (bad,), tags=frozenset(("probe",)),
                 routes=(_REVERSE,)),
    ]
