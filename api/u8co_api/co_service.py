"""Gates, audit fields, and the call into the CO bridge. Passwords are not logged."""

from __future__ import annotations

import re

from u8co_api.auth import Caller
from u8co_api.co_access import WRITE, access_of, is_mgmt, is_perm_evaluate, is_read, may_call
from u8co_api.co_client import DEFAULT_ROUTE, BridgeRoutes
from u8co_api.co_clock import login_defaults
from u8co_api.co_dryrun import take as take_dry_run
from u8co_api.co_idem import caller_tag, idempotency_fields
from u8co_api.co_models import CoAuth
from u8co_api.co_route_health import RouteProbes
from u8co_api.errors import ApiError, forbidden, not_found, unavailable
from u8co_api.write_class import WriteInfo, classify

__all__ = ["login_defaults"]

_UUID = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


def bridge_payload(body: CoAuth) -> dict:
    data = body.model_dump(exclude_none=True)
    date, year = login_defaults(data.get("date"))
    data["date"] = date
    data.setdefault("year", year)
    return data


def ensure_co(request, caller: Caller, action: str) -> None:
    settings = request.app.state.settings
    if not settings.enabled:
        raise not_found("CO 接口未启用")
    if not may_call(caller.write, caller.read, action, caller.mgmt, caller.perm_evaluate):
        raise _denied_error(caller, action)
    if not settings.configured or request.app.state.co_bridge is None:
        raise unavailable("CO 桥未配置")


def _denied_error(caller: Caller, action: str) -> ApiError:
    # 经营管理路由只认经营管理权限（co_access.MGMT）。
    if is_mgmt(action):
        return forbidden("无权查询经营管理数据", "mgmt_forbidden")
    # perm/evaluate 只给信任项写了 perm_evaluate 的调用方。
    if is_perm_evaluate(action):
        return forbidden("无权查询其他操作员的权限")
    return forbidden(_denied(caller))


def _denied(caller: Caller) -> str:
    if caller.read:
        return "只读权限不能调用写操作"
    return "无权使用 CO 接口"


def allow_acc(settings, caller: Caller, acc: str) -> None:
    # 服务白名单（U8CO_ACCOUNTS）和令牌的账套声明（若信任项配置了）同时满足才放行。
    if acc not in settings.accounts:
        raise forbidden("账套不在允许列表", "account_not_allowed")
    if caller.accounts is not None and acc not in caller.accounts:
        raise forbidden("令牌无权使用该账套", "account_not_allowed")


def refuse_read_only(request, action: str, acc: str) -> None:
    """只读账套（U8CO_READONLY_ACCOUNTS）的写路由一律 403 account_read_only：在访问桥和写入策略之前判定，
    预演、带幂等键的重试、公司间生单都一样拒绝（不重放）。读路由、经营管理路由不受影响。所有写入口都调这一处。"""
    if access_of(action) != WRITE:
        return
    if acc in request.app.state.settings.read_only_accounts:
        request.state.outcome = "account_read_only"
        raise forbidden("该账套只开放读取", "account_read_only")


def remember_co(request, action: str, body: CoAuth) -> None:
    request.state.accs = [body.acc]
    request.state.operators = [f"{body.acc}={body.operator}"]
    request.state.audit_action = _audit_action(action, body)
    remember_user(request)


def _audit_action(route: str, body: CoAuth) -> str:
    # 记下路径、动作和单据主键。口令和审批意见不进审计。
    verb = getattr(body, "action", None)
    if not isinstance(verb, str) or not verb:
        verb = _route_verb(route)
    text = f"{route}:{verb}" if verb else route
    doc_id = getattr(body, "id", None)
    if isinstance(doc_id, int):
        return f"{text}#{doc_id}"
    # 总账凭证记期间-类别-凭证号，档案记档案:编码。
    ref = getattr(body, "audit_ref", None)
    if callable(ref):
        return f"{text}#{ref()}"
    return text


def _route_verb(route: str) -> str:
    if "/" not in route:
        return ""
    return route.rsplit("/", 1)[-1]


def remember_user(request) -> None:
    """审计的终端用户：令牌有 sub 时就是 sub，U8CO_USER_HEADER 头被忽略（头可以伪造）；只有信任项写了
    on_behalf_header: true 的机器调用方（代人调用）才取头里的 UUID，此时 sub 另记在审计的 sub 字段。
    头只认一个 UUID，别的格式忽略。"""
    sub = getattr(request.state, "token_sub", None)
    if getattr(request.state, "on_behalf_header", False) is not True:
        if sub:
            request.state.end_user = sub
        return
    values = request.headers.getlist(request.app.state.settings.user_header)
    if len(values) != 1:
        return
    token = values[0].strip()
    if _UUID.fullmatch(token) is not None:
        request.state.end_user = token


def prepare_call(request, caller: Caller, action: str, body: CoAuth) -> dict:
    """审计字段、读写权限、账套白名单、写入策略，然后生成桥请求体（填好日期和年度）。"""
    remember_co(request, action, body)
    ensure_co(request, caller, action)
    allow_acc(request.app.state.settings, caller, body.acc)
    payload = bridge_payload(body)
    check_write(request, action, payload)
    return payload


def check_write(request, action: str, payload: dict) -> None:
    """写入策略：写路由先分类（审计记 type、op），再拒绝只读账套，配置了 U8CO_WRITE_POLICY_FILE 时在访问桥之前判定第 1 到 6 步。
    预演同样判定。没登记分类的写路由按 type 为空、op 为 other 判定（只有 "*" 规则放行）。"""
    if is_read(action):
        return
    info = classify(action, payload) or WriteInfo(
        acc=_acc_of(payload), type="", op="other", operator=str(payload.get("operator", ""))
    )
    request.state.write_type = info.type or None
    request.state.write_op = info.op
    refuse_read_only(request, action, _acc_of(payload))
    policy = getattr(request.app.state, "write_policy", None)
    if policy is None:
        return
    refused = policy.check(info)
    if refused is not None:
        request.state.outcome = refused.code
        raise refused


def _acc_of(payload: dict) -> str:
    acc = payload.get("acc")
    return acc if isinstance(acc, str) else ""


def run_call(request, caller: Caller, action: str, bridge_path: str, body: CoAuth) -> dict:
    payload = prepare_call(request, caller, action, body)
    # dry_run 只在为 true 时转给桥；预演不能带 Idempotency-Key（co_dryrun）。
    take_dry_run(payload, bridge_path, request.headers)
    payload.update(idempotency_fields(request.headers, caller, bridge_path))
    if not is_read(action):
        # 写入一律带 caller（没带幂等键时桥只记进审计），桥的审计行据此记下调用方。
        payload.setdefault("caller", caller_tag(caller))
    return call_bridge(request, body.acc, bridge_path, payload)


def call_bridge(request, acc: str, bridge_path: str, payload: dict) -> dict:
    """调桥，并把结果码（ok 或桥的错误码）记进审计行的 outcome。"""
    try:
        result = bridge_of(request, acc).call(bridge_path, payload)
    except ApiError as exc:
        request.state.outcome = exc.code
        raise
    request.state.outcome = "ok"
    return result


def run_health(request, caller: Caller) -> dict:
    request.state.audit_action = "co:health"
    remember_user(request)
    ensure_co(request, caller, "co:health")
    entries = _routes(request).entries
    # 分流桥先并行发出短超时探测，再照旧探测缺省桥；顶层只描述缺省桥（与未分流时一致），
    # 某个分流桥不通或超时只记在 routes 里，不影响整体状态码。
    probes = RouteProbes(entries) if entries else None
    try:
        out = dict(_default_health(request))
        if probes is not None:
            out["routes"] = probes.collect()
        # 本服务自己的写入策略（U8CO_WRITE_POLICY_FILE）；桥的 write_policy 原样透传（顶层与各分流桥）。
        policy = getattr(request.app.state, "write_policy", None)
        if policy is not None:
            out["api_write_policy"] = policy.health()
        # 本服务的只读账套（U8CO_READONLY_ACCOUNTS）；桥的 read_only_accounts 原样透传。没配时省略。
        read_only = request.app.state.settings.read_only_accounts
        if read_only:
            out["api_read_only_accounts"] = list(read_only)
        return out
    finally:
        if probes is not None:
            probes.close()


def _default_health(request) -> dict:
    try:
        return bridge_of(request).health()
    except ApiError:
        raise
    except Exception as exc:
        raise unavailable("CO 桥不可达") from exc


def bridge_of(request, acc: str | None = None):
    """取服务这个账套的桥：分流文件里有这个账套就用分流桥，否则（含不带账套的调用）用缺省桥。审计行记下用的是哪个。"""
    bridge = request.app.state.co_bridge
    if bridge is None:
        raise unavailable("CO 桥未配置")
    routed = _routes(request).pick(acc) if acc else None
    if routed is None:
        request.state.bridge = DEFAULT_ROUTE
        return bridge
    request.state.bridge = routed[0]
    return routed[1]


def _routes(request) -> BridgeRoutes:
    found = getattr(request.app.state, "co_routes", None)
    return found if found is not None else _NO_ROUTES


_NO_ROUTES = BridgeRoutes()
