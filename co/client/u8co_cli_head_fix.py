"""历史发货单表头修正命令：voucher-head-fix --type dispatch --id N [--addr-code X] [--person-code Y]
[--expect-addr-code A] [--expect-person-code B] [--undo-file undo.json]。给了哪个 --xxx，就必须同时给对应的 --expect-xxx。
--addr-code "" 表示清空收货地址编码（回滚已补填的编码）；--expect-xxx "" 表示当前为空。
回滚时对调 fields 与 expect，并用 --undo-file 传入正向响应里的 undo（JSON 对象文件，内容就是 undo 本身）。由 u8co_cli_gl_arc 并入。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth, _json_object
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_head_fix import HEAD_FIX_KINDS, HeadFix

_KEYS = ("addr_code", "person_code")


def add_commands(sub: Any) -> None:
    fix = sub.add_parser("voucher-head-fix")
    _add_auth(fix)
    fix.add_argument("--type", required=True, choices=HEAD_FIX_KINDS)
    fix.add_argument("--id", required=True, type=int)
    fix.add_argument("--addr-code", default=None)
    fix.add_argument("--person-code", default=None)
    fix.add_argument("--expect-addr-code", default=None)
    fix.add_argument("--expect-person-code", default=None)
    fix.add_argument("--undo-file", default=None)


def _fix_from(parsed: argparse.Namespace) -> HeadFix:
    fields: dict[str, str] = {}
    expect: dict[str, str] = {}
    for key in _KEYS:
        new = getattr(parsed, key)
        old = getattr(parsed, "expect_" + key)
        flag = "--" + key.replace("_", "-")
        if new is None:
            if old is not None:
                raise SystemExit(f"给了 --expect-{flag[2:]} 就要同时给 {flag}")
            continue
        if old is None:
            raise SystemExit(f"{flag} 必须同时给 --expect-{flag[2:]}（当前值，空串表示当前为空）")
        fields[key] = new
        expect[key] = old
    if not fields:
        raise SystemExit("至少给 --addr-code 或 --person-code 之一")
    undo = _json_object(parsed.undo_file) if parsed.undo_file else None
    return HeadFix(parsed.type, fields, expect, undo)


def _cmd_head_fix(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.voucher_head_fix(call, _fix_from(parsed))


HANDLERS = {"voucher-head-fix": _cmd_head_fix}
