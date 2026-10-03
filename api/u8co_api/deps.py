"""路由依赖：从 Authorization 头取出并校验调用方。"""

from __future__ import annotations

from fastapi import Request

from u8co_api.auth import Caller, bearer_token, caller_key, verify_bearer


def current_caller(request: Request) -> Caller:
    token = bearer_token(request.headers.get("authorization"))
    caller = verify_bearer(token, request.app.state.settings, request.app.state.jwks)
    request.state.caller_id = caller.client_id
    request.state.trust_name = caller.name
    # 名额键（同 co_routes.co_slot）、令牌 sub 与是否信任终端用户头（co_service.remember_user 用）。
    request.state.caller_key = caller_key(caller)
    request.state.token_sub = caller.subject or None
    request.state.on_behalf_header = caller.on_behalf_header
    return caller
