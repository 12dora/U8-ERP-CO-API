"""JSON-RPC / MCP 协议层、stdio 循环和包内资源。"""

from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock

from u8co_mcp import __version__
from u8co_mcp.protocol import GUIDE_URI, Protocol
from u8co_mcp.server import serve
from u8co_mcp.tools import load_guide, load_routes, route_names

from tests.support import env, make_app


def rpc(method: str, params=None, msg_id=1) -> str:
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message)


class ProtocolCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, env())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app, self.send = make_app()
        self.proto = Protocol(self.app)

    def ask(self, method: str, params=None, msg_id=1) -> dict:
        return json.loads(self.proto.handle_line(rpc(method, params, msg_id)))


class HandshakeTests(ProtocolCase):
    def test_initialize_echoes_supported_version(self):
        reply = self.ask("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}})
        result = reply["result"]
        self.assertEqual(reply["id"], 1)
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertEqual(result["serverInfo"]["name"], "u8co-mcp")
        self.assertEqual(result["serverInfo"]["version"], __version__)
        self.assertIn("tools", result["capabilities"])

    def test_initialize_unknown_version_gets_newest(self):
        result = self.ask("initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")

    def test_notification_has_no_reply(self):
        line = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(self.proto.handle_line(line))

    def test_ping(self):
        self.assertEqual(self.ask("ping")["result"], {})

    def test_unknown_method(self):
        self.assertEqual(self.ask("sampling/createMessage")["error"]["code"], -32601)

    def test_parse_error(self):
        reply = json.loads(self.proto.handle_line("{not json"))
        self.assertEqual((reply["id"], reply["error"]["code"]), (None, -32700))

    def test_invalid_request(self):
        reply = json.loads(self.proto.handle_line(json.dumps({"jsonrpc": "1.0", "id": 3, "method": "ping"})))
        self.assertEqual((reply["id"], reply["error"]["code"]), (3, -32600))
        reply = json.loads(self.proto.handle_line("42"))
        self.assertEqual(reply["error"]["code"], -32600)

    def test_client_response_ignored(self):
        self.assertIsNone(self.proto.handle_line(json.dumps({"jsonrpc": "2.0", "id": 9, "result": {}})))

    def test_batch(self):
        line = json.dumps([json.loads(rpc("ping", msg_id=1)), {"jsonrpc": "2.0", "method": "notifications/x"}])
        replies = json.loads(self.proto.handle_line(line))
        self.assertEqual([r["id"] for r in replies], [1])
        self.assertEqual(json.loads(self.proto.handle_line("[]"))["error"]["code"], -32600)

    def test_params_must_be_object(self):
        self.assertEqual(self.ask("tools/list", [1])["error"]["code"], -32602)


class ToolCallTests(ProtocolCase):
    def test_tools_list(self):
        tools = self.ask("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(
            sorted(names),
            sorted(["u8_guide", "u8_describe", "u8_read", "u8_write", "u8_resolve", "u8_idempotency_get"]),
        )
        by_name = {t["name"]: t for t in tools}
        write_enum = by_name["u8_write"]["inputSchema"]["properties"]["route"]["enum"]
        read_enum = by_name["u8_read"]["inputSchema"]["properties"]["route"]["enum"]
        self.assertIn("vouchers/create", write_enum)
        self.assertNotIn("vouchers/load", write_enum)
        self.assertIn("vouchers/load", read_enum)
        self.assertIn("archives/resolve", read_enum)
        self.assertNotIn("vouchers/create", read_enum)
        self.assertEqual(by_name["u8_write"]["inputSchema"]["required"], ["route", "body"])
        idem = by_name["u8_idempotency_get"]["inputSchema"]["properties"]["route"]["enum"]
        # 所有写路由都能按幂等键查。
        self.assertEqual(idem, route_names("write"))
        self.assertEqual(idem, write_enum)

    def test_read_only_tools_list(self):
        proto = Protocol(make_app(read_only=True)[0])
        tools = json.loads(proto.handle_line(rpc("tools/list")))["result"]["tools"]
        self.assertNotIn("u8_write", [t["name"] for t in tools])

    def test_call_success(self):
        self.send.add("POST", "/v1/co/vouchers/load", body={"ok": True, "id": 1})
        result = self.ask("tools/call", {"name": "u8_read", "arguments": {"route": "vouchers/load", "body": {}}})
        result = result["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), {"ok": True, "id": 1})
        self.assertEqual(result["structuredContent"], {"ok": True, "id": 1})

    def test_call_http_error_is_tool_error(self):
        error = {"code": "not_found", "message": "单据不存在", "retryable": False}
        self.send.add("POST", "/v1/co/vouchers/load", status=404, body={"error": error})
        reply = self.ask("tools/call", {"name": "u8_read", "arguments": {"route": "vouchers/load", "body": {}}})
        self.assertNotIn("error", reply)
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(reply["result"]["structuredContent"], {"status": 404, "error": error})

    def test_call_bad_params(self):
        self.assertEqual(self.ask("tools/call", {"arguments": {}})["error"]["code"], -32602)
        self.assertEqual(self.ask("tools/call", {"name": "u8_read", "arguments": [1]})["error"]["code"], -32602)
        self.assertEqual(self.ask("tools/call", {"name": "nope"})["error"]["code"], -32602)

    def test_call_guide(self):
        result = self.ask("tools/call", {"name": "u8_guide"})["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["guide"], load_guide())


class ResourceTests(ProtocolCase):
    def test_list_and_read_guide(self):
        resources = self.ask("resources/list")["result"]["resources"]
        self.assertEqual([r["uri"] for r in resources], [GUIDE_URI])
        contents = self.ask("resources/read", {"uri": GUIDE_URI})["result"]["contents"]
        self.assertEqual(contents[0]["text"], load_guide())
        self.assertEqual(self.ask("resources/read", {"uri": "u8co://nope"})["error"]["code"], -32002)
        self.assertEqual(self.ask("prompts/list")["result"], {"prompts": []})


class ServeTests(ProtocolCase):
    def test_serve_until_eof(self):
        lines = [
            rpc("initialize", {"protocolVersion": "2025-06-18"}, 1),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            "",
            rpc("ping", msg_id=2),
        ]
        stdin = io.BytesIO(("\n".join(lines) + "\n").encode("utf-8"))
        stdout = io.BytesIO()
        self.assertEqual(serve(self.proto, stdin, stdout), 0)
        replies = [json.loads(x) for x in stdout.getvalue().decode("utf-8").splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])

    def test_serve_bad_utf8(self):
        stdout = io.BytesIO()
        self.assertEqual(serve(self.proto, io.BytesIO(b"\xff\xfe\n"), stdout), 0)
        self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], -32700)

    def test_serve_empty_stdin(self):
        stdout = io.BytesIO()
        self.assertEqual(serve(self.proto, io.BytesIO(b""), stdout), 0)
        self.assertEqual(stdout.getvalue(), b"")


class PackageDataTests(unittest.TestCase):
    def test_guide_small(self):
        self.assertLessEqual(len(load_guide().encode("utf-8")), 3072)

    def test_routes_sorted_unique(self):
        routes = load_routes()
        names = [r["route"] for r in routes]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), len(set(names)))
        for entry in routes:
            self.assertEqual(set(entry), {"route", "access", "summary"})
            self.assertIn(entry["access"], ("read", "write"))
            self.assertTrue(entry["summary"])
            self.assertFalse(entry["route"].startswith("/"))

    def test_routes_contain_assist_routes(self):
        access = {r["route"]: r["access"] for r in load_routes()}
        self.assertEqual(access["archives/resolve"], "read")
        self.assertEqual(access["idempotency/get"], "read")
        self.assertEqual((access["health"], access["meta"]), ("read", "read"))
        for route in ("vouchers/create", "vouchers/verify", "gl/vouchers/create", "arap/writeoff/auto"):
            self.assertEqual(access[route], "write")

    def test_routes_contain_openings_post_as_write(self):
        routes = {r["route"]: r for r in load_routes()}
        self.assertEqual(routes["openings/post"]["access"], "write")
        self.assertEqual(routes["openings/post"]["summary"], "采购 / 存货核算期初记账")

    def test_routes_contain_openings_arap_as_write(self):
        routes = {r["route"]: r for r in load_routes()}
        self.assertEqual(routes["openings/arap"]["access"], "write")
        self.assertEqual(routes["openings/arap"]["summary"], "应收应付期初单据")

    def test_routes_contain_periods_close_as_write(self):
        routes = {r["route"]: r for r in load_routes()}
        self.assertEqual(routes["periods/close"]["access"], "write")
        self.assertEqual(routes["periods/close"]["summary"], "月末结账")

    def test_routes_contain_ia_post_and_period_end_as_write(self):
        routes = {r["route"]: r for r in load_routes()}
        self.assertEqual(routes["ia/post"]["access"], "write")
        self.assertEqual(routes["ia/post"]["summary"], "存货核算记账")
        self.assertEqual(routes["ia/period_end"]["access"], "write")
        self.assertEqual(routes["ia/period_end"]["summary"], "存货核算期末处理")

    def test_routes_contain_arap_processing_as_write(self):
        routes = {r["route"]: r for r in load_routes()}
        names = (
            "transfer", "merge", "red_offset", "process/cancel", "process/voucher",
            "exchange_gain", "exchange_gain/cancel", "bad_debt",
        )
        for name in names:
            self.assertEqual(routes["arap/" + name]["access"], "write")
            self.assertIn("arap/" + name, route_names("write"))
        self.assertIn("arap/process/cancel", load_guide())
        self.assertIn("test_account_only", load_guide())
        self.assertIn("feature_disabled", load_guide())


if __name__ == "__main__":
    unittest.main()
