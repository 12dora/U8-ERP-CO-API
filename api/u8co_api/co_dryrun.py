"""写操作预演（dry_run）的 API 侧处理：只在为 true 时转给桥，拒绝与 Idempotency-Key 同用，
按 DryRunOut 校验预演响应，并在 OpenAPI 里给 200 响应补上预演的形状。"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from u8co_api.co_idem import HEADER
from u8co_api.co_models_dry import DryRunOut
from u8co_api.co_shape import shape
from u8co_api.errors import bad_gateway, bad_request, timeout_error

FIELD = "dry_run"
# arap/writeoff/auto 的 dry_run 是原有的「只算计划」，照旧原样转给桥，响应仍按该路由自己的模型。
PLAN_ONLY = frozenset({"/v1/arap/writeoff/auto"})
OPENAPI_FLAG = "x-u8co-dry-run"
_SCHEMA_PREFIX = "#/components/schemas/"
_NOTE = "dry_run 为 true 时返回预演结果 DryRunOut（什么都没有写入）。"


def supports(route) -> bool:
    """请求体声明了 dry_run、又不是只算计划的路由：预演走 DryRunOut。"""
    fields = getattr(route.body_model, "model_fields", {})
    return FIELD in fields and route.bridge_path not in PLAN_ONLY


def is_dry(bridge_path: str, body: object) -> bool:
    return bridge_path not in PLAN_ONLY and getattr(body, FIELD, False) is True


def take(payload: dict[str, Any], bridge_path: str, headers) -> bool:
    """dry_run 只在为 true 时留在桥请求体里（正式写入的请求体与以前完全相同）。返回是否预演。
    arap/writeoff/auto 的 dry_run 原样保留（只算计划），但计划同样不能带 Idempotency-Key（不能把计划存成结果）。"""
    if bridge_path in PLAN_ONLY:
        if payload.get(FIELD) is True and headers.getlist(HEADER):
            raise bad_request("预演不能带 Idempotency-Key", field=FIELD)
        return False
    if FIELD not in payload:
        return False
    if payload.pop(FIELD) is not True:
        return False
    if headers.getlist(HEADER):
        raise bad_request("预演不能带 Idempotency-Key", field=FIELD)
    payload[FIELD] = True
    return True


def dry_response(result: dict[str, Any], fields: str | None, compact: bool) -> dict[str, Any]:
    """预演响应不按路由的正式出参校验，而按 DryRunOut。桥没有按预演执行时写入可能已生效，按 504 处理。"""
    if not isinstance(result, dict) or result.get(FIELD) is not True:
        raise timeout_error("CO 桥没有按预演执行，写入可能已生效，请核对", "outcome_unknown")
    shaped = shape(result, fields, compact)
    try:
        model = DryRunOut.model_validate(shaped)
    except ValidationError as exc:
        raise bad_gateway("CO 桥的预演响应格式不对", "bad_response") from exc
    return model.model_dump(mode="json", exclude_none=True)


def document(paths: dict, schemas: dict) -> None:
    """给标了 x-u8co-dry-run 的写操作把 200 响应改成「正式出参 或 DryRunOut」，并补上 DryRunOut 的 schema。"""
    marked = [op for item in paths.values() for op in item.values() if isinstance(op, dict) and op.get(OPENAPI_FLAG)]
    if not marked:
        return
    schemas.update(_dry_schemas())
    ref = {"$ref": _SCHEMA_PREFIX + DryRunOut.__name__}
    for operation in marked:
        ok = (operation.get("responses") or {}).get("200")
        content = ((ok or {}).get("content") or {}).get("application/json")
        if not isinstance(content, dict) or "schema" not in content:
            continue
        content["schema"] = {"anyOf": [content["schema"], ref]}
        ok["description"] = (ok.get("description") or "") + "。" + _NOTE


def _dry_schemas() -> dict:
    schema = DryRunOut.model_json_schema(ref_template=_SCHEMA_PREFIX + "{model}", mode="serialization")
    found = dict(schema.pop("$defs", {}))
    found[DryRunOut.__name__] = schema
    return found
