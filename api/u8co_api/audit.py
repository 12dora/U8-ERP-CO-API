"""每个请求一行 JSON 审计。不写令牌、口令和审批意见。

去向由 U8CO_AUDIT_LOG 决定：stdout（缺省）、stderr、off，或一个绝对路径（追加写）。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request

_LOGGER = "u8co.audit"


class _StreamHandler(logging.Handler):
    # 每次写时再取 sys.stdout / sys.stderr，测试替换流后仍能收到。
    def __init__(self, stream_name: str) -> None:
        super().__init__()
        self._stream_name = stream_name

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = getattr(sys, self._stream_name)
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)


def configure_audit(target: str = "stdout") -> None:
    logger = logging.getLogger(_LOGGER)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for old in list(logger.handlers):
        logger.removeHandler(old)
        old.close()
    handler = _handler(target)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def _handler(target: str) -> logging.Handler:
    if target == "off":
        return logging.NullHandler()
    if target in ("stdout", "stderr"):
        return _StreamHandler(target)
    return logging.FileHandler(target, encoding="utf-8")


def install_audit(app: FastAPI) -> None:
    @app.middleware("http")
    async def audit_middleware(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            took = int((time.perf_counter() - started) * 1000)
            write_audit(request, status, took)


def write_audit(request: Request, status: int, took_ms: int) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "caller": getattr(request.state, "caller_id", None),
        "trust": getattr(request.state, "trust_name", None),
        "endpoint": request.url.path,
        "action": getattr(request.state, "audit_action", None),
        "accs": list(getattr(request.state, "accs", []) or []),
        "took_ms": took_ms,
        "status": status,
        "user": getattr(request.state, "end_user", None),
        # 令牌的 sub（代人调用的机器调用方：user 是头里的终端用户，sub 是令牌本身的主体）。
        "sub": getattr(request.state, "token_sub", None),
        "operators": list(getattr(request.state, "operators", []) or []),
        # 写操作预演（dry_run=true，什么都没写入）为 true，其余一律 false。
        "dry_run": getattr(request.state, "dry_run", False) is True,
        # 服务这次调用的桥：default 或 routes[序号]（U8CO_BRIDGE_ROUTES_FILE）；没走到桥时为 null。
        "bridge": getattr(request.state, "bridge", None),
        # 写入分类（write_class）：单据类型或路由族（gl、arap……）与操作；读路由为 null。
        "type": getattr(request.state, "write_type", None),
        "op": getattr(request.state, "write_op", None),
        # 调桥的结果：ok 或错误码（含本服务按写入策略拒绝的码）；没走到桥也没被策略拒绝时为 null。
        "outcome": getattr(request.state, "outcome", None),
        # 经营管理查询（co_mgmt_core）：report、fiscal_year、periods、consolidate、cache_hit；其它路由为 null。
        "mgmt": getattr(request.state, "mgmt_audit", None),
    }
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    logging.getLogger(_LOGGER).info(line)
