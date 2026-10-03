"""一条 /v1/co POST 路由的执行：预演分流、查询参数 fields / compact 的裁剪。

fields / compact 只作用在自由格式的容器上（co_shape）。出参里 head / lines / items / fields 是带必填字段的
类型化模型的路由（例如 vouchers/close 的 lines、核销的 items），裁剪会让响应校验失败，所以这些路由拒绝
fields 和 compact（400，field 为 fields / compact）；预演响应（DryRunOut）不受此限。
"""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass
from functools import cache
from typing import Any

from fastapi import Query
from pydantic import BaseModel

from u8co_api.co_dryrun import dry_response, is_dry
from u8co_api.co_service import run_call
from u8co_api.co_shape import parse_fields, shape
from u8co_api.errors import bad_request
from u8co_api.http import UnicodeJSONResponse

_CONTAINERS = ("head", "lines", "items", "fields")

FIELDS_QUERY = Query(
    None,
    max_length=4000,
    description="只返回这些键（逗号分隔，最多 100 项，大小写不敏感）。只裁剪 head、lines、items、fields 和预演的 "
    "docs[].head、docs[].lines，信封里的键不动。可带前缀 head. lines. items. fields. docs.head. docs.lines. "
    "只作用于那个容器。",
)
COMPACT_QUERY = Query(
    False,
    description="true 时在上述容器里去掉值为 null、空串、[] 或 {} 的键（0 和 false 保留）。",
)


@dataclass(frozen=True)
class View:
    fields: str | None = None
    compact: bool = False


def call_route(route, request, caller, body, view: View) -> Any:
    dry = is_dry(route.bridge_path, body)
    # 审计行记下预演，免得预演的删除看起来像真删除（audit.write_audit）；
    # arap/writeoff/auto 的 dry_run（只算计划）同样不写入，也记为 true。
    request.state.dry_run = getattr(body, "dry_run", False) is True
    parse_fields(view.fields)
    if not dry:
        _check_shapeable(route, view)
    runner = route.runner or run_call
    result = runner(request, caller, route.action, route.bridge_path, body)
    if dry:
        # 预演不按路由的正式出参校验：直接返回 DryRunOut 的 JSON。
        return UnicodeJSONResponse(dry_response(result, view.fields, view.compact))
    return shape(result, view.fields, view.compact)


def _check_shapeable(route, view: View) -> None:
    if shapeable(route.out_model):
        return
    if view.fields is not None:
        raise bad_request("该接口不支持 fields", field="fields")
    if view.compact:
        raise bad_request("该接口不支持 compact", field="compact")


@cache
def shapeable(out_model: type) -> bool:
    """出参的 head / lines / items / fields 里没有带必填字段的模型时才能裁剪。"""
    fields = getattr(out_model, "model_fields", {})
    return not any(_has_required(fields[name].annotation) for name in _CONTAINERS if name in fields)


def _has_required(annotation: object) -> bool:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return any(info.is_required() for info in annotation.model_fields.values())
    origin = typing.get_origin(annotation)
    if origin is None and not isinstance(annotation, types.UnionType):
        return False
    return any(_has_required(arg) for arg in typing.get_args(annotation))
