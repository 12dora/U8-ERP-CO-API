"""幂等键：任何写调用都可带 idempotency_key。桥按「caller + 账套 + 路由 + 键」只执行一次。

直接调用桥时不带 caller，桥记为 direct。同一个键重试会原样拿到第一次的结果（包括 504 outcome_unknown），
内容不同返回 409 idempotency_mismatch。预演（dry_run）不能带键；应收应付自动核销的试算（dry_run: true）也不能带。
"""

from __future__ import annotations

import argparse
import re
from typing import Any

LEGACY_ROUTES = frozenset({"/v1/sale-orders/verify", "/v1/dispatches/verify"})


def _ops(root: str, ops: str) -> set[str]:
    return {root + op for op in ops.split()}


# 桥的写路由（同 co/bridge/src/WriteGate.cs 的 Writes，去掉 /u8co 前缀）。写预演（u8co_assist.dry_fields）和幂等键共用这一份。
WRITE_ROUTES = frozenset(
    _ops("/v1/vouchers/", "verify create update delete close generate lock")
    | _ops("/v1/workflow/", "submit withdraw approve disagree return abandon resubmit")
    | _ops("/v1/gl/vouchers/", "create update void unvoid verify unverify sign unsign delete post")
    | {"/v1/gl/vouchers/reverse"}  # 红字冲销
    | {"/v1/gl/vouchers/unpost"}  # 取消记账（测试账套）
    | {"/v1/gl/transfer/pnl", "/v1/gl/transfer/custom"}  # 期间损益结转、自定义转账（测试账套）
    | _ops("/v1/archives/", "create update delete")
    | _ops("/v1/arap/", "writeoff writeoff/cancel writeoff/auto voucher voucher/delete")
    # 应收应付处理：转账、并账、红票对冲、取消处理、处理制单、汇兑损益及取消。
    | _ops("/v1/arap/", "transfer merge red_offset process/cancel process/voucher exchange_gain exchange_gain/cancel")
    | {"/v1/arap/bad_debt"}  # 坏账发生、收回、计提
    | _ops("/v1/notes/", "create delete")  # 应收票据登记、删除
    | {"/v1/notes/process"}  # 票据处理（结算、贴现、背书）
    | {"/v1/openings/post"}  # 期初记账
    | {"/v1/openings/arap"}  # 应收应付期初单据
    | {"/v1/periods/close"}  # 月末结账
    | {"/v1/ia/post", "/v1/ia/period_end"}  # 存货核算记账、期末处理
    | LEGACY_ROUTES
)
# 支持幂等键的路由就是全部写路由（桥 IdemReq.Supports = WriteGate.IsWrite），不另列清单。
IDEMPOTENT_ROUTES = WRITE_ROUTES
AUTO_ROUTE = "/v1/arap/writeoff/auto"

_KEY = re.compile(r"^[\x21-\x7e]{1,128}\Z")
_HELP = "幂等键（1 到 128 个可见 ASCII 字符）。同一个键重试不会重复执行，拿到第一次的结果"


def check_key(key: object) -> str:
    if type(key) is not str or _KEY.fullmatch(key) is None:
        raise ValueError("idempotency_key 必须是 1 到 128 个可见 ASCII 字符，不能含空格")
    return key


def with_key(fields: dict[str, Any], key: str | None) -> dict[str, Any]:
    """key 为 None 时原样返回；否则校验后加进请求字段。"""
    if key is None:
        return fields
    fields["idempotency_key"] = check_key(key)
    return fields


def key_fields(route: str, fields: dict[str, Any], key: str) -> dict[str, Any]:
    """client.keyed(key) 的写调用：在请求字段后面加 idempotency_key，读路由原样返回。

    字段里已有另一个键、或自动核销试算（dry_run: true）带键，在本地就拒绝（桥也会 400）。"""
    if route not in WRITE_ROUTES:
        return fields
    check_key(key)
    if fields.get("idempotency_key", key) != key:
        raise ValueError("请求里已有另一个 idempotency_key")
    if route == AUTO_ROUTE and fields.get("dry_run") is True:
        raise ValueError("自动核销试算（dry_run）不能带幂等键")
    clean = dict(fields)
    clean["idempotency_key"] = key
    return clean


def add_key_arg(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--idempotency-key", default=None, help=_HELP)


def key_kwargs(parsed: argparse.Namespace) -> dict[str, str]:
    # 没给 --idempotency-key 时不传这个参数，调用方式与以前完全相同。
    key = getattr(parsed, "idempotency_key", None)
    if key is None:
        return {}
    return {"idempotency_key": key}
