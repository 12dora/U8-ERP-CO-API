"""身份绑定服务（mgmt.person_proxy）客户端：incoming 方式下以调用者本人绑定的 U8 操作员执行经营管理查询。

协议（每次工具调用一次请求；单账套写 acc，多账套写 accs，全有或全无）：
    POST {url}
    Authorization: Bearer <person_proxy.token_file 里的服务令牌>
    X-U8co-Caller-Token: <调用者本次请求的 Bearer 令牌，原样>
    {"acc": "801" | "accs": ["801", "802"], "route": "reports/mgmt/pnl", "body": {...不含任何登录...}}
响应：
    200 {"ok": true, "data": <API 的 JSON>}
    409 {"error": {"code": "binding_missing", "accs_missing": ["802"]?}}   调用者在账套没有绑定 U8 操作员
    403 {"error": {"code": "mgmt_forbidden" | "no_permission"}}
    400 {"error": {"code": "forbidden_field"}}                               body 里带了登录相关的键

本服务从不发送 U8 口令，也不在日志里写两个令牌；调用者令牌由身份绑定服务（或 API）验签。
"""

from __future__ import annotations

import json
import re

from u8co_mcp.config import PersonProxyConfig
from u8co_mcp.http import USER_AGENT, Send, TransportError

CALLER_HEADER = "X-U8co-Caller-Token"
# 请求体里不允许出现的键（纵深防御：工具参数的 schema 已经不允许，这里再查一次）。
_LOGIN_KEYS = frozenset({"logins", "login", "acc", "accs", "operator", "password", "password_enc"})
_RETRYABLE = (429, 502, 503, 504)
_ACC = re.compile(r"^\d{3}\Z")
_MESSAGES = {
    "binding_missing": "你在账套 {acc} 没有绑定 U8 操作员，不能按你的 U8 权限查询经营数据",
    "mgmt_forbidden": "你没有账套 {acc} 的经营管理查询权限",
    "no_permission": "你绑定的 U8 操作员在账套 {acc} 没有这项查询的功能权限",
}
_HINTS = {
    "binding_missing": "先在身份绑定服务里绑定本人在该账套的 U8 操作员，再查询",
    "mgmt_forbidden": "向管理员申请该账套的经营管理查询权限",
    "no_permission": "请 U8 管理员给你的操作员分配对应的查询功能权限",
}


class ProxyFailure(Exception):
    """payload 同工具错误结果：{"status", "error": {"code", "message", "retryable", ...}}。"""

    def __init__(self, payload: dict):
        super().__init__(payload["error"]["code"])
        self.payload = payload


def call(cfg: PersonProxyConfig, send: Send, tokens: tuple[str, str], request: tuple[list[str], str, dict]) -> dict:
    """tokens：(服务令牌, 调用者令牌)；request：(账套列表, 路由名, 请求体)。成功返回 data（JSON 对象）。"""
    service_token, caller_token = tokens
    accs, route, body = request
    bad = sorted(k for k in body if str(k).lower() in _LOGIN_KEYS)
    if bad:
        raise AssertionError("经营管理请求体不能带登录信息")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": USER_AGENT,
        "Authorization": "Bearer " + service_token,
        CALLER_HEADER: caller_token,
    }
    target = {"acc": accs[0]} if len(accs) == 1 else {"accs": list(accs)}
    data = json.dumps(dict(target, route=route, body=body), ensure_ascii=False).encode("utf-8")
    try:
        resp = send("POST", cfg.url, headers, data, cfg.timeout_s)
    except TransportError as exc:
        raise _failure(0, "unavailable", f"连不上身份绑定服务（{exc}）", True) from None
    payload = resp.json()
    if resp.status == 200:
        if isinstance(payload, dict) and payload.get("ok") is True and isinstance(payload.get("data"), dict):
            return _whole(payload["data"])
        raise _failure(200, "bad_response", "身份绑定服务返回了无法解析的响应", False)
    raise _error(resp.status, payload, accs)


def _error(status: int, payload: object, accs: list[str]) -> ProxyFailure:
    error = payload.get("error") if isinstance(payload, dict) else None
    error = error if isinstance(error, dict) else {}
    code = error.get("code")
    acc = "、".join(accs)
    if status == 403 and code not in _MESSAGES:
        # 未知的拒绝码一律按无权处理；对方的消息一律不回显（可能带别的账套或往来单位的数字）。
        code = "mgmt_forbidden"
    if status in (403, 409) and code in _MESSAGES:
        return _denied(status, code, error, accs)
    if status == 401:
        message = "身份绑定服务拒绝了本服务的令牌（检查 mgmt.person_proxy.token_file）"
        return _failure(status, "proxy_unauthorized", message, False)
    retryable = status in _RETRYABLE
    message = f"身份绑定服务返回 HTTP {status}（账套 {acc}）"
    return _failure(status, code if _plain_code(code) else "bad_response", message, retryable)


def _plain_code(code: object) -> bool:
    """只透传形如 snake_case 的短错误码，其余（可能夹带内容）一律换成 bad_response。"""
    return isinstance(code, str) and len(code) <= 64 and code.isascii() and code.replace("_", "").isalnum()


def _denied(status: int, code: str, error: dict, accs: list[str]) -> ProxyFailure:
    missing = _missing(error.get("accs_missing"), accs) if code == "binding_missing" else []
    failure = _failure(status, code, _MESSAGES[code].format(acc="、".join(missing or accs)), False)
    failure.payload["error"]["hint"] = _HINTS[code]
    if missing:
        failure.payload["error"]["accs_missing"] = missing
    return failure


def _whole(data: dict) -> dict:
    """全有或全无：API 部分账套失败时仍回 200（complete 为 false），这时整体按失败处理，不返回已取到的账套。"""
    if data.get("complete") is False:
        raise _failure(200, "incomplete", "部分账套查询失败，为免只给出部分数据，本次不返回结果；稍后重试", True)
    return data


def _missing(raw: object, accs: list[str]) -> list[str]:
    """accs_missing 只保留本次请求里的三位账套号，其余一概丢弃。"""
    if not isinstance(raw, list):
        return []
    return [a for a in accs if a in raw and _ACC.match(a)]


def _failure(status: int, code: str, message: str, retryable: bool) -> ProxyFailure:
    return ProxyFailure({"status": status, "error": {"code": code, "message": message, "retryable": retryable}})
