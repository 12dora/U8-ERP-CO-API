"""GET /v1/co/meta：字段元数据。内容由桥按自身的白名单表和 U8 的 RsXml 生成，这里只代理并在进程内缓存 60 秒。

不在 API 里另存一份单据类型或字段清单：pydantic 模型里的 Literal 只是方便调用方，真正的来源是桥。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field

from u8co_api.auth import Caller
from u8co_api.co_access import access_note, access_of
from u8co_api.co_service import bridge_of, ensure_co, remember_user
from u8co_api.co_table import TAG_CO
from u8co_api.deps import current_caller as _caller
from u8co_api.errors import ApiError, unavailable

META_ACTION = "co:meta"
META_PATH = "/v1/co/meta"
META_TTL = 60.0
# 等待同一次桥调用的上限，略长于桥自己的等待（约 75 秒）。
META_WAIT = 100.0

_CREATE_LOCK = threading.Lock()

_DESCRIPTION = """\
返回桥上的字段元数据，供调用方生成请求和校验字段。

**用法**
- 返回单据类型与支持的操作、必填字段、生单来源。
- 可写字段：exact 逐个列出，spans 为 cdefine / cfree 编号区间。
- 档案标签、总账凭证字段、列表类型和桥的路由表。
- 写字段到档案的对照 field_refs、gl_field_refs。
- 预演模式：kinds[].dry_run、dry_run_routes。

**规则**
- 不需要账套和口令，桥不登录 U8。
- revision 是除 features 之外内容的 SHA-256，内容变了 revision 才变。
- features 是运行时自检结果，可能随时变化。
- 本服务在进程内缓存 60 秒。
- complete 为 false：有档案的 RsXml 没有读到（该档案 tags 为 null，带 tags_error）。

"""


class CoMetaOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    ok: bool = Field(description="总是 true")
    version: str = Field(description="元数据格式版本")
    revision: str = Field(description="除 features 外内容的 SHA-256（十六进制小写）")
    complete: bool = Field(description="档案标签是否全部读到")
    kinds: list[dict[str, Any]] = Field(description="单据类型")
    archives: list[dict[str, Any]] = Field(description="档案类型")
    gl: dict[str, Any] = Field(description="总账凭证字段")
    list_kinds: list[str] = Field(description="vouchers/list 支持的类型")
    routes: list[dict[str, Any]] = Field(description="桥的路由及请求体允许的字段")
    features: dict[str, Any] = Field(description="运行时自检结果")
    # 写字段 → 档案的对照（给名称解析用）和各路由的预演模式。旧版桥没有时省略。
    field_refs: dict[str, str] | None = Field(
        None, description="写字段名（小写）→ 档案类型，如 ccuscode → customer；值可用 archives/resolve 解析"
    )
    gl_field_refs: dict[str, str] | None = Field(
        None, description="总账凭证分录字段 → 档案类型，如 account → account、dept → department"
    )
    dry_run_routes: dict[str, Any] | None = Field(
        None,
        description="单据以外路由的预演模式：路由 → rollback / validate，或按档案类型分的 {类型: 模式, \"*\": 模式}。"
        "单据类型各操作的模式见 kinds[].dry_run",
    )


class _Flight:
    """一次正在进行的桥调用。领头的请求去取，其余请求等它的结果。"""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: dict[str, Any] | None = None
        self.error: BaseException | None = None


class MetaCache:
    """成功结果缓存 ttl 秒。锁只保护几行状态读写，不在锁里调桥；缓存过期时同一时刻只有一个请求打到桥（single-flight），
    其余请求等这次的结果（最多 wait 秒），失败时一起收到同一个错误，失败不缓存。"""

    def __init__(
        self, ttl: float = META_TTL, clock: Callable[[], float] = time.monotonic, wait: float = META_WAIT
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._wait = wait
        self._lock = threading.Lock()
        self._value: dict[str, Any] | None = None
        self._at = 0.0
        self._flight: _Flight | None = None

    def get(self, fetch: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            if self._value is not None and self._clock() - self._at < self._ttl:
                return self._value
            flight = self._flight
            if flight is None:
                flight = self._flight = _Flight()
                leader = True
            else:
                leader = False
        if leader:
            return self._lead(flight, fetch)
        return self._join(flight)

    def _lead(self, flight: _Flight, fetch: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            value = fetch()
        except BaseException as exc:
            flight.error = exc
            raise
        else:
            flight.value = value
            with self._lock:
                self._value = value
                self._at = self._clock()
            return value
        finally:
            with self._lock:
                self._flight = None
            flight.done.set()

    def _join(self, flight: _Flight) -> dict[str, Any]:
        if not flight.done.wait(self._wait):
            raise unavailable("CO 桥没有及时返回元数据")
        if flight.error is not None:
            raise flight.error
        if flight.value is None:
            raise unavailable("CO 桥没有返回元数据")
        return flight.value


def register_meta(app: FastAPI, errors: dict, slot: Callable) -> None:
    app.add_api_route(
        META_PATH,
        co_meta,
        methods=["GET"],
        response_model=CoMetaOut,
        responses=errors,
        summary="字段元数据",
        description=_DESCRIPTION + access_note(META_ACTION),
        tags=[TAG_CO],
        operation_id="coMeta",
        dependencies=[Depends(slot)],
        response_model_exclude_none=True,
        openapi_extra={"x-u8co-access": access_of(META_ACTION)},
    )


def co_meta(request: Request, caller: Caller = Depends(_caller)) -> dict[str, Any]:
    return run_meta(request, caller)


def run_meta(request: Request, caller: Caller) -> dict[str, Any]:
    request.state.audit_action = META_ACTION
    remember_user(request)
    ensure_co(request, caller, META_ACTION)
    # 元数据与账套无关，总走缺省桥。
    bridge = bridge_of(request)
    return _cache(request.app.state).get(lambda: _fetch(bridge))


def _fetch(bridge) -> dict[str, Any]:
    # 只读，不会改动 U8；传输中断也按不可用处理，可以直接重试。
    try:
        return bridge.meta()
    except ApiError as exc:
        if exc.code == "outcome_unknown":
            raise unavailable("CO 桥没有返回元数据") from exc
        raise


def _cache(state) -> MetaCache:
    cache = getattr(state, "co_meta_cache", None)
    if cache is not None:
        return cache
    with _CREATE_LOCK:
        cache = getattr(state, "co_meta_cache", None)
        if cache is None:
            cache = MetaCache()
            state.co_meta_cache = cache
        return cache
