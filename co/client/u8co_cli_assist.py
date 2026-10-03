"""命令行：写命令的 --dry-run（预演）和 --idempotency-key（幂等键），名称解析 resolve，幂等结果查询 idem-get。
meta-fields、search、load-many、arc-get-many 在 u8co_cli_lookup，经这里并入。由 u8co_cli 并入。

resolve --item customer=张三贸易 --item inventory=示例存货X1 [--limit 5] [--include-disabled]
idem-get --route /v1/vouchers/verify --key <幂等键> [--caller <调用方>]
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_cli_lookup import HANDLERS as LOOKUP_HANDLERS
from co.client.u8co_cli_lookup import add_commands as add_lookup_commands
from co.client.u8co_idem import IDEMPOTENT_ROUTES, add_key_arg
from co.client.u8co_gl_arc import GL_OPS

# 带 --dry-run 的写命令。旧路由 sale-order / dispatch 不支持预演（改用 verify）。
WRITE_COMMANDS = (
    "create", "update", "delete", "verify", "close", "generate", "lock",
    "wf-submit", "wf-withdraw", "wf-resubmit", "wf-approve", "wf-disagree", "wf-return", "wf-abandon",
    "gl-create", "gl-update", "arc-create", "arc-update", "arc-delete", "openings-post", "periods-close",
    "ia-post", "ia-period-end", "openings-arap",
    "gl-reverse",  # 红字冲销
    "gl-transfer-pnl", "gl-transfer-custom",  # 期间损益结转、自定义转账（测试账套）
) + tuple(f"gl-{op}" for op in GL_OPS)
_DRY_HELP = "预演：桥照常校验（能回滚的操作真跑一遍再回滚），不写入；响应带 mode、docs、warnings"
_ITEM_HELP = "档案=名称或编码，例如 customer=张三贸易；可重复，最多 20 项"


# 幂等键在全部写命令上，含不支持预演的旧路由 sale-order / dispatch。
KEY_COMMANDS = WRITE_COMMANDS + ("sale-order", "dispatch")


def add_dry_flags(sub: Any) -> None:
    for name in WRITE_COMMANDS:
        cmd = sub.choices.get(name)
        if cmd is not None:
            cmd.add_argument("--dry-run", action="store_true", help=_DRY_HELP)
    for name in KEY_COMMANDS:
        cmd = sub.choices.get(name)
        # create、generate、gl-create、arc-create 早就有 --idempotency-key（传给库方法），不重复加。
        if cmd is not None and "--idempotency-key" not in cmd._option_string_actions:
            add_key_arg(cmd)


def add_commands(sub: Any) -> None:
    resolve = sub.add_parser("resolve")
    _add_auth(resolve)
    resolve.add_argument("--item", required=True, action="append", metavar="ARCHIVE=Q", help=_ITEM_HELP)
    resolve.add_argument("--limit", type=int)
    resolve.add_argument("--include-disabled", action="store_true")
    idem = sub.add_parser("idem-get")
    _add_auth(idem)
    idem.add_argument("--route", required=True, choices=sorted(IDEMPOTENT_ROUTES))
    idem.add_argument("--key", required=True)
    idem.add_argument("--caller")
    add_lookup_commands(sub)


def parse_items(values: list[str]) -> list[dict[str, str]]:
    items = []
    for text in values:
        archive, sep, query = text.partition("=")
        if not sep or not archive.strip() or not query.strip():
            raise SystemExit("--item 要写成 ARCHIVE=Q，例如 customer=张三贸易")
        items.append({"archive": archive.strip(), "q": query.strip()})
    return items


def dry_client(client: U8CoClient, parsed: argparse.Namespace) -> U8CoClient:
    """带 --dry-run 时换成预演客户端，带 --idempotency-key 时换成带键的客户端；预演不能带幂等键。"""
    key = getattr(parsed, "idempotency_key", None)
    if not getattr(parsed, "dry_run", False):
        return client if key is None else client.keyed(key)
    if key is not None:
        raise SystemExit("预演不能带 --idempotency-key")
    return client.dry()


def _cmd_resolve(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.resolve(call, parse_items(parsed.item), parsed.limit, parsed.include_disabled)


def _cmd_idem(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.idempotency_get(call, parsed.route, parsed.key, parsed.caller)


HANDLERS = {"resolve": _cmd_resolve, "idem-get": _cmd_idem, **LOOKUP_HANDLERS}
