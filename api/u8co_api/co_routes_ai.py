"""给调用方（含 AI 代理）用的只读路由：档案名称解析、幂等结果查询。注册见 co_routes.register_co_routes。"""

from __future__ import annotations

from typing import Any

from u8co_api.co_bridge import to_api_error
from u8co_api.co_idem import idempotency_fields, lookup_fields
from u8co_api.co_models_ai import (
    IDEM_HELP,
    IDEM_SUMMARY,
    RESOLVE_HELP,
    RESOLVE_SUMMARY,
    IdemGetIn,
    IdemGetOut,
    ResolveIn,
    ResolveOut,
)
from u8co_api.co_routes_gl_arc import TAG_ARC
from u8co_api.co_service import bridge_of, prepare_call
from u8co_api.co_table import TAG_CO, CoRoute
from u8co_api.http import api_error_detail

IDEM_ACTION = "co:idempotency/get"


def run_idem_get(request, caller, action: str, bridge_path: str, body: IdemGetIn) -> dict[str, Any]:
    """转给桥：原请求的桥路由、键、与原请求相同的 caller。存的是失败响应时换成本服务的错误格式和状态。"""
    payload = prepare_call(request, caller, action, body)
    # 这条路由本身不收 Idempotency-Key 头（带了照常 400）。
    idempotency_fields(request.headers, caller, bridge_path)
    payload.pop("path", None)
    payload.pop("key", None)
    payload.update(lookup_fields(caller, body.path, body.key))
    return translate(bridge_of(request, body.acc).call(bridge_path, payload))


def translate(result: dict[str, Any]) -> dict[str, Any]:
    status = result.get("status")
    if not result.get("found") or not isinstance(status, int) or 200 <= status < 300:
        return result
    stored = result.get("response")
    error = to_api_error(status, stored if isinstance(stored, dict) else None)
    out = dict(result)
    out["status"] = error.status
    # 与正常错误响应同一个实现（http.api_error_detail）。
    detail = api_error_detail(error)
    out["response"] = {"error": detail}
    return out


AI_ROUTES = (
    CoRoute(
        "/v1/co/archives/resolve",
        "/v1/archives/resolve",
        ResolveIn,
        ResolveOut,
        RESOLVE_SUMMARY,
        RESOLVE_HELP,
        "coArchiveResolve",
        "co:archives/resolve",
        TAG_ARC,
    ),
    CoRoute(
        "/v1/co/idempotency/get",
        "/v1/idempotency/get",
        IdemGetIn,
        IdemGetOut,
        IDEM_SUMMARY,
        IDEM_HELP,
        "coIdempotencyGet",
        IDEM_ACTION,
        TAG_CO,
        runner=run_idem_get,
    ),
)
