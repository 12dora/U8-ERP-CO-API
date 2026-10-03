"""幂等键：全部写路由接受 Idempotency-Key 头，转成桥请求体里的 idempotency_key 和 caller。

记录只存在桥上（桥是真正调用 U8 的地方，多个 API 副本也共用一份）。caller 取「信任项:客户端」，
令牌有 sub 时再加「:sub」（auth.caller_key）：不同调用方、同一客户端下的不同用户用同一个键互不影响，
idempotency/get 也只查得到自己的记录。
"""

from __future__ import annotations

import hashlib
import re

from u8co_api.auth import Caller, caller_key
from u8co_api.co_access import ACCESS, WRITE
from u8co_api.errors import bad_request

HEADER = "Idempotency-Key"

# 支持幂等键的桥路径 = co_access 里全部写动作（co:<路径> ↔ 桥 /v1/<路径>），不另列清单；桥侧同为 WriteGate 的写路由。
_WRITE_ROUTES = sorted(action.removeprefix("co:") for action, level in ACCESS.items() if level == WRITE)
IDEMPOTENT_PATHS = frozenset("/v1/" + route for route in _WRITE_ROUTES)
# idempotency/get 的 path：原请求的 API 路径，按字母序（OpenAPI 枚举稳定）。
IDEMPOTENT_API_PATHS = tuple("/v1/co/" + route for route in _WRITE_ROUTES)

# 只在 API 层组合的写路由，桥上记录记在它实际调用的桥路由下：查询时换成那条路由。
API_ALIASES = {"/v1/co/intercompany/generate_buyer": "/v1/co/vouchers/generate"}

# 桥上记录按请求路径（带 /u8co 前缀，同桥 IdemReq.Routes）算存储键。
BRIDGE_PREFIX = "/u8co"

_KEY = re.compile(r"^[\x21-\x7e]{1,128}\Z")
_CALLER_LIMIT = 200
_BAD_KEY = "Idempotency-Key 必须是 1 到 128 个可见 ASCII 字符，不能含空格"


def idempotency_fields(headers, caller: Caller, bridge_path: str) -> dict[str, str]:
    """没带头返回空字典；带了头但路径不支持、重复或格式不对都返回 400，不访问桥。"""
    values = headers.getlist(HEADER)
    if not values:
        return {}
    if bridge_path not in IDEMPOTENT_PATHS:
        raise bad_request("该接口不支持 Idempotency-Key")
    if len(values) != 1 or _KEY.fullmatch(values[0]) is None:
        raise bad_request(_BAD_KEY)
    return {"idempotency_key": values[0], "caller": caller_tag(caller)}


def caller_tag(caller: Caller) -> str:
    # 超过 200 字符或含控制字符时换成摘要，仍然每个调用方唯一。
    text = caller_key(caller)
    if len(text) <= _CALLER_LIMIT and text.isprintable():
        return text
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def lookup_fields(caller: Caller, api_path: str, key: str) -> dict[str, str]:
    """idempotency/get 转给桥的字段：原请求的桥路由、键和与原请求相同算法的 caller（桥据此算出同一个记录）。

    公司间生成买方单据（intercompany/generate_buyer）的记录在买方桥的 vouchers/generate 下，按那条路由查。
    """
    bridge_path = API_ALIASES.get(api_path, api_path).replace("/v1/co/", "/v1/", 1)
    if bridge_path not in IDEMPOTENT_PATHS:
        raise bad_request("该路径不支持 Idempotency-Key", field="path")
    if _KEY.fullmatch(key) is None:
        raise bad_request(_BAD_KEY, field="key")
    return {"route": BRIDGE_PREFIX + bridge_path, "idempotency_key": key, "caller": caller_tag(caller)}


def idempotency_openapi(bridge_path: str) -> dict | None:
    """给支持的路由在 OpenAPI 里补上 Idempotency-Key 头的说明。"""
    if bridge_path not in IDEMPOTENT_PATHS:
        return None
    return {
        "parameters": [
            {
                "name": HEADER,
                "in": "header",
                "required": False,
                "description": (
                    "幂等键，1 到 128 个可见 ASCII 字符。同一调用方、账套、路径和键在 24 小时内只执行一次："
                    "已完成的原样重放，还在执行的等待同一结果，内容不同返回 409 idempotency_mismatch。"
                ),
                "schema": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[\\x21-\\x7e]+$",
                },
            }
        ]
    }
