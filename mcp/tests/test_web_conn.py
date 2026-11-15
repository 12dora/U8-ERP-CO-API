"""HTTP 方式的连接层：非 2xx 关闭长连接（不串包）、Transfer-Encoding 411、请求体上限、并发上限、
两个令牌并发互不串用、SIGTERM 停机。"""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import threading
import time
import unittest

from u8co_mcp.protocol import Protocol
from u8co_mcp.web import MAX_BODY, McpServer, _on_sigterm

from tests.test_web import MGMT_TOKEN, PROXY_PATH, USER, WebCase, jwt

PING = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")


def raw_request(body: bytes, token: str | None = MGMT_TOKEN, extra: str = "", path: str = "/mcp") -> bytes:
    """extra 代替 Content-Length 行（给出要发的长度头或编码头）。"""
    head = f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
    if token is not None:
        head += f"Authorization: Bearer {token}\r\n"
    head += extra or f"Content-Length: {len(body)}\r\n"
    return (head + "\r\n").encode("ascii") + body


def read_all(sock: socket.socket) -> bytes:
    """读到对方关闭连接（或超时）为止。"""
    sock.settimeout(5)
    chunks = []
    while True:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


class ConnCase(WebCase):
    def connect(self) -> socket.socket:
        port = int(self.url.rsplit(":", 1)[1])
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.addCleanup(sock.close)
        return sock

    def client(self) -> http.client.HTTPConnection:
        port = int(self.url.rsplit(":", 1)[1])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.addCleanup(conn.close)
        return conn


class KeepAliveTests(ConnCase):
    def test_success_keeps_connection(self):
        self.serve()
        conn = self.client()
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + MGMT_TOKEN}
        conn.request("POST", "/mcp", PING, headers)
        resp = conn.getresponse()
        self.assertEqual((resp.status, resp.getheader("Connection")), (200, None))
        resp.read()
        first = conn.sock
        conn.request("POST", "/mcp", PING, headers)
        self.assertEqual(conn.getresponse().status, 200)
        self.assertIs(conn.sock, first)

    def test_401_closes_and_body_is_not_parsed_as_next_request(self):
        # 第一个请求没有令牌（401，请求体没读），请求体本身是一个合法的 HTTP 请求：不能被当成第二个请求处理。
        send = self.serve()
        send.add("POST", PROXY_PATH, body={"ok": True, "data": {"ok": True}})
        smuggled = raw_request(PING)
        sock = self.connect()
        sock.sendall(raw_request(smuggled, token=None))
        data = read_all(sock)
        self.assertTrue(data.startswith(b"HTTP/1.1 401"), data[:40])
        self.assertIn(b"Connection: close", data)
        self.assertEqual(data.count(b"HTTP/1.1 "), 1)

    def test_client_reuses_after_401(self):
        self.serve()
        conn = self.client()
        conn.request("POST", "/mcp", PING, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual((resp.status, resp.getheader("Connection")), (401, "close"))
        resp.read()
        # http.client 看到 Connection: close 后自动重连，同一个连接对象继续可用。
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + MGMT_TOKEN}
        conn.request("POST", "/mcp", PING, headers)
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.read())["result"], {})

    def test_other_refusals_close(self):
        self.serve()
        cases = [
            (raw_request(PING, extra="X-Pad: 1\r\n"), b"411"),
            (raw_request(PING, path="/other"), b"404"),
            (raw_request(PING).replace(b"application/json", b"text/plain"), b"415"),
            (raw_request(PING).replace(b"POST /mcp", b"GET /mcp"), b"405"),
        ]
        for request, status in cases:
            with self.subTest(status=status):
                sock = self.connect()
                sock.sendall(request + raw_request(PING))
                data = read_all(sock)
                self.assertTrue(data.startswith(b"HTTP/1.1 " + status), data[:40])
                self.assertEqual(data.count(b"HTTP/1.1 "), 1, data)
                self.assertIn(b"Connection: close", data)

    def test_transfer_encoding_is_411_and_close(self):
        self.serve()
        sock = self.connect()
        chunked = b"%x\r\n" % len(PING) + PING + b"\r\n0\r\n\r\n"
        sock.sendall(raw_request(chunked, extra="Transfer-Encoding: chunked\r\n") + raw_request(PING))
        data = read_all(sock)
        self.assertTrue(data.startswith(b"HTTP/1.1 411"), data[:40])
        self.assertIn(b"Connection: close", data)
        self.assertEqual(data.count(b"HTTP/1.1 "), 1)
        # 同时带 Content-Length 也一样拒绝。
        sock = self.connect()
        both = f"Transfer-Encoding: chunked\r\nContent-Length: {len(chunked)}\r\n"
        sock.sendall(raw_request(chunked, extra=both))
        self.assertTrue(read_all(sock).startswith(b"HTTP/1.1 411"))

    def test_body_limit(self):
        self.serve()
        status, headers, _ = self.post({"pad": "x" * MAX_BODY})
        self.assertEqual((status, headers.get("Connection")), (413, "close"))
        self.assertEqual(MAX_BODY, 256 * 1024)

    def test_get_with_body_closes(self):
        self.serve()
        sock = self.connect()
        sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\nContent-Length: 4\r\n\r\nabcd" + raw_request(PING))
        data = read_all(sock)
        self.assertTrue(data.startswith(b"HTTP/1.1 200"))
        self.assertEqual(data.count(b"HTTP/1.1 "), 1)


class LimitTests(ConnCase):
    def test_connection_limit_returns_503(self):
        self.serve()
        server = McpServer(("127.0.0.1", 0), Protocol(self.app), self.app.config.http, limit=1)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = "http://127.0.0.1:%d" % server.server_address[1]
        held = self.connect()
        held.sendall(raw_request(PING))
        self.assertIn(b"200 OK", held.recv(65536))
        # 第一个连接仍保持（长连接），第二个连接超过上限。
        status, headers = self.try_ping()
        for _ in range(20):
            if status is not None:
                break
            status, headers = self.try_ping()
        self.assertEqual((status, headers.get("Retry-After")), (503, "1"))
        held.close()
        for _ in range(50):
            if self.try_ping()[0] == 200:
                break
            time.sleep(0.05)
        else:
            self.fail("释放连接后仍然 503")

    def try_ping(self) -> tuple[int | None, dict]:
        # 超限时服务端回 503 后立即关闭、不读请求体：客户端还在发请求体时会断管或被重置，算作这次没拿到响应。
        try:
            status, headers, _ = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        except OSError:
            return None, {}
        return status, headers


class ConcurrencyTests(ConnCase):
    def test_two_tokens_in_parallel_do_not_mix(self):
        send = self.serve(read_only=True)
        send.add("POST", PROXY_PATH, body={"ok": True, "data": {"ok": True}})
        tokens = {
            "801": jwt({"sub": USER, "u8co_mgmt": True, "u8co_accs": ["801"], "exp": int(time.time()) + 600}),
            "802": jwt({"sub": "lisi", "u8co_mgmt": True, "u8co_accs": "802", "exp": int(time.time()) + 600}),
        }
        errors: list[str] = []
        params = {"name": "u8_mgmt_overview", "arguments": {"fiscal_year": 2026, "period_to": 3}}

        def worker(token: str):
            try:
                for _ in range(15):
                    result = self.rpc("tools/call", params, token=token)["result"]
                    if result["isError"]:
                        errors.append(json.dumps(result["structuredContent"]))
            except Exception as exc:  # 线程里的断言失败要带回主线程
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(t,)) for t in tokens.values() for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(send.calls), 90)
        by_token = {token: acc for acc, token in tokens.items()}
        for call in send.calls:
            # 每次调用的账套和调用者令牌必须属于同一个请求，不能串用别人的令牌。
            self.assertEqual(call.body()["acc"], by_token[call.headers["X-U8co-Caller-Token"]])
            self.assertNotIn("logins", call.body()["body"])


class SigtermTests(ConnCase):
    def test_sigterm_stops_serving(self):
        self.serve()
        server = McpServer(("127.0.0.1", 0), Protocol(self.app), self.app.config.http)
        self.addCleanup(server.server_close)
        previous = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, previous)
        _on_sigterm(server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        os.kill(os.getpid(), signal.SIGTERM)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(server.drain(1))


if __name__ == "__main__":
    unittest.main()
