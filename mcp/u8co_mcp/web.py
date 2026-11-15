"""HTTP 方式：MCP Streamable HTTP（无状态、只回 JSON，不开 SSE 流），只用标准库。

POST {path}：body 是一条 JSON-RPC 消息（或批量）。含请求时回 200 application/json；只有通知或响应时回 202。
GET / DELETE {path}：405（不提供服务端推送流，也不发会话号）。GET /healthz：200，不要令牌，给容器健康检查用。
每个 POST 都必须带 Authorization: Bearer <令牌>，否则 401。令牌原样转给 API（token.type=incoming），
列工具、经营管理门控、审计用户头都按本次请求的令牌算；本服务只解码载荷、不验签，签名和权限由 API 校验。
连接：HTTP/1.1 长连接；凡不是 2xx 的响应都带 Connection: close 并关闭连接（请求体可能没读，不能再复用）；
不接受 Transfer-Encoding（分块上传），回 411 并关闭。同时处理的连接数有上限，超出时回 503；
套接字读写 30 秒超时。收到 SIGTERM 时停止接受新连接，等在途请求处理完（有上限）再退出。
"""

from __future__ import annotations

import json
import logging
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from u8co_mcp import __version__
from u8co_mcp.auth import with_incoming
from u8co_mcp.config import HttpConfig
from u8co_mcp.mgmt import token_claims
from u8co_mcp.protocol import INTERNAL_ERROR, PARSE_ERROR, Protocol

log = logging.getLogger("u8co_mcp")

MAX_BODY = 256 * 1024  # 单个请求体上限（字节）
MAX_CONNECTIONS = 32  # 同时处理的连接数上限
SOCKET_TIMEOUT_S = 30  # 读请求、写响应的套接字超时（秒），也是长连接的空闲上限
DRAIN_S = 25.0  # 收到 SIGTERM 后等在途请求的上限（秒），小于 compose 的 stop_grace_period
HEALTH_PATH = "/healthz"
_BEARER = re.compile(r"^Bearer +([\x21-\x7e]{1,8192})\Z", re.IGNORECASE)
_JSON = "application/json"


_BUSY = (
    b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json; charset=utf-8\r\n"
    b"Retry-After: 1\r\nConnection: close\r\nContent-Length: 2\r\n\r\n{}"
)


class McpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], protocol: Protocol, cfg: HttpConfig, limit: int = MAX_CONNECTIONS):
        super().__init__(address, Handler)
        self.protocol = protocol
        self.cfg = cfg
        self._slots = threading.BoundedSemaphore(limit)
        self._busy = 0
        self._idle = threading.Condition()

    def process_request(self, request, client_address):
        # 连接数到上限时不排队：直接回 503 并关闭，不占用处理线程。
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(_BUSY)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # 对方断开、超时是常态，不打堆栈；其余只记异常类型（不记请求内容）。
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        log.error("处理连接出错：%s", type(exc).__name__)

    def begin(self) -> None:
        with self._idle:
            self._busy += 1

    def end(self) -> None:
        with self._idle:
            self._busy -= 1
            self._idle.notify_all()

    def drain(self, timeout: float) -> bool:
        """等在途请求处理完，最多 timeout 秒。都处理完返回 True。"""
        with self._idle:
            return self._idle.wait_for(lambda: self._busy == 0, timeout)


class Handler(BaseHTTPRequestHandler):
    server: McpServer
    server_version = f"u8co-mcp/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = SOCKET_TIMEOUT_S

    # ---- 路由 ----

    def do_GET(self):
        if self._has_body():
            # GET 不读请求体：带了请求体的，回完就关闭连接，免得请求体被当成下一个请求。
            self.close_connection = True
        if self._path() == HEALTH_PATH:
            self._send(200, {"ok": True})
        elif self._path() == self.server.cfg.path:
            self._send(405, _rpc_error(None, -32000, "不提供 SSE 流，请用 POST"), {"Allow": "POST"})
        else:
            self._send(404, {"error": "not_found"})

    def do_DELETE(self):
        if self._has_body():
            self.close_connection = True
        if self._path() == self.server.cfg.path:
            self._send(405, _rpc_error(None, -32000, "本服务不使用会话"), {"Allow": "POST"})
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self):
        if self._path() != self.server.cfg.path:
            self._send(404, {"error": "not_found"})
            return
        refusal = self._refusal()
        if refusal is not None:
            status, body, headers = refusal
            self._send(status, body, headers)
            return
        raw = self._body()
        if raw is None:
            return
        token = self._token()
        assert token is not None
        self.server.begin()
        try:
            with with_incoming(token):
                reply = self._handle(raw)
        finally:
            self.server.end()
        if reply is None:
            self._send_raw(202, b"", None)
        else:
            self._send_raw(200, reply.encode("utf-8"), _JSON)

    # ---- 检查 ----

    def _refusal(self) -> tuple[int, dict, dict] | None:
        origin = self.headers.get("Origin")
        if origin is not None and origin.rstrip("/") not in self.server.cfg.allowed_origins:
            return 403, _rpc_error(None, -32000, "来源不被允许（Origin）"), {}
        token = self._token()
        if token is None:
            return 401, _rpc_error(None, -32001, "缺少 Bearer 令牌"), {"WWW-Authenticate": 'Bearer realm="u8co-mcp"'}
        if _expired(token):
            challenge = 'Bearer realm="u8co-mcp", error="invalid_token", error_description="token expired"'
            return 401, _rpc_error(None, -32001, "令牌已过期"), {"WWW-Authenticate": challenge}
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != _JSON:
            return 415, _rpc_error(None, -32000, "Content-Type 必须是 application/json"), {}
        return None

    def _token(self) -> str | None:
        match = _BEARER.match((self.headers.get("Authorization") or "").strip())
        return match.group(1) if match else None

    def _has_body(self) -> bool:
        return self.headers.get("Transfer-Encoding") is not None or (self.headers.get("Content-Length") or "0") != "0"

    def _body(self) -> bytes | None:
        if self.headers.get("Transfer-Encoding") is not None:
            self._send(411, _rpc_error(None, -32000, "不支持 Transfer-Encoding，请用 Content-Length"))
            return None
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self._send(411, _rpc_error(None, -32000, "缺少 Content-Length"))
            return None
        if length < 0 or length > MAX_BODY:
            self._send(413, _rpc_error(None, PARSE_ERROR, "请求太大"))
            return None
        raw = self.rfile.read(length)
        if len(raw) < length:
            # 对方提前断开或超时：不回响应，关闭连接。
            self.close_connection = True
            return None
        return raw

    def _handle(self, raw: bytes) -> str | None:
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            return json.dumps(_rpc_error(None, PARSE_ERROR, "不是 UTF-8"), ensure_ascii=False)
        try:
            return self.server.protocol.handle_line(line)
        except Exception as exc:  # 兜底：一条坏消息不能让服务出错
            log.error("处理消息出错：%s", type(exc).__name__)
            return json.dumps(_rpc_error(None, INTERNAL_ERROR, "内部错误"), ensure_ascii=False)

    # ---- 输出 ----

    def _path(self) -> str:
        return self.path.split("?", 1)[0]

    def _send(self, status: int, body: dict, headers: dict | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_raw(status, data, _JSON, headers)

    def _send_raw(self, status: int, data: bytes, ctype: str | None, headers: dict | None = None) -> None:
        self.send_response(status)
        if not 200 <= status < 300 or self.close_connection:
            # 非 2xx 时请求体可能没读（或没读完），连接不能再复用；send_header 同时设 close_connection。
            self.send_header("Connection", "close")
        if ctype:
            self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def log_message(self, format, *args):  # 只记方法、路径、状态，不记请求头（含令牌）
        log.info("%s %s", self.address_string(), format % args)


def _expired(token: str) -> bool:
    """令牌是 JWT 且 exp 已过时返回 True，让客户端刷新令牌。不是 JWT 或没有 exp 的交给 API 判断。"""
    exp = token_claims(token).get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return False
    return exp <= time.time()


def _rpc_error(msg_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def make_server(protocol: Protocol, cfg: HttpConfig, host: str, port: int) -> McpServer:
    return McpServer((host, port), protocol, cfg)


def serve_http(protocol: Protocol, cfg: HttpConfig, host: str, port: int) -> int:
    server = make_server(protocol, cfg, host, port)
    bound = server.server_address
    log.info("HTTP 方式监听 %s:%s%s", bound[0], bound[1], cfg.path)
    _on_sigterm(server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if not server.drain(DRAIN_S):
            log.warning("等待在途请求超时，直接退出")
        server.server_close()
    return 0


def _on_sigterm(server: McpServer) -> None:
    """SIGTERM（docker stop）时停止接受新连接，serve_forever 返回后由调用方等在途请求。只能在主线程装。"""
    if threading.current_thread() is not threading.main_thread():
        return

    def stop(signum, frame):
        log.info("收到 SIGTERM，停止接受新连接")
        # shutdown 会等 serve_forever 退出，不能在运行 serve_forever 的主线程里直接调。
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
