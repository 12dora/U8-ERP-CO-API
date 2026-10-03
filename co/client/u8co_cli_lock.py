"""锁定、解锁命令：lock --type sale_order --id N --action lock|unlock。由 u8co_cli_gl_arc 并入。"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_lock import LOCK_ACTIONS, LOCKABLE_KINDS


def add_commands(sub: Any) -> None:
    lock = sub.add_parser("lock")
    _add_auth(lock)
    lock.add_argument("--type", required=True, choices=LOCKABLE_KINDS)
    lock.add_argument("--id", required=True, type=int)
    lock.add_argument("--action", required=True, choices=LOCK_ACTIONS)


def _cmd_lock(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.lock_voucher(call, parsed.type, parsed.action)


HANDLERS = {"lock": _cmd_lock}
