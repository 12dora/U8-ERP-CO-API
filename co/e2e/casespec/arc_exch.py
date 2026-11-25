"""汇率写入（桥 ArcExchWrite / ArcExchSql）：新增走 U8 官方 EAI 分发器（currencyrate），修改、删除是桥的受控 SQL。

挑一个非本位币的币种（经 API 读币种档案，币种名称多为中文，不走挑选 SQL），在登录年度里找一个还没有任何汇率的未来期间
（从 12 期往回找，到登录月份为止），依次：新增记账汇率 → 再新增同一汇率 409 state_mismatch（已存在，改用 update）→ 读取
→ 修改 → 读取 → 删除 → 读取 404。只用空期间：删除会删掉该期间的全部汇率，不能碰已有数据。
挑不到外币或空期间记 N/A。步骤 kind 为 "custom"，sql_name 是操作名（EXCH_OPS 并入 ops_run）。中途失败时尽力删掉本用例建的汇率。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_write_more import _cleanup, _expect, _send

_ROUTES = tuple("/v1/archives/" + op for op in ("create", "get", "update", "delete", "list"))
_ARCHIVE = "exchange_rate"
_RATES = (7.1234, 7.2345)


def _foreign(env: Env) -> str:
    """非本位币的币种名称（get 的 fields 里 iotherused 不是 -1）；找不到返回空串。"""
    status, got = _send(env, "list", {"archive": "currency", "limit": 50})
    items = got.get("items") if status == 200 else None
    for item in items if isinstance(items, list) else []:
        code = str(item.get("code") or "") if isinstance(item, dict) else ""
        status, one = _send(env, "get", {"archive": "currency", "code": code}) if code else (0, {})
        fields = one.get("fields") if status == 200 else None
        if isinstance(fields, dict) and str(fields.get("iotherused", "")).strip() not in ("", "-1"):
            return code
    return ""


def _free_period(env: Env, currency: str, year: int, month: int) -> int:
    """登录年度里该币种没有任何汇率的未来期间（从 12 往回到登录月份之后）；没有返回 0。"""
    body = {"archive": _ARCHIVE, "currency": currency, "fiscal_year": year, "limit": 500}
    status, got = _send(env, "list", body)
    items = got.get("items") if status == 200 else None
    if not isinstance(items, list):
        return 0
    used = {item.get("period") for item in items if isinstance(item, dict)}
    return next((period for period in range(12, month, -1) if period not in used), 0)


def _rate_problem(env: Env, key: dict, want: float) -> str:
    status, got = _send(env, "get", key)
    bad = _expect(status, got, 200, "读取")
    if bad:
        return bad
    fields = got.get("fields") if isinstance(got.get("fields"), dict) else {}
    rate = fields.get("rate")
    if not isinstance(rate, (int, float)) or abs(float(rate) - want) > 1e-6:
        return f"读取 rate={rate!r}，期望 {want}"
    return ""


def _again_refused(env: Env, key: dict) -> str:
    status, payload = _send(env, "create", dict(key, fields={"rate": _RATES[0]}))
    if status != 409 or payload.get("code") != "state_mismatch":
        return f"重复新增应 409 state_mismatch，实际 {status} {payload.get('code')} {payload.get('message')}"[:200]
    return ""


def _cycle(env: Env, key: dict) -> str:
    status, payload = _send(env, "create", dict(key, fields={"rate": _RATES[0]}))
    bad = _expect(status, payload, 200, "新增")
    if not bad and payload.get("code") != key["code"]:
        bad = f"新增响应的 code 是 {payload.get('code')!r}，期望 {key['code']!r}"
    bad = bad or _again_refused(env, key) or _rate_problem(env, key, _RATES[0])
    if bad:
        return bad
    status, payload = _send(env, "update", dict(key, fields={"rate": _RATES[1]}))
    bad = _expect(status, payload, 200, "修改") or _rate_problem(env, key, _RATES[1])
    if bad:
        return bad
    status, payload = _send(env, "delete", key)
    bad = _expect(status, payload, 200, "删除")
    if bad or payload.get("deleted") is not True:
        return bad or "删除响应缺 deleted"
    status, payload = _send(env, "get", key)
    return _expect(status, payload, 404, "删除后读取")


def exch_write(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del spec, step
    currency = _foreign(env)
    if not currency:
        return "N/A", "测试账套里没有外币"
    year, month = int(env.today[:4]), int(env.today[5:7])
    period = _free_period(env, currency, year, month)
    if not period:
        return "N/A", f"{currency} 在 {year} 年登录月份之后没有空的期间"
    key = {"archive": _ARCHIVE, "code": f"{currency}:{year}:{period}"}
    problem = _cycle(env, key)
    if problem:
        return "FAIL", (_cleanup(env, _ARCHIVE, key["code"]) + "；" + problem)[:240]
    return "PASS", f"exchange_rate {key['code']}"


EXCH_OPS = {"exch_write": exch_write}


def exch_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name="exch_write", id_from="none")
    title = "汇率新增（EAI）读取修改删除（受控 SQL），已存在时新增 409"
    tags = frozenset({"arc:" + _ARCHIVE, "arc:exchange"})
    return [CaseSpec("archive-exchange-rate-write", title, (), (step,), True, tags=tags, routes=_ROUTES)]
