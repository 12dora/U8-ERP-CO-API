"""JSON 响应和错误处理。错误消息是中文。"""

from __future__ import annotations

import json
import logging
import re

from fastapi import FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from u8co_api.errors import ApiError, default_hint, is_retryable

_AUTH = {"WWW-Authenticate": "Bearer"}
_SEGMENT = re.compile(r"[A-Za-z0-9_\-]+")
_FIELD_MAX = 200
_BARE_QUERY = frozenset({"fields", "compact"})
_TYPE_TAGS = frozenset({"str", "int", "float", "bool", "none", "bytes", "decimal", "date", "datetime"})

_HTTP = {
    400: ("bad_request", "请求参数无效"),
    401: ("unauthorized", "缺少或无效的访问令牌"),
    403: ("forbidden", "无权访问"),
    404: ("not_found", "路径不存在"),
    405: ("bad_request", "方法不被接受"),
    409: ("conflict", "请求与当前状态冲突"),
    422: ("unprocessable", "请求无法处理"),
    429: ("rate_limited", "请求过于频繁"),
    502: ("bad_gateway", "上游服务错误"),
}


class UnicodeJSONResponse(JSONResponse):
    def render(self, content: object) -> bytes:
        return json.dumps(content, ensure_ascii=False).encode("utf-8")


def install_errors(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(FastAPIHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected)


def _body(status: int, detail: dict, retry_after: int | None = None) -> UnicodeJSONResponse:
    headers = dict(_AUTH) if status == 401 else {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return UnicodeJSONResponse({"error": detail}, status_code=status, headers=headers or None)


def error_detail(status: int, code: str, message: str, field: str | None = None, hint: str | None = None) -> dict:
    """错误体里 error 的内容。别处（如 idempotency/get 翻译存下的失败响应）也用它，保证形状一致。"""
    return _detail(status, code, message, field, hint)


def api_error_detail(exc: ApiError) -> dict:
    """ApiError 对应的 error 内容，含桥给的结构化 detail（只用于 4xx，401 除外）。"""
    detail = _detail(exc.status, exc.code, exc.message, exc.field, exc.hint)
    if exc.detail and 400 <= exc.status < 500 and exc.status != 401:
        detail["detail"] = exc.detail
    return detail


def _detail(status: int, code: str, message: str, field: str | None = None, hint: str | None = None) -> dict:
    # retryable 总是有；field 只用于 400；hint 没有具体提示时按错误码取默认，401/500 不给。
    detail: dict = {"code": code, "message": message, "retryable": is_retryable(code)}
    if field and status == 400:
        detail["field"] = field
    if status not in (401, 500):
        hint = hint or default_hint(status, code)
        if hint:
            detail["hint"] = hint
    return detail


def validation_field(errors: list) -> str | None:
    """FastAPI 校验错误 → 点分字段路径（取第一条）：去掉开头的 "body"，整数下标保留；
    联合类型各成员的标签（str、int、function-after[...] 等）不算字段，截掉。"""
    # json_invalid 的 loc 是 ("body", 字符位置)，不是字段。
    if not errors or not isinstance(errors[0], dict) or errors[0].get("type") == "json_invalid":
        return None
    loc = errors[0].get("loc")
    if not isinstance(loc, (list, tuple)):
        return None
    parts = list(loc)
    if parts and parts[0] == "body":
        parts = parts[1:]
    # 查询参数 fields、compact 与 co_call 的检查一致，只报参数名。
    if len(parts) == 2 and parts[0] == "query" and parts[1] in _BARE_QUERY:
        parts = parts[1:]
    if _union_tagged(errors, tuple(loc)):
        parts = parts[:-1]
    return _join(parts)


def _is_tag(part: object) -> bool:
    return isinstance(part, str) and (part in _TYPE_TAGS or "[" in part)


def _union_tagged(errors: list, loc: tuple) -> bool:
    # 同一个位置有两条以上错误、只差最后一段且最后一段都是类型标签 → 是联合类型的逐成员报错。
    if not loc or not _is_tag(loc[-1]):
        return False
    lasts = [
        tuple(item["loc"])[-1]
        for item in errors
        if isinstance(item, dict)
        and isinstance(item.get("loc"), (list, tuple))
        and tuple(item["loc"])[:-1] == loc[:-1]
        and len(item["loc"]) == len(loc)
    ]
    return len(lasts) > 1 and all(_is_tag(last) for last in lasts)


def _join(parts: list) -> str | None:
    names: list[str] = []
    for part in parts:
        text = str(part) if isinstance(part, int) and not isinstance(part, bool) else part
        if not isinstance(text, str) or not _SEGMENT.fullmatch(text):
            break
        names.append(text)
    field = ".".join(names)
    if not field or len(field) > _FIELD_MAX:
        return None
    return field


async def _api_error(_request, exc: ApiError) -> UnicodeJSONResponse:
    return _body(exc.status, api_error_detail(exc), exc.retry_after)


async def _validation(_request, exc: RequestValidationError) -> UnicodeJSONResponse:
    field = validation_field(list(exc.errors()))
    message = f"请求参数无效：{field}" if field else "请求参数无效"
    return _body(400, _detail(400, "bad_request", message, field))


async def _http_error(_request, exc: StarletteHTTPException) -> UnicodeJSONResponse:
    code, message = _HTTP.get(exc.status_code, ("error", "请求无法处理"))
    return _body(exc.status_code, _detail(exc.status_code, code, message))


async def _unexpected(_request, _exc: Exception) -> UnicodeJSONResponse:
    logging.getLogger("u8co.api").exception("unhandled")
    return _body(500, _detail(500, "error", "服务器内部错误"))
