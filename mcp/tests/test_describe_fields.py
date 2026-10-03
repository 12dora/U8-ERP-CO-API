"""u8_describe 并上 meta/fields（字段中文名、类型、必填、枚举），以及按字节封顶。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from u8co_mcp import describe as desc

from tests.support import env, make_app

META = {
    "ok": True,
    "kinds": [{"name": "sale_order", "title": "销售订单", "writable": {"create": {"head": {"exact": ["ccuscode"]}}}}],
    "archives": [{"name": "customer", "key": "cCusCode"}],
    "gl": {"required_head": ["sign"]},
    "field_refs": {"ccuscode": "customer"},
    "gl_field_refs": {"account": "account"},
}

FIELDS = {
    "ok": True,
    "type": "sale_order",
    "op": "create",
    "head": [
        {"name": "ccuscode", "label": "客户编码", "type": "string", "required": False, "max_length": 20},
        {"name": "cbustype", "label": "业务类型", "type": "enum", "required": True,
         "enum": [{"code": "普通销售", "name": "普通销售"}]},
    ],
    "lines": [{"name": "iquantity", "label": "数量", "type": "decimal", "required": True}],
    "fields_revision": "abc",
}


class DescribeFieldsTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, env())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app, self.send = make_app()
        self.send.add("GET", "/v1/co/meta", body=META)

    def describe(self, args: dict) -> dict:
        return self.app.call("u8_describe", args)

    def fields_calls(self):
        return [c for c in self.send.calls if c.path == "/v1/co/meta/fields"]

    def test_type_merges_fields(self):
        self.send.add("POST", "/v1/co/meta/fields", body=FIELDS)
        out = self.describe({"type": "sale_order"})["structuredContent"]
        self.assertEqual(out["kind"]["name"], "sale_order")
        self.assertEqual(out["field_refs"], {"ccuscode": "customer"})
        self.assertEqual(out["fields"]["head"][1]["label"], "业务类型")
        self.assertNotIn("ok", out["fields"])
        self.assertNotIn("trimmed", out)
        body = self.fields_calls()[0].body()
        self.assertEqual(body["type"], "sale_order")
        self.assertNotIn("op", body)
        self.assertEqual((body["acc"], body["operator"]), ("801", "op001"))
        self.assertEqual(self.fields_calls()[0].query, {})

    def test_op_and_source_forwarded(self):
        self.send.add("POST", "/v1/co/meta/fields", body=FIELDS)
        self.describe({"type": "sale_order", "op": "generate", "source": "quotation"})
        body = self.fields_calls()[0].body()
        self.assertEqual((body["op"], body["source"]), ("generate", "quotation"))

    def test_archive_and_gl(self):
        self.send.add("POST", "/v1/co/meta/fields", body={"ok": True, "archive": "customer", "fields": []})
        out = self.describe({"archive": "customer"})["structuredContent"]
        self.assertEqual(out["archive"]["key"], "cCusCode")
        self.assertEqual(out["fields"]["fields"], [])
        self.assertEqual(self.fields_calls()[0].body()["archive"], "customer")
        out = self.describe({"gl": True})["structuredContent"]
        self.assertEqual(out["gl_field_refs"], {"account": "account"})
        self.assertIs(self.fields_calls()[1].body()["gl"], True)
        self.describe({"type": "gl"})
        self.assertIs(self.fields_calls()[2].body()["gl"], True)
        self.assertNotIn("type", self.fields_calls()[2].body())

    def test_fields_failure_keeps_meta(self):
        error = {"code": "bad_request", "message": "不支持的 op", "retryable": False, "field": "op"}
        self.send.add("POST", "/v1/co/meta/fields", status=400, body={"error": error})
        result = self.describe({"type": "sale_order", "op": "update"})
        self.assertFalse(result["isError"])
        out = result["structuredContent"]
        self.assertEqual(out["kind"]["name"], "sale_order")
        self.assertEqual(out["fields_error"], {"status": 400, "error": error})
        self.assertNotIn("fields", out)

    def test_fields_token_failure_reports_error(self):
        # meta 已缓存，meta/fields 取令牌时失败：工具不失败，只带 fields_error。
        self.send.add("POST", "/v1/co/meta/fields", body=FIELDS)
        self.describe({"type": "sale_order"})
        with mock.patch.dict(os.environ, {"U8CO_MCP_TEST_TOKEN": ""}):
            result = self.describe({"type": "sale_order"})
        self.assertFalse(result["isError"])
        out = result["structuredContent"]
        self.assertEqual(out["fields_error"]["error"]["code"], "token_failed")
        self.assertEqual(out["kind"]["name"], "sale_order")
        self.assertEqual(len(self.fields_calls()), 1)

    def test_fields_without_password_reports_error(self):
        with mock.patch.dict(os.environ, {"U8CO_MCP_PASSWORD": ""}):
            out = self.describe({"type": "sale_order"})["structuredContent"]
        self.assertEqual(out["fields_error"]["error"]["code"], "config_error")
        self.assertEqual(self.fields_calls(), [])

    def test_argument_rules(self):
        cases = (
            {"op": "update"},
            {"archive": "customer", "op": "update"},
            {"type": "sale_order", "op": "generate"},
            {"type": "sale_order", "source": "quotation"},
            {"type": "sale_order", "op": "update", "source": "quotation"},
            {"gl": True, "type": "sale_order"},
            {"type": "sale_order", "op": "delete"},
        )
        for args in cases:
            result = self.describe(args)
            self.assertTrue(result["isError"], args)
        self.assertEqual(self.send.calls, [])

    def test_unknown_type_does_not_call_fields(self):
        self.assertTrue(self.describe({"type": "nope"})["isError"])
        self.assertEqual(self.fields_calls(), [])


def _big(enum_len: int, count: int) -> dict:
    enum = [{"code": f"C{i:04d}", "name": f"枚举值{i}"} for i in range(enum_len)]
    head = [
        {"name": f"f{i}", "label": f"字段{i}", "type": "enum", "required": False, "enum": enum} for i in range(count)
    ]
    return {"kind": {"name": "x", "writable": {"create": {"head": {"exact": ["a"] * 50}}}}, "fields": {"head": head}}


class FitFieldsTests(unittest.TestCase):
    def test_small_untouched(self):
        out = {"kind": {"name": "x"}, "fields": {"head": [{"name": "a", "enum": [1] * 20}]}}
        self.assertIs(desc.fit_fields(out), out)

    def test_writable_dropped_first(self):
        big = _big(3, 2)
        big["kind"]["writable"] = {"create": {"head": {"exact": ["a" * 40] * 100}}}
        out = desc.fit_fields(big, cap=2000)
        self.assertEqual(out["trimmed"], ["kind_writable_dropped"])
        self.assertNotIn("writable", out["kind"])
        self.assertEqual(len(out["fields"]["head"][0]["enum"]), 3)

    def test_long_enums_cut_after_writable(self):
        out = desc.fit_fields(_big(60, 3), cap=3000)
        item = out["fields"]["head"][0]
        self.assertEqual(out["trimmed"], ["kind_writable_dropped", "enum_cut"])
        self.assertEqual((len(item["enum"]), item["enum_total"]), (10, 60))
        self.assertLessEqual(desc.size_of(out), 3000)

    def test_enums_dropped_then_brief(self):
        out = desc.fit_fields(_big(60, 3), cap=600)
        self.assertEqual(out["trimmed"][:3], ["kind_writable_dropped", "enum_cut", "enum_dropped"])
        self.assertNotIn("enum", out["fields"]["head"][0])
        self.assertLessEqual(desc.size_of(out), 600)
        out = desc.fit_fields(_big(60, 30), cap=2000)
        self.assertEqual(out["trimmed"], ["kind_writable_dropped", "enum_cut", "enum_dropped", "fields_brief"])
        self.assertEqual(set(out["fields"]["head"][0]), {"name", "label"})

    def test_cap_is_strict(self):
        for cap in (1000, 400, 250):
            out = desc.fit_fields(_big(60, 300), cap=cap)
            self.assertTrue(out["fields_truncated"], cap)
            self.assertLessEqual(desc.size_of(out), cap)
        out = desc.fit_fields(_big(60, 300), cap=1000)
        self.assertTrue(0 < len(out["fields"]["head"]) < 300)
        self.assertEqual(out["fields"]["head"][0], {"name": "f0", "label": "字段0"})

    def test_without_fields_untouched(self):
        out = {"kind": {"name": "x", "tables": "y" * 20000}}
        self.assertIs(desc.fit_fields(out, cap=100), out)


if __name__ == "__main__":
    unittest.main()
