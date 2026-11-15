"""MCP 的 JSON-RPC 2.0 分派。handle_line 收一行文本，返回要写回的一行（通知返回 None）。"""

from __future__ import annotations

import json
import logging

from u8co_mcp import __version__
from u8co_mcp.app import App, UnknownTool
from u8co_mcp.tools import load_guide

log = logging.getLogger("u8co_mcp")

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "u8co-mcp"
GUIDE_URI = "u8co://guide"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
RESOURCE_NOT_FOUND = -32002

_INSTRUCTIONS = (
    "用友 U8+ 业务接口。先调 u8_guide 读工作流程；写入前用 u8_resolve 把名称解析成编码，"
    "用 u8_describe 查字段，用 u8_write 的 dry_run=true 预演。"
)
# 托管（incoming）方式只提供指南和经营管理工具。
_INSTRUCTIONS_MGMT = "用友 U8+ 经营管理查询。先调 u8_guide；u8_mgmt_overview 看关键指标，其余 u8_mgmt_* 看利润、销售、往来和资金存货。"


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Protocol:
    def __init__(self, app: App):
        self.app = app
        self.methods = {
            "initialize": self._initialize,
            "ping": lambda params: {},
            "tools/list": lambda params: {"tools": self.app.list_tools()},
            "tools/call": self._tools_call,
            "resources/list": self._resources_list,
            "resources/read": self._resources_read,
            "prompts/list": lambda params: {"prompts": []},
        }

    def handle_line(self, line: str) -> str | None:
        try:
            message = json.loads(line)
        except Exception:  # 含 RecursionError（嵌套过深）；解析失败都按 -32700 回，继续服务
            return _dump(_error(None, PARSE_ERROR, "不是合法的 JSON"))
        if isinstance(message, list):
            if not message:
                return _dump(_error(None, INVALID_REQUEST, "空的批量请求"))
            replies = [r for r in (self.handle(item) for item in message) if r is not None]
            return _dump(replies) if replies else None
        reply = self.handle(message)
        return None if reply is None else _dump(reply)

    def handle(self, message: object) -> dict | None:
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "请求必须是 JSON 对象")
        msg_id = message.get("id")
        is_request = "id" in message
        if msg_id is not None and (isinstance(msg_id, bool) or not isinstance(msg_id, (str, int, float))):
            return _error(None, INVALID_REQUEST, "id 必须是字符串或数字")
        method = message.get("method")
        if not isinstance(method, str):
            # 客户端发来的响应（有 result / error）直接忽略。
            if "result" in message or "error" in message:
                return None
            return _error(msg_id, INVALID_REQUEST, "缺少 method")
        if message.get("jsonrpc") != "2.0":
            return _error(msg_id, INVALID_REQUEST, "jsonrpc 必须是 2.0") if is_request else None
        if not is_request:
            return None  # 通知（notifications/initialized、notifications/cancelled 等）不回
        return self._request(msg_id, method, message.get("params"))

    def _request(self, msg_id: object, method: str, params: object) -> dict:
        handler = self.methods.get(method)
        if handler is None:
            return _error(msg_id, METHOD_NOT_FOUND, f"不支持的方法 {method}")
        if params is not None and not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "params 必须是对象")
        try:
            result = handler(params or {})
        except RpcError as exc:
            return _error(msg_id, exc.code, exc.message)
        except Exception as exc:  # 兜底，细节只进 stderr
            log.error("处理 %s 出错：%s", method, type(exc).__name__)
            return _error(msg_id, INTERNAL_ERROR, "内部错误")
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _initialize(self, params: dict) -> dict:
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False, "subscribe": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {"name": SERVER_NAME, "title": "U8 CO", "version": __version__},
            "instructions": _INSTRUCTIONS_MGMT if self.app.incoming else _INSTRUCTIONS,
        }

    def _tools_call(self, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments")
        if not isinstance(name, str):
            raise RpcError(INVALID_PARAMS, "缺少工具名 name")
        if args is not None and not isinstance(args, dict):
            raise RpcError(INVALID_PARAMS, "arguments 必须是对象")
        log.info("tools/call %s", name)
        try:
            return self.app.call(name, args)
        except UnknownTool:
            raise RpcError(INVALID_PARAMS, f"没有工具 {name}") from None

    def _resources_list(self, params: dict) -> dict:
        return {
            "resources": [
                {
                    "uri": GUIDE_URI,
                    "name": "u8co-guide",
                    "title": "U8 CO 使用指南",
                    "description": "工作流程、预演、幂等和出错规则",
                    "mimeType": "text/markdown",
                }
            ]
        }

    def _resources_read(self, params: dict) -> dict:
        uri = params.get("uri")
        if not isinstance(uri, str):
            raise RpcError(INVALID_PARAMS, "缺少 uri")
        if uri != GUIDE_URI:
            raise RpcError(RESOURCE_NOT_FOUND, f"没有资源 {uri}")
        return {"contents": [{"uri": GUIDE_URI, "mimeType": "text/markdown", "text": load_guide()}]}


def _error(msg_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _dump(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
