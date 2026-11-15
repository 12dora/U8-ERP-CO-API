"""u8_describe 的纯函数部分：从 OpenAPI 取请求体结构，从 meta 取单据类型和档案。

请求体结构展开 $ref（限深、防环），去掉由本服务注入的 acc / operator / password / password_enc，
输出按 UTF-8 字节数封顶（缺省 12 KiB），超了就逐级减少展开深度、再去掉说明。
"""

from __future__ import annotations

import json

AUTH_KEYS = ("acc", "operator", "password", "password_enc")
CAP_BYTES = 12 * 1024
_REF_PREFIX = "#/components/schemas/"
_DEPTHS = (8, 5, 3, 2)
_DESC_CAP = 1500
_SMALL = 400
_ENUM_KEEP = 10


def size_of(obj: object) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def catalog(routes: list[dict]) -> dict:
    return {"routes": [{k: r[k] for k in ("route", "access", "summary")} for r in routes]}


def describe_route(openapi: dict, entry: dict, idempotent: bool, cap: int = CAP_BYTES) -> dict:
    """entry 是 routes.json 的一项。找不到该路径时 request_schema 为 None。"""
    route = entry["route"]
    method, operation = find_operation(openapi, "/v1/co/" + route)
    out = {
        "route": route,
        "access": entry["access"],
        "summary": entry["summary"],
        "method": method.upper() if method else ("GET" if route in ("health", "meta") else "POST"),
        "description": _cut(str((operation or {}).get("description") or ""), _DESC_CAP),
        "idempotency_key": idempotent,
        "request_schema": None,
    }
    raw = _body_schema(operation)
    if raw is None:
        return out
    components = (openapi.get("components") or {}).get("schemas") or {}
    for depth in _DEPTHS:
        out["request_schema"] = strip_auth(resolve(raw, components, depth))
        if size_of(out) <= cap:
            return out
    out["request_schema"] = _drop_descriptions(out["request_schema"])
    out["description"] = _cut(out["description"], 300)
    if size_of(out) <= cap:
        return out
    top = strip_auth(resolve(raw, components, 1))
    props = top.get("properties") if isinstance(top, dict) else None
    out["request_schema"] = {"truncated": True, "properties": sorted(props) if isinstance(props, dict) else []}
    return out


def find_operation(openapi: dict, path: str) -> tuple[str, dict | None]:
    item = (openapi.get("paths") or {}).get(path)
    if not isinstance(item, dict):
        return "", None
    for method in ("post", "get"):
        if isinstance(item.get(method), dict):
            return method, item[method]
    return "", None


def _body_schema(operation: dict | None) -> object:
    if not operation:
        return None
    content = ((operation.get("requestBody") or {}).get("content") or {}).get("application/json") or {}
    return content.get("schema")


def resolve(node: object, components: dict, depth: int, stack: tuple[str, ...] = ()) -> object:
    """展开 #/components/schemas/ 引用。超过深度或成环时留下 {"$ref_name": 名字}。"""
    if isinstance(node, list):
        return [resolve(item, components, depth, stack) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
        name = ref[len(_REF_PREFIX) :]
        target = components.get(name)
        if depth <= 0 or name in stack or not isinstance(target, dict):
            return {"$ref_name": name}
        merged = dict(target)
        merged.update({k: v for k, v in node.items() if k != "$ref"})
        return resolve(merged, components, depth - 1, stack + (name,))
    return {key: resolve(value, components, depth, stack) for key, value in node.items()}


def strip_auth(schema: object) -> object:
    """去掉顶层（含顶层 allOf / anyOf / oneOf 分支）的账套、操作员、口令字段和示例。"""
    if not isinstance(schema, dict):
        return schema
    out = {k: v for k, v in schema.items() if k not in ("example", "examples")}
    props = out.get("properties")
    if isinstance(props, dict):
        out["properties"] = {k: v for k, v in props.items() if k not in AUTH_KEYS}
    required = out.get("required")
    if isinstance(required, list):
        out["required"] = [k for k in required if k not in AUTH_KEYS]
    for key in ("allOf", "anyOf", "oneOf"):
        if isinstance(out.get(key), list):
            out[key] = [strip_auth(item) for item in out[key]]
    return out


def _drop_descriptions(node: object) -> object:
    if isinstance(node, list):
        return [_drop_descriptions(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {k: _drop_descriptions(v) for k, v in node.items() if k not in ("description", "title")}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def describe_kind(meta: dict, name: str) -> dict | None:
    """meta.kinds 里名为 name 的一项，tables / blocked 太大时去掉；另附 field_refs。"""
    kind = _named(meta.get("kinds"), name)
    if kind is None:
        return None
    kind = {k: v for k, v in kind.items() if not (k in ("tables", "blocked") and size_of(v) > _SMALL)}
    return {"kind": kind, "field_refs": meta.get("field_refs") or {}}


def describe_gl(meta: dict) -> dict:
    return {"gl": meta.get("gl") or {}, "gl_field_refs": meta.get("gl_field_refs") or {}}


def fit_fields(out: dict, cap: int = CAP_BYTES) -> dict:
    """describe 并上 meta/fields 后严格按字节封顶：先去重复的 kind.writable，再截长枚举、去枚举，
    再只留字段名和中文名，最后从最长的字段列表尾部删项（fields_truncated=true）。
    做过的步骤写进 trimmed，模型能看出结果被删减过。"""
    if size_of(out) <= cap or not isinstance(out.get("fields"), dict):
        return out
    done: list[str] = []
    for name, step in _FIT_STEPS:
        out = step(out)
        done.append(name)
        if size_of(dict(out, trimmed=done)) <= cap:
            return dict(out, trimmed=done)
    return _truncate(dict(out, trimmed=done, fields_truncated=True), cap)


def _truncate(out: dict, cap: int) -> dict:
    fields = {k: list(v) if isinstance(v, list) else v for k, v in out["fields"].items()}
    out = dict(out, fields=fields)
    while size_of(out) > cap:
        lists = [v for v in fields.values() if isinstance(v, list) and v]
        if not lists:
            break
        max(lists, key=len).pop()
    if size_of(out) > cap:
        # 字段已删光还超：其余部分也只留要点。
        out = {k: v for k, v in out.items() if k not in ("field_refs", "gl_field_refs")}
        for key in ("kind", "archive", "gl"):
            if isinstance(out.get(key), dict):
                out[key] = {k: v for k, v in out[key].items() if k in ("name", "title")}
    return out


def _map_items(out: dict, fn) -> dict:
    fields = {}
    for key, value in out["fields"].items():
        if isinstance(value, list):
            value = [fn(item) if isinstance(item, dict) else item for item in value]
        fields[key] = value
    return dict(out, fields=fields)


def _cut_enum(item: dict) -> dict:
    enum = item.get("enum")
    if not isinstance(enum, list) or len(enum) <= _ENUM_KEEP:
        return item
    return dict(item, enum=enum[:_ENUM_KEEP], enum_total=len(enum))


def _drop_enum(item: dict) -> dict:
    enum = item.get("enum")
    if not isinstance(enum, list):
        return item
    total = item.get("enum_total", len(enum))
    return dict({k: v for k, v in item.items() if k != "enum"}, enum_total=total)


def _brief(item: dict) -> dict:
    return {k: item[k] for k in ("name", "label") if k in item}


def _drop_writable(out: dict) -> dict:
    kind = out.get("kind")
    if not isinstance(kind, dict) or "writable" not in kind:
        return out
    return dict(out, kind={k: v for k, v in kind.items() if k != "writable"})


_FIT_STEPS = (
    ("kind_writable_dropped", _drop_writable),
    ("enum_cut", lambda out: _map_items(out, _cut_enum)),
    ("enum_dropped", lambda out: _map_items(out, _drop_enum)),
    ("fields_brief", lambda out: _map_items(out, _brief)),
)


def describe_archive(meta: dict, name: str) -> dict | None:
    archive = _named(meta.get("archives"), name)
    return None if archive is None else {"archive": archive}


def names(meta: dict, key: str) -> list[str]:
    items = meta.get(key)
    if not isinstance(items, list):
        return []
    return sorted(str(item.get("name")) for item in items if isinstance(item, dict) and item.get("name"))


def _named(items: object, name: str) -> dict | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None
