"""u8_describe：OpenAPI 请求体结构的展开与脱敏、meta 取项、大小封顶。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from u8co_mcp import describe as desc

from tests.support import env, make_app

_AUTH_PROPS = {
    "acc": {"type": "string", "description": "账套号"},
    "operator": {"type": "string"},
    "password": {"type": "string", "writeOnly": True},
    "password_enc": {"type": "string"},
}

def _ref(name: str) -> dict:
    return {"$ref": "#/components/schemas/" + name}


OPENAPI = {
    "openapi": "3.1.0",
    "paths": {
        "/v1/co/vouchers/create": {
            "post": {
                "summary": "新增单据",
                "description": "新增一张单据。",
                "requestBody": {"content": {"application/json": {"schema": _ref("CoCreateIn")}}},
            }
        },
        "/v1/co/health": {"get": {"summary": "CO 桥健康检查", "description": "桥不可达时返回 503。"}},
    },
    "components": {
        "schemas": {
            "CoCreateIn": {
                "type": "object",
                "description": "新增单据的请求体",
                "properties": dict(
                    _AUTH_PROPS,
                    type={"type": "string", "description": "单据类型"},
                    head={"$ref": "#/components/schemas/Head"},
                    node={"$ref": "#/components/schemas/Node"},
                ),
                "required": ["acc", "operator", "password", "type", "head"],
                "examples": [{"acc": "801", "operator": "op001"}],
            },
            "Head": {"type": "object", "description": "表头", "additionalProperties": {"type": "string"}},
            "Node": {"type": "object", "properties": {"child": {"$ref": "#/components/schemas/Node"}}},
        }
    },
}

META = {
    "ok": True,
    "kinds": [
        {"name": "sale_order", "title": "销售订单", "tables": {"head": "SO_SOMain", "x": "y" * 500}, "blocked": ["a"]},
        {"name": "dispatch", "title": "发货单"},
    ],
    "archives": [{"name": "customer", "key": "cCusCode"}],
    "gl": {"required_head": ["sign"]},
    "field_refs": {"ccuscode": "customer"},
    "gl_field_refs": {"account": "account"},
}

ENTRY = {"route": "vouchers/create", "access": "write", "summary": "新增单据"}


class RouteSchemaTests(unittest.TestCase):
    def test_resolves_and_strips_auth(self):
        out = desc.describe_route(OPENAPI, ENTRY, True)
        schema = out["request_schema"]
        self.assertEqual(out["method"], "POST")
        self.assertTrue(out["idempotency_key"])
        self.assertEqual(out["description"], "新增一张单据。")
        for key in desc.AUTH_KEYS:
            self.assertNotIn(key, schema["properties"])
        self.assertEqual(schema["required"], ["type", "head"])
        self.assertNotIn("examples", schema)
        self.assertEqual(schema["properties"]["head"]["description"], "表头")
        self.assertEqual(schema["properties"]["type"]["description"], "单据类型")

    def test_cycle_stops_with_ref_name(self):
        schema = desc.describe_route(OPENAPI, ENTRY, True)["request_schema"]
        child = schema["properties"]["node"]["properties"]["child"]
        self.assertEqual(child, {"$ref_name": "Node"})

    def test_depth_limit(self):
        resolved = desc.resolve({"$ref": "#/components/schemas/CoCreateIn"}, OPENAPI["components"]["schemas"], 1)
        self.assertEqual(resolved["properties"]["head"], {"$ref_name": "Head"})

    def test_get_route_has_no_schema(self):
        entry = {"route": "health", "access": "read", "summary": "CO 桥健康检查"}
        out = desc.describe_route(OPENAPI, entry, False)
        self.assertEqual(out["method"], "GET")
        self.assertIsNone(out["request_schema"])

    def test_missing_path(self):
        entry = {"route": "vouchers/list", "access": "read", "summary": "单据列表"}
        self.assertIsNone(desc.describe_route(OPENAPI, entry, False)["request_schema"])

    def test_cap_truncates(self):
        out = desc.describe_route(OPENAPI, ENTRY, True, cap=200)
        self.assertEqual(out["request_schema"]["truncated"], True)
        self.assertIn("type", out["request_schema"]["properties"])
        self.assertNotIn("acc", out["request_schema"]["properties"])

    def test_cap_drops_descriptions_first(self):
        full = desc.size_of(desc.describe_route(OPENAPI, ENTRY, True))
        out = desc.describe_route(OPENAPI, ENTRY, True, cap=full - 10)
        self.assertNotIn("truncated", out["request_schema"])
        self.assertLessEqual(desc.size_of(out), full - 10)

    def test_strip_auth_in_all_of(self):
        schema = {"allOf": [{"properties": dict(_AUTH_PROPS, x={}), "required": ["acc", "x"]}]}
        stripped = desc.strip_auth(schema)
        self.assertEqual(stripped["allOf"][0], {"properties": {"x": {}}, "required": ["x"]})


class MetaTests(unittest.TestCase):
    def test_kind_drops_big_tables_keeps_small(self):
        out = desc.describe_kind(META, "sale_order")
        self.assertNotIn("tables", out["kind"])
        self.assertEqual(out["kind"]["blocked"], ["a"])
        self.assertEqual(out["field_refs"], {"ccuscode": "customer"})
        self.assertIsNone(desc.describe_kind(META, "nope"))

    def test_archive_and_gl(self):
        self.assertEqual(desc.describe_archive(META, "customer"), {"archive": META["archives"][0]})
        self.assertEqual(desc.describe_gl(META)["gl_field_refs"], {"account": "account"})
        self.assertEqual(desc.names(META, "kinds"), ["dispatch", "sale_order"])


class ToolTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, env())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_catalog_without_http(self):
        app, send = make_app()
        result = app.call("u8_describe", {})
        routes = result["structuredContent"]["routes"]
        self.assertIn({"route": "vouchers/create", "access": "write", "summary": "新增单据"}, routes)
        self.assertEqual(send.calls, [])

    def test_route_fetches_openapi_once(self):
        app, send = make_app()
        send.add("GET", "/v1/openapi.json", body=OPENAPI)
        first = app.call("u8_describe", {"route": "vouchers/create"})
        app.call("u8_describe", {"route": "vouchers/create"})
        self.assertFalse(first["isError"])
        self.assertEqual(len(send.calls), 1)
        self.assertNotIn("password", first["content"][0]["text"])

    def test_unknown_route(self):
        app, send = make_app()
        result = app.call("u8_describe", {"route": "nope"})
        self.assertTrue(result["isError"])
        self.assertEqual(send.calls, [])

    def test_type_uses_cached_meta(self):
        app, send = make_app()
        send.add("GET", "/v1/co/meta", body=META)
        send.add("POST", "/v1/co/meta/fields", body={"ok": True, "head": []})
        out = app.call("u8_describe", {"type": "sale_order"})
        app.call("u8_describe", {"archive": "customer"})
        self.assertEqual(out["structuredContent"]["kind"]["name"], "sale_order")
        meta_calls = [c for c in send.calls if c.path == "/v1/co/meta"]
        self.assertEqual(len(meta_calls), 1)
        self.assertIsNone(meta_calls[0].data)

    def test_unknown_type_lists_names(self):
        app, send = make_app()
        send.add("GET", "/v1/co/meta", body=META)
        result = app.call("u8_describe", {"type": "nope"})
        self.assertTrue(result["isError"])
        self.assertIn("sale_order", result["structuredContent"]["error"]["message"])

    def test_only_one_selector(self):
        app, _ = make_app()
        result = app.call("u8_describe", {"type": "sale_order", "archive": "customer"})
        self.assertTrue(result["isError"])


if __name__ == "__main__":
    unittest.main()
