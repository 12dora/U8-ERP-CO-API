"""工具定义（名称、说明、输入 JSON Schema）和包内资源（routes.json、guide.md）。

路由名是 API 路径去掉 /v1/co/ 前缀，例如 vouchers/create、reports/gl_balance、health、meta。
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

GET_ROUTES = ("health", "meta")


@lru_cache(maxsize=1)
def load_routes() -> tuple[dict, ...]:
    text = resources.files("u8co_mcp").joinpath("routes.json").read_text(encoding="utf-8")
    return tuple(json.loads(text))


@lru_cache(maxsize=1)
def load_guide() -> str:
    return resources.files("u8co_mcp").joinpath("guide.md").read_text(encoding="utf-8")


def route_names(access: str) -> list[str]:
    return [r["route"] for r in load_routes() if r["access"] == access]


def route_entry(route: str) -> dict | None:
    for entry in load_routes():
        if entry["route"] == route:
            return entry
    return None


def _obj(props: dict, required: tuple[str, ...] = ()) -> dict:
    schema = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema


_BODY = {
    "type": "object",
    "description": "请求体（不含 acc、operator、password，由本服务注入；year、date 可以覆盖）。字段用 u8_describe 查",
}


def tool_list(read_only: bool) -> list[dict]:
    tools = [_guide(), _describe(), _read(), _resolve(), _idem_get()]
    if not read_only:
        tools.insert(3, _write())
    return tools


def _guide() -> dict:
    return {
        "name": "u8_guide",
        "title": "U8 使用指南",
        "description": "先读这个：U8 CO 接口的工作流程（解析编码 → 查结构 → 预演 → 写入 → 核对）和出错规则。",
        "inputSchema": _obj({}),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    }


def _describe() -> dict:
    return {
        "name": "u8_describe",
        "title": "查路由、单据类型或档案结构",
        "description": (
            "不带参数：列出全部路由（route、access、summary）。route：该路由请求体的 JSON Schema。"
            "type：单据类型能做的操作、字段到档案的对应（field_refs），以及当前账套模板里的可写字段"
            "（fields：中文名 label、类型、必填、枚举值）；op / source 选新增、修改或参照生单的字段。"
            "archive：档案类型的结构和字段中文名。gl=true：总账凭证。route、type、archive、gl 一次只给一个。"
        ),
        "inputSchema": _obj(
            {
                "route": {"type": "string", "description": "路由名，如 vouchers/create"},
                "type": {"type": "string", "description": "单据类型，如 sale_order"},
                "op": {
                    "type": "string",
                    "enum": ["create", "update", "generate"],
                    "description": "配合 type：字段用于哪种写入，缺省 create",
                },
                "source": {"type": "string", "description": "配合 type 和 op=generate：来源单据类型"},
                "archive": {"type": "string", "description": "档案类型，如 customer"},
                "gl": {"type": "boolean", "description": "true：总账凭证的字段"},
            }
        ),
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    }


def _read() -> dict:
    return {
        "name": "u8_read",
        "title": "U8 只读调用",
        "description": "调用只读路由（读取、列表、报表、审批状态等）。health、meta 不带 body。",
        "inputSchema": _obj(
            {
                "route": {"type": "string", "enum": route_names("read"), "description": "只读路由名"},
                "body": _BODY,
                "fields": {
                    "type": "string",
                    "description": "只返回这些字段，逗号分隔，可加 head. / lines. / items. / fields. 前缀",
                },
                "compact": {"type": "boolean", "default": True, "description": "去掉空值字段，缺省 true"},
            },
            ("route",),
        ),
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    }


def _write() -> dict:
    return {
        "name": "u8_write",
        "title": "U8 写入调用",
        "description": (
            "调用写路由。先用 dry_run=true 预演（不落库），确认后再正式写。"
            "正式写入（非预演）没给 idempotency_key 时自动生成并在结果里返回。504 outcome_unknown 时不要直接重试，"
            "先用 u8_idempotency_get 或读取核对。"
        ),
        "inputSchema": _obj(
            {
                "route": {"type": "string", "enum": route_names("write"), "description": "写路由名"},
                "body": _BODY,
                "dry_run": {"type": "boolean", "default": False, "description": "预演：执行校验后回滚，不写入"},
                "idempotency_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "幂等键，1 到 128 个可见 ASCII 字符；重试同一请求时带上原来的键，预演不能带",
                },
            },
            ("route", "body"),
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    }


def _resolve() -> dict:
    item = _obj(
        {
            "archive": {"type": "string", "minLength": 1, "description": "档案类型，如 customer、inventory"},
            "q": {"type": "string", "minLength": 1, "maxLength": 100, "description": "名称、简称、助记码或编码"},
        },
        ("archive", "q"),
    )
    return {
        "name": "u8_resolve",
        "title": "名称解析成编码",
        "description": "把客户、存货、部门等的名称解析成 U8 编码。status：exact 唯一命中，ambiguous 多个，"
        "partial 只有模糊命中，none 没有。写单据前先解析，不要猜编码。",
        "inputSchema": _obj(
            {
                "items": {"type": "array", "minItems": 1, "maxItems": 20, "items": item},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "每项最多候选数，缺省 5"},
                "include_disabled": {"type": "boolean", "description": "包括已停用的档案，缺省 false"},
            },
            ("items",),
        ),
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    }


def _idem_get() -> dict:
    return {
        "name": "u8_idempotency_get",
        "title": "按幂等键查写入结果",
        "description": "写入返回 504 outcome_unknown 或连接中断后，用同一个幂等键查第一次写入的结果。",
        "inputSchema": _obj(
            {
                "route": {"type": "string", "enum": route_names("write"), "description": "原写入的路由名"},
                "key": {"type": "string", "minLength": 1, "maxLength": 128, "description": "原写入用的幂等键"},
            },
            ("route", "key"),
        ),
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    }
