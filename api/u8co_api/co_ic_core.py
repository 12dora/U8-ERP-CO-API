"""公司间（多账套）调用的公共部分：对照文件、账套授权（全有或全无）、按账套并行调桥、翻页、审计。

每个账套各用自己的操作员和口令，经 bridge_of 同一套分流规则选桥，走 CoBridge.call（错误映射与单账套调用相同）。
单个账套失败不抛出，记在结果里；授权不全直接 403，不访问任何桥。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any

from u8co_api.co_access import MGMT_ACTIONS
from u8co_api.co_client import DEFAULT_ROUTE
from u8co_api.co_ic_map import IcGroup, IcMap, load_ic_map
from u8co_api.co_models import CoAuth
from u8co_api.co_models_ic import IcLogin
from u8co_api.co_service import allow_acc, bridge_payload, remember_user
from u8co_api.deps import current_caller
from u8co_api.errors import ApiError, not_found, timeout_error, unavailable, unprocessable

__all__ = [
    "FANOUT_ACTIONS",
    "FanResult",
    "IcGroup",
    "IcLogin",
    "IcMap",
    "allow_accs",
    "fanout",
    "ic_map_of",
    "load_ic_map",
    "paged",
    "remember_ic",
    "run_jobs",
]

# 一个请求最多同时调 3 个账套的桥。
MAX_WORKERS = 3
# 公司间路由的审计动作：runner 按账套多次调桥，不是「一个路由一次桥调用」（测试据此把它们与普通路由分开）。
FANOUT_ACTIONS = frozenset(
    {
        "co:reports/intercompany_match",
        "co:intercompany/generate_buyer",
        "co:reports/aggregate",
        "co:reports/consolidation",
    }
    # 经营管理查询（co_mgmt_core）同样按账套多次调桥。
    | MGMT_ACTIONS
)


@dataclass(frozen=True)
class FanResult:
    """一个账套的结果：ok 时 body 是桥的响应体；失败时 error 是 {code, message}，status 是对应的 HTTP 状态。"""

    ok: bool
    status: int
    body: dict | None = None
    error: dict | None = None

    @staticmethod
    def failed(exc: ApiError) -> FanResult:
        return FanResult(False, exc.status, None, {"code": exc.code, "message": exc.message})


def ic_map_of(request) -> IcMap:
    found = getattr(request.app.state.settings, "ic_map", None)
    if found is None:
        error = not_found("未配置公司间对照", "ic_not_configured")
        error.hint = "管理员在 U8CO_IC_MAP_FILE 里配置公司组后再用公司间接口"
        raise error
    return found


def allow_accs(request, accs: Iterable[str], caller=None) -> None:
    """每个账套都要过 allow_acc（U8CO_ACCOUNTS ∩ 令牌账套声明），有一个不过就 403 account_not_allowed。"""
    who = caller or getattr(request.state, "ic_caller", None) or current_caller(request)
    settings = request.app.state.settings
    for acc in accs:
        allow_acc(settings, who, acc)


def remember_ic(request, action: str, logins: Iterable[CoAuth], ref: str = "") -> None:
    """一个公司间请求一行审计：accs、operators 列出全部账套，action 末尾追加各账套的结果（fanout 自动补）。"""
    chosen = list(logins)
    request.state.accs = [login.acc for login in chosen]
    request.state.operators = [f"{login.acc}={login.operator}" for login in chosen]
    request.state.ic_action = f"{action}#{ref}" if ref else action
    request.state.ic_outcomes = {}
    request.state.ic_bridges = {}
    request.state.audit_action = request.state.ic_action
    remember_user(request)


def note_outcome(request, acc: str, outcome: str) -> None:
    outcomes = _state_dict(request, "ic_outcomes")
    outcomes[acc] = outcome
    base = getattr(request.state, "ic_action", None) or getattr(request.state, "audit_action", None) or "co:ic"
    request.state.ic_action = base
    tail = ",".join(f"{key}={value}" for key, value in sorted(outcomes.items()))
    request.state.audit_action = f"{base}:{tail}" if "#" in base else f"{base}#{tail}"


def _state_dict(request, name: str) -> dict:
    found = getattr(request.state, name, None)
    if not isinstance(found, dict):
        found = {}
        setattr(request.state, name, found)
    return found


def bridge_for(request, acc: str):
    """取服务这个账套的桥（规则同 co_service.bridge_of），不在工作线程里改 request.state；审计的 bridge 记成「账套=路由」。"""
    bridge = request.app.state.co_bridge
    if bridge is None:
        raise unavailable("CO 桥未配置")
    routes = getattr(request.app.state, "co_routes", None)
    routed = routes.pick(acc) if routes is not None else None
    name, chosen = (DEFAULT_ROUTE, bridge) if routed is None else routed
    names = _state_dict(request, "ic_bridges")
    names[acc] = name
    request.state.bridge = ",".join(f"{key}={value}" for key, value in sorted(names.items()))
    return chosen


def call_acc(request, login: CoAuth, path: str, body: dict) -> dict:
    """一个账套的一次桥调用：登录字段（日期、年度缺省）加上 body。桥要在主线程里先用 bridge_for 取好。"""
    bridge = _bridges(request).get(login.acc) or bridge_for(request, login.acc)
    payload = bridge_payload(login)
    payload.update(body)
    return bridge.call(path, payload)


def _bridges(request) -> dict:
    return _state_dict(request, "ic_bridge_objs")


def _prime(request, accs: Iterable[str]) -> None:
    found = _bridges(request)
    for acc in accs:
        if acc not in found:
            found[acc] = bridge_for(request, acc)


def paged(request, login: CoAuth, path: str, body: dict, **paging: int) -> list[dict]:
    """按 next / after 读完一个列表或报表的全部 items。关键字 page_size（每页条数）、max_pages（最多页数）必填；
    超过 max_pages 页仍有下一页时 422 ic_too_many_rows。"""
    page_size, max_pages = paging["page_size"], paging["max_pages"]
    rows: list[dict] = []
    after: Any = None
    for _ in range(max_pages):
        request_body = dict(body, limit=page_size)
        if after is not None:
            request_body["after"] = after
        reply = call_acc(request, login, path, request_body)
        items = reply.get("items")
        rows.extend(item for item in items or [] if isinstance(item, dict))
        after = reply.get("next")
        if after is None or after == "":
            return rows
    raise too_many(login.acc, max_pages * page_size)


def too_many(acc: str, limit: int) -> ApiError:
    error = unprocessable(f"账套 {acc} 的数据超过 {limit} 行", "ic_too_many_rows")
    error.hint = "缩小日期范围或条件后重试"
    return error


def run_jobs(request, jobs: dict[str, Callable[[], dict]], *, timeout: float | None = None) -> dict[str, FanResult]:
    """每个账套一个任务（可以是多次桥调用），最多 3 个并行，并发名额同时占全局和本调用方的在途上限。
    任务里的 ApiError 记成该账套的失败，不抛出；结果的键顺序同 jobs。"""
    _prime(request, jobs)
    results: dict[str, FanResult] = {}
    with _extra_slots(request, min(MAX_WORKERS, len(jobs)) - 1) as extra:
        pool = ThreadPoolExecutor(max_workers=1 + extra, thread_name_prefix="u8co-ic")
        try:
            pending = [(acc, pool.submit(job)) for acc, job in jobs.items()]
            deadline = None if timeout is None else time.monotonic() + timeout
            for acc, future in pending:
                results[acc] = _collect(acc, future, deadline)
        finally:
            pool.shutdown(wait=timeout is None, cancel_futures=True)
    for acc, result in results.items():
        note_outcome(request, acc, "ok" if result.ok else (result.error or {}).get("code", "error"))
    return results


def _collect(acc: str, future: Future, deadline: float | None) -> FanResult:
    try:
        wait = None if deadline is None else max(0.0, deadline - time.monotonic())
        body = future.result(timeout=wait)
    except ApiError as exc:
        return FanResult.failed(exc)
    except TimeoutError:
        return FanResult.failed(timeout_error(f"账套 {acc} 超时"))
    except Exception:
        # 桥不可达已由 CoBridge.call 转成 ApiError；走到这里的是本服务自身的错误，记日志，按内部错误报。
        logging.getLogger("u8co.api").exception("公司间任务出错：账套 %s", acc)
        return FanResult.failed(ApiError(500, "internal", f"账套 {acc} 的处理出现内部错误"))
    return FanResult(True, 200, body if isinstance(body, dict) else {}, None)


def fanout(
    request,
    logins: Iterable[CoAuth],
    path: str,
    body_for: Callable[[CoAuth], dict],
    *,
    timeout: float | None = None,
) -> dict[str, FanResult]:
    """每个账套调一次同一个桥路由，请求体是登录字段加 body_for(login)。先检查全部账套的授权（不全则 403）。"""
    chosen = list(logins)
    allow_accs(request, [login.acc for login in chosen])
    jobs = {login.acc: _job(request, login, path, body_for) for login in chosen}
    return run_jobs(request, jobs, timeout=timeout)


def _job(request, login: CoAuth, path: str, body_for: Callable[[CoAuth], dict]) -> Callable[[], dict]:
    body = body_for(login)
    return lambda: call_acc(request, login, path, body)


@contextmanager
def _extra_slots(request, want: int):
    """除了路由依赖已占的一个名额，再尽量多占 want 个（全局 + 本调用方各一份），占不到就少开线程，不报 429。"""
    state = request.app.state
    # 与 co_routes.co_slot 同一个调用方键（含 sub，见 auth.caller_key）。
    key = getattr(request.state, "caller_key", None) or (
        f"{getattr(request.state, 'trust_name', '')}:{getattr(request.state, 'caller_id', '')}"
    )
    got = 0
    with ExitStack() as stack:
        for _ in range(max(0, want)):
            if not _take(stack, state.co_global_limit, "co"):
                break
            if not _take(stack, state.co_limit, key):
                break
            got += 1
        yield got


def _take(stack: ExitStack, limit, key: str) -> bool:
    if limit is None or not limit.acquire(key):
        return False
    stack.callback(limit.release, key)
    return True
