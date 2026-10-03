"""工具参数校验：只支持工具定义里用到的 JSON Schema 子集。返回第一处错误的中文说明，没错返回空串。"""

from __future__ import annotations

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
}


def validate(value: object, schema: dict, where: str = "arguments") -> str:
    kind = schema.get("type")
    problem = _type_problem(value, kind, where)
    if problem:
        return problem
    if "enum" in schema and value not in schema["enum"]:
        return f"{where} 不是允许的值"
    if kind == "object":
        return _object(value, schema, where)
    if kind == "array":
        return _array(value, schema, where)
    if kind == "string":
        return _string(value, schema, where)
    if kind == "integer":
        return _integer(value, schema, where)
    return ""


def _type_problem(value: object, kind: object, where: str) -> str:
    if kind is None:
        return ""
    if kind == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, _TYPES[kind])
    return "" if ok else f"{where} 必须是 {kind}"


def _object(value: dict, schema: dict, where: str) -> str:
    props = schema.get("properties") or {}
    for key in schema.get("required") or ():
        if key not in value:
            return f"{where} 缺少 {key}"
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(props))
        if extra:
            return f"{where} 有未知的参数：{', '.join(extra)}"
    for key, sub in props.items():
        if key in value:
            problem = validate(value[key], sub, f"{where}.{key}" if where != "arguments" else key)
            if problem:
                return problem
    return ""


def _array(value: list, schema: dict, where: str) -> str:
    if len(value) < schema.get("minItems", 0):
        return f"{where} 至少 {schema['minItems']} 项"
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        return f"{where} 最多 {schema['maxItems']} 项"
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            problem = validate(item, items, f"{where}.{index}")
            if problem:
                return problem
    return ""


def _string(value: str, schema: dict, where: str) -> str:
    if len(value) < schema.get("minLength", 0):
        return f"{where} 不能为空" if schema.get("minLength") == 1 else f"{where} 太短"
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        return f"{where} 最长 {schema['maxLength']} 个字符"
    return ""


def _integer(value: int, schema: dict, where: str) -> str:
    low = schema.get("minimum")
    high = schema.get("maximum")
    if (low is not None and value < low) or (high is not None and value > high):
        return f"{where} 必须在 {low} 到 {high} 之间"
    return ""
