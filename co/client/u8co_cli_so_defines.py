"""销售订单表头自定义项写入命令：voucher-so-defines --type sale_order --id N --set cdefine1=X [--set cdefine2=Y …]
--expect cdefine1=A [--expect cdefine2= …] [--empty-value TOKEN …] [--cascade]。
每个 --set 的列都要有对应的 --expect；--expect KEY= 表示当前为空。--dry-run、--idempotency-key 由 u8co_cli_assist 统一加。
由 u8co_cli_gl_arc 并入。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_so_defines import SO_DEFINES_KINDS, SoDefines


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser("voucher-so-defines")
    _add_auth(cmd)
    cmd.add_argument("--type", required=True, choices=SO_DEFINES_KINDS)
    cmd.add_argument("--id", required=True, type=int)
    cmd.add_argument("--set", required=True, action="append", metavar="KEY=VALUE", help="要写入的列，如 cdefine1=DEMO-001")
    cmd.add_argument("--expect", action="append", default=[], metavar="KEY=VALUE", help="列的当前值；KEY= 表示当前为空")
    cmd.add_argument("--empty-value", action="append", default=[], metavar="TOKEN", help="另按空值处理的取值，可重复")
    cmd.add_argument("--cascade", action="store_true", help="连带更新下游单据表头")


def _pairs(values: list[str], flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for text in values:
        key, sep, value = text.partition("=")
        key = key.strip().lower()
        if not sep or not key:
            raise SystemExit(f"{flag} 要写成 KEY=VALUE，例如 {flag} cdefine1=DEMO-001")
        if key in out:
            raise SystemExit(f"{flag} {key} 重复")
        out[key] = value
    return out


def _change_from(parsed: argparse.Namespace) -> SoDefines:
    fields = _pairs(parsed.set, "--set")
    expect = _pairs(parsed.expect, "--expect")
    missing = [key for key in fields if key not in expect]
    if missing:
        raise SystemExit(f"--set {'、'.join(missing)} 必须同时给 --expect（KEY= 表示当前为空）")
    extra = sorted(set(expect) - set(fields))
    if extra:
        raise SystemExit(f"--expect {'、'.join(extra)} 没有对应的 --set")
    return SoDefines(parsed.type, fields, expect, tuple(parsed.empty_value), parsed.cascade)


def _cmd_so_defines(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.voucher_so_defines(call, _change_from(parsed))


HANDLERS = {"voucher-so-defines": _cmd_so_defines}
