"""ASGI 应用。运行：u8co-api，或 uvicorn --factory u8co_api.main:create_app。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI, Request

from u8co_api.audit import configure_audit, install_audit
from u8co_api.co_client import BridgeRoutes, build_bridge, build_routes
from u8co_api.co_clock import set_zone
from u8co_api.co_routes import register_co_routes
from u8co_api.config import Settings, load_settings, parse_zone
from u8co_api.http import UnicodeJSONResponse, install_errors
from u8co_api.jwks import JwksCache, JwksRegistry
from u8co_api.limit import Limit
from u8co_api.write_policy import load_write_policy


@dataclass
class AppDeps:
    settings: Settings | None = None
    jwks: JwksCache | JwksRegistry | None = None
    co_bridge: object | None = None
    co_routes: BridgeRoutes | None = None
    co_limit: Limit | None = None
    co_global_limit: Limit | None = None


def create_app(deps: AppDeps | None = None) -> FastAPI:
    chosen = deps or AppDeps()
    settings = chosen.settings or load_settings()
    configure_audit(settings.audit_log)
    set_zone(parse_zone(settings.timezone))
    # 公开的 /docs、/redoc、/openapi.json 关闭；文档在 /v1/openapi.json，需要令牌。
    app = FastAPI(
        title="u8co-api",
        version=_version(),
        default_response_class=UnicodeJSONResponse,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    _install_state(app, settings, chosen)
    install_errors(app)
    install_audit(app)
    app.add_api_route("/healthz", healthz, methods=["GET"], include_in_schema=False)
    register_co_routes(app)
    _warn_missing(settings)
    return app


def healthz(request: Request) -> dict:
    # 只说明进程活着，不访问桥，也不访问身份提供方。
    settings = request.app.state.settings
    body = {"ok": True, "configured": settings.enabled and settings.configured}
    # 配了分流时只报分流桥个数：这个端点不要令牌，不列账套号。
    routed = len(request.app.state.co_routes.entries)
    if routed:
        body["bridge_routes"] = routed
    return body


def _install_state(app: FastAPI, settings: Settings, chosen: AppDeps) -> None:
    app.state.settings = settings
    app.state.jwks = chosen.jwks if chosen.jwks is not None else _registry(settings)
    app.state.co_bridge = chosen.co_bridge if chosen.co_bridge is not None else build_bridge(settings)
    # 按账套分流的桥（U8CO_BRIDGE_ROUTES_FILE）；没配时为空，所有账套走 co_bridge。
    app.state.co_routes = chosen.co_routes if chosen.co_routes is not None else build_routes(settings)
    # 写入策略（U8CO_WRITE_POLICY_FILE）；没配时为 None，写入不受限制。
    app.state.write_policy = load_write_policy(settings.write_policy_file, parse_zone(settings.timezone))
    # 每个调用方的在途上限低于全局，避免一个调用方占满桥。
    app.state.co_limit = chosen.co_limit or Limit(
        rpm=_positive(settings.rpm),
        concurrency=_positive(settings.caller_concurrency),
    )
    app.state.co_global_limit = chosen.co_global_limit or Limit(
        rpm=1_000_000,
        concurrency=_positive(settings.concurrency),
    )
    _prefetch(app.state.jwks)


def _positive(value: int) -> int:
    if value >= 1:
        return value
    return 1


def _prefetch(keys) -> None:
    method = getattr(keys, "prefetch", None)
    if method is not None:
        method()


def _registry(settings: Settings) -> JwksRegistry:
    return JwksRegistry(tuple(item.key_source for item in settings.trust))


def _warn_missing(settings: Settings) -> None:
    missing = settings.missing()
    if missing:
        logging.getLogger("u8co.api").warning("配置不完整，相关调用会被拒绝: %s", ", ".join(missing))


def _version() -> str:
    try:
        return version("u8co-api")
    except PackageNotFoundError:
        return "0.0.0"


SHUTDOWN_GRACE_DEFAULT = 100


def shutdown_grace() -> int:
    """停机时等在途请求的秒数（U8CO_SHUTDOWN_GRACE，缺省 100）。不是正整数时用缺省值并记警告。

    普通请求桥最多等 75 秒，100 秒够用；要让在途的存货核算长时操作也走完，设成不小于 U8CO_BRIDGE_LONG_TIMEOUT。"""
    raw = os.environ.get("U8CO_SHUTDOWN_GRACE", "").strip()
    if raw == "":
        return SHUTDOWN_GRACE_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        logging.getLogger("u8co.api").warning("U8CO_SHUTDOWN_GRACE 不是正整数，按 %d 秒", SHUTDOWN_GRACE_DEFAULT)
        return SHUTDOWN_GRACE_DEFAULT
    return value


def main() -> None:
    import uvicorn

    host = os.environ.get("U8CO_API_HOST", "").strip() or "0.0.0.0"
    port = int(os.environ.get("U8CO_API_PORT", "").strip() or "8080")
    # 停机时给在途的写操作留足时间（见 shutdown_grace）。
    uvicorn.run(
        "u8co_api.main:create_app",
        factory=True,
        host=host,
        port=port,
        timeout_graceful_shutdown=shutdown_grace(),
    )
