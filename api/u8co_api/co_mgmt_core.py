"""经营管理查询（/v1/co/mgmt/*）的公共部分。各报表路由只写自己的计算函数，其余都在这里：

- mgmt_guard(request, caller=None)：令牌要有经营管理权限，否则 403 mgmt_forbidden。
- begin_mgmt(request, caller, action, body)：审计（一行、全部账套）、权限、账套授权（全有或全无）、
  需要合并时确认同一公司组，再并行读各账套的 mgmt/meta。返回 MgmtRun。
- run_mgmt(request, body, report_path, inner_params_fn, only=None)：每个账套调一次桥报表
  （请求体 = 登录 + inner_params_fn(login)），结果按账套给 FanResult，单个账套失败不抛出。
- cached(key_parts, accounts_meta, compute_fn, cache)：按各账套水位缓存（co_mgmt_cache）；路由里 cache 传
  cache_of(request.app)（每个应用一份）。
- serve(request, caller, action, body, compute)：上面几步串起来并套上响应信封；compute(run) 返回
  {by_account, consolidated, eliminations, warnings, notes, complete}（缺的键按空处理）。
- finish、envelope：serve 的后两步，不走缓存的路由（mgmt/meta）直接用。
- account_error、report_failures、ratio：计算函数共用的小工具。
- mdec、madd、masked_union：桥按字段权限置空的金额保持 null，含它的合计也为 null，字段名记入 masked_fields。

桥的经营管理报表在 /v1/reports/mgmt/*，只查数据库。合并的抵销规则见公司间对照（co_ic_map）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from u8co_api.auth import Caller, caller_key
from u8co_api.co_ic_aggregate import ZERO, dec, failures, money
from u8co_api.co_ic_core import FanResult, IcGroup, allow_accs, fanout, ic_map_of, remember_ic
from u8co_api.co_mgmt_cache import cache_of, cached, credential_mark, period_entry
from u8co_api.co_models_ic import IcLogin
from u8co_api.co_models_mgmt import MgmtYearIn
from u8co_api.deps import current_caller
from u8co_api.errors import ApiError, forbidden

__all__ = [
    "MGMT_BRIDGE",
    "MASKED",
    "META_PATH",
    "ZERO",
    "MgmtRun",
    "account_error",
    "begin_mgmt",
    "cache_of",
    "cached",
    "dec",
    "envelope",
    "finish",
    "madd",
    "masked_union",
    "mdec",
    "mgmt_guard",
    "money",
    "ratio",
    "report_failures",
    "run_mgmt",
    "serve",
]

MGMT_BRIDGE = "/v1/reports/mgmt/"
META_PATH = MGMT_BRIDGE + "meta"
_HUNDRED = Decimal(100)
_PCT = Decimal("0.01")
MASKED = "masked_fields"


def mgmt_guard(request, caller: Caller | None = None) -> Caller:
    """令牌没有经营管理权限时 403 mgmt_forbidden。通过后记住调用方，后面的账套授权不再重复验签。"""
    who = caller or getattr(request.state, "ic_caller", None) or current_caller(request)
    if not who.mgmt:
        raise forbidden("无权查询经营管理数据", "mgmt_forbidden")
    request.state.ic_caller = who
    return who


@dataclass
class MgmtRun:
    """一次经营管理请求的上下文。meta 是每个请求账套的 mgmt/meta 结果；group 只在合并或配置了对照时有。"""

    request: Any
    action: str
    body: MgmtYearIn
    group: IcGroup | None
    names: dict[str, str]
    meta: dict[str, FanResult] = field(default_factory=dict)

    @property
    def accs(self) -> list[str]:
        return [login.acc for login in self.body.logins]

    @property
    def ok_accs(self) -> set[str]:
        return {acc for acc, result in self.meta.items() if result.ok}

    @property
    def consolidating(self) -> bool:
        return self.group is not None and self.body.wants_consolidation()

    def meta_body(self, acc: str) -> dict:
        found = self.meta.get(acc)
        return (found.body or {}) if found is not None and found.ok else {}

    def settings(self):
        return self.request.app.state.settings


def begin_mgmt(request, caller: Caller, action: str, body: MgmtYearIn) -> MgmtRun:
    remember_ic(request, action, body.logins, body.audit_ref())
    _audit(request, action, body)
    who = mgmt_guard(request, caller)
    allow_accs(request, [login.acc for login in body.logins], who)
    group = _group(request, body)
    names = {login.acc: group.name(login.acc) if group else login.acc for login in body.logins}
    run = MgmtRun(request, action, body, group, names)
    run.meta = run_mgmt(request, body, META_PATH, lambda _login: {"fiscal_year": body.fiscal_year})
    return run


def _audit(request, action: str, body: MgmtYearIn) -> None:
    periods = body.period_range()
    request.state.mgmt_audit = {
        "report": action.removeprefix("co:mgmt/"),
        "fiscal_year": body.fiscal_year,
        "periods": list(periods) if periods else None,
        "consolidate": body.wants_consolidation(),
        "cache_hit": None,
    }


def _group(request, body: MgmtYearIn) -> IcGroup | None:
    """合并时必须有对照且同组（否则 404 / 400）；不合并时有对照且同组就借用名称，没有也不报错。"""
    accs = {login.acc for login in body.logins}
    if body.wants_consolidation():
        return ic_map_of(request).group_of(accs)
    found = getattr(request.app.state.settings, "ic_map", None)
    if found is None:
        return None
    try:
        return found.group_of(accs)
    except ApiError:
        return None


def run_mgmt(
    request,
    body: MgmtYearIn,
    report_path: str,
    inner_params_fn: Callable[[IcLogin], dict],
    only: Iterable[str] | None = None,
) -> dict[str, FanResult]:
    """每个账套（only 给了时只调其中的账套）调一次桥报表，最多 3 个并行。结果的键顺序同 body.logins。"""
    chosen = set(only) if only is not None else None
    logins = [login for login in body.logins if chosen is None or login.acc in chosen]
    if not logins:
        return {}
    return fanout(request, logins, report_path, inner_params_fn)


def account_error(result: FanResult) -> dict[str, Any]:
    return {"ok": False, "status": result.status, "error": result.error}


def report_failures(results: dict[str, FanResult]) -> list[str]:
    """失败账套的提醒；全部失败时 503（同多账套汇总）。没有结果时返回空列表。"""
    return failures(results) if results else []


def mdec(item: dict, key: str) -> Decimal | None:
    """字段权限：桥把操作员无权查看的金额置为 null（键在、值为 null），这里保持 None，不当 0；键不在按 0。"""
    if key in item and item[key] is None:
        return None
    return dec(item.get(key))


def madd(total: Decimal | None, value: Decimal | None, sign: int = 1) -> Decimal | None:
    """含被遮蔽分量（None）的合计也是 None：不拿部分数冒充合计。"""
    if total is None or value is None:
        return None
    return total + value * sign


def masked_union(bodies: Iterable[dict], *extra: str) -> list[str]:
    """各账套响应的 masked_fields 与合并时置空的字段名的并集（排序去重）。"""
    names = {name for found in bodies for name in found.get(MASKED) or [] if isinstance(name, str)}
    names.update(extra)
    return sorted(names)


def ratio(numerator: Decimal, denominator: Decimal, scale: Decimal = _HUNDRED) -> float | None:
    """numerator / denominator × scale，保留两位小数；分母为 0 时为 None。"""
    if denominator == ZERO:
        return None
    return float((numerator * scale / denominator).quantize(_PCT))


def serve(request, caller: Caller, action: str, body: MgmtYearIn, compute: Callable[[MgmtRun], dict]) -> dict:
    """完整的一次经营管理请求：begin_mgmt → 缓存 → compute → 响应信封。"""
    run = begin_mgmt(request, caller, action, body)
    meta_warnings = report_failures(run.meta)
    accounts_meta = {acc: (result.body or {}) if result.ok else None for acc, result in run.meta.items()}
    key_parts = {
        "action": action,
        "logins": sorted(f"{login.acc}={login.operator}" for login in body.logins),
        "params": body.cache_params(),
        "periods": list(body.period_range() or ()) or None,
        # 调用方和各账套登录（含口令）的 HMAC：换人、换口令都换键，口令不对读不到别人的缓存。
        "who": _who(request, caller),
        "creds": sorted(
            credential_mark(login.acc, login.operator, login.password, login.year, login.date) for login in body.logins
        ),
    }
    value, info = cached(key_parts, accounts_meta, lambda: finish(run, compute(run)), cache_of(request.app))
    request.state.mgmt_audit["cache_hit"] = info["hit"]
    return envelope(run, value, meta_warnings, info)


def _who(request, caller: Caller | None) -> str:
    # mgmt_guard 已把验过签的调用方记在 request.state.ic_caller；取不到时为空串（co_mgmt_cache 据此不走缓存）。
    who = getattr(request.state, "ic_caller", None) or caller
    return caller_key(who) if isinstance(who, Caller) else ""


def finish(run: MgmtRun, computed: dict) -> dict:
    """补上 meta 读取失败的账套，算 complete；不完整时 consolidated 置为 null。"""
    value = dict(computed)
    by_account = dict(value.get("by_account") or {})
    for acc, result in run.meta.items():
        if not result.ok:
            by_account[acc] = account_error(result)
    complete = value.get("complete", True) is True and len(run.ok_accs) == len(run.meta)
    value.update(by_account={acc: by_account[acc] for acc in run.accs if acc in by_account}, complete=complete)
    if not complete:
        value["consolidated"] = None
    return value


def envelope(run: MgmtRun, value: dict, meta_warnings: list[str], info: dict) -> dict:
    """响应信封（字段见 co_models_mgmt.MgmtOut）。"""
    periods = run.body.period_range()
    return {
        "ok": True,
        "report": run.action.removeprefix("co:mgmt/"),
        "fiscal_year": run.body.fiscal_year,
        "period_from": periods[0] if periods else None,
        "period_to": periods[1] if periods else None,
        "complete": value.get("complete") is True,
        "accounts": [_account(run, acc, periods) for acc in run.accs],
        "by_account": value.get("by_account") or {},
        "consolidated": value.get("consolidated"),
        "eliminations": list(value.get("eliminations") or []),
        "warnings": meta_warnings + list(value.get("warnings") or []),
        "notes": list(value.get("notes") or []),
        "cache": info,
    }


def _account(run: MgmtRun, acc: str, periods: tuple[int, int] | None) -> dict[str, Any]:
    found = run.meta.get(acc)
    if found is None or not found.ok:
        return {"acc": acc, "name": run.names.get(acc, acc), "close": None}
    meta = found.body or {}
    if periods is None:
        close = [item for item in meta.get("periods") or [] if isinstance(item, dict)]
    else:
        entries = (period_entry(meta, period) for period in range(periods[0], periods[1] + 1))
        close = [item for item in entries if item is not None]
    return {"acc": acc, "name": run.names.get(acc, acc), "close": close}
