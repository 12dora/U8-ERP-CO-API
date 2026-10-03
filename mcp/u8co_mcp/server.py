"""u8co-mcp 命令：stdio（缺省）或 HTTP 上的 MCP 服务。

stdin / stdout 每行一条 JSON-RPC 2.0 消息（UTF-8）；stdout 只写协议消息，日志写 stderr。
stdin 读到 EOF 就正常退出（退出码 0）。
  u8co-mcp            运行服务（stdio）
  u8co-mcp --http [--host H] [--port P]
                      HTTP 方式（MCP Streamable HTTP，多人共用），令牌必须是 incoming、read_only 必须为 true，
                      见 web.py
  u8co-mcp --check    读配置、取令牌、调一次 GET /v1/co/health，结果写 stderr
  u8co-mcp --version
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from u8co_mcp import __version__
from u8co_mcp.app import App
from u8co_mcp.config import ConfigError, HttpConfig, load_config
from u8co_mcp.protocol import INTERNAL_ERROR, PARSE_ERROR, Protocol
from u8co_mcp.web import serve_http

log = logging.getLogger("u8co_mcp")

MAX_LINE = 4 * 1024 * 1024  # 单条消息上限（字节）


def build_app() -> App:
    try:
        config = load_config()
    except ConfigError as exc:
        # 配置有错也照常启动：工具调用返回 config_error，客户端里能直接看到原因。
        log.error("配置不可用：%s", exc)
        return App(None, config_error=str(exc))
    return App(config)


def serve(protocol: Protocol, stdin, stdout, max_line: int = MAX_LINE) -> int:
    for raw in _lines(stdin, max_line):
        reply = _reply(protocol, raw)
        if reply is None:
            continue
        try:
            stdout.write((reply + "\n").encode("utf-8"))
            stdout.flush()
        except BrokenPipeError:
            return 0
    return 0


def _lines(stdin, max_line: int):
    """逐行读 stdin（字节）。超过 max_line 的行丢掉剩余部分，产出 None。EOF 结束。"""
    while True:
        raw = stdin.readline(max_line + 1)
        if not raw:
            return
        if len(raw) <= max_line:
            yield raw
            continue
        while not raw.endswith(b"\n"):
            raw = stdin.readline(max_line)
            if not raw:
                break
        yield None


def _reply(protocol: Protocol, raw: bytes | None) -> str | None:
    if raw is None:
        return _parse_error("请求太大（单行超过上限）")
    try:
        line = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _parse_error("不是 UTF-8")
    if not line.strip():
        return None
    try:
        return protocol.handle_line(line)
    except Exception as exc:  # 兜底：一条坏消息不能让服务退出
        log.error("处理消息出错：%s", type(exc).__name__)
        return json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": INTERNAL_ERROR, "message": "内部错误"}})


def _parse_error(message: str) -> str:
    error = {"code": PARSE_ERROR, "message": message}
    return json.dumps({"jsonrpc": "2.0", "id": None, "error": error}, ensure_ascii=False)


def check(app: App) -> int:
    if app.config is None:
        print(f"配置不可用：{app.config_error}", file=sys.stderr)
        return 2
    if app.config.token.type == "incoming":
        # 令牌来自每个 HTTP 请求，这里没有令牌可用，只检查配置。
        print("正常：配置可用（incoming 令牌来自每个请求，未检查连通性）", file=sys.stderr)
        return 0
    result = app.call("u8_read", {"route": "health"})
    text = result["content"][0]["text"]
    print(("失败：" if result["isError"] else "正常：") + text, file=sys.stderr)
    return 1 if result["isError"] else 0


def run_http(app: App, host: str | None, port: int | None) -> int:
    """HTTP 方式。配置不可用、令牌不是 incoming 或没有 read_only: true 时不启动（退出码 2）：
    不能让共享的机器令牌暴露在网络上，多人共用的服务也不提供写入。"""
    if app.config is None:
        log.error("配置不可用，HTTP 方式不启动：%s", app.config_error)
        return 2
    if app.config.token.type != "incoming":
        log.error("HTTP 方式要求 token.type 为 incoming（令牌取自每个请求），不启动")
        return 2
    if not app.config.read_only:
        log.error("HTTP 方式要求配置 read_only: true，不启动")
        return 2
    cfg = app.config.http or HttpConfig()
    try:
        return serve_http(Protocol(app), cfg, host or cfg.host, cfg.port if port is None else port)
    except OSError as exc:
        log.error("HTTP 监听失败：%s", exc)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="u8co-mcp", description="用友 U8+ CO 接口的 MCP 服务（stdio 或 HTTP）")
    parser.add_argument("--check", action="store_true", help="检查配置和连通性后退出")
    parser.add_argument("--http", action="store_true", help="以 HTTP 方式运行（也可在配置里写 http.enabled）")
    parser.add_argument("--host", help="HTTP 方式的监听地址，覆盖 http.host")
    parser.add_argument("--port", type=int, help="HTTP 方式的端口，覆盖 http.port")
    parser.add_argument("--version", action="version", version=f"u8co-mcp {__version__}")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="u8co-mcp %(levelname)s %(message)s")
    app = build_app()
    if args.check:
        return check(app)
    if args.http or (app.config is not None and app.config.http is not None and app.config.http.enabled):
        return run_http(app, args.host, args.port)
    if app.config is not None and app.config.token.type == "incoming":
        log.error("token.type 为 incoming 时只能以 HTTP 方式运行（加 --http）")
        return 2
    out = sys.stdout.buffer
    # 协议之外任何误写 stdout 的内容都改道到 stderr，免得破坏消息流。
    sys.stdout = sys.stderr
    try:
        return serve(Protocol(app), sys.stdin.buffer, out)
    except KeyboardInterrupt:
        return 0
