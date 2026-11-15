"""期初命令。由 u8co_cli_gl_arc 并入。

openings-post --module pu|ia [--unpost] [--dry-run] [--idempotency-key K]
openings-arap --side ar|ap --action create --partner C --amount N --account S [--department D] [--person P]
              [--digest T] [--currency C --exch-rate R] [--dry-run] [--idempotency-key K]
openings-arap --side ar|ap --action delete|verify|unverify --id N [--dry-run] [--idempotency-key K]

--dry-run、--idempotency-key 由 u8co_cli_assist.add_dry_flags 统一加（两个命令都在 WRITE_COMMANDS 里）。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_openings import OPENING_ARAP_ACTIONS, OPENING_ARAP_CREATE, OPENING_ARAP_SIDES, OPENING_POST_MODULES

COMMAND = "openings-post"
ARAP_COMMAND = "openings-arap"
# create 字段的命令行开关（exch_rate → --exch-rate）。
_ARAP_HELP = {
    "partner": "客户编码（ar）或供应商编码（ap），create 必填",
    "amount": "原币金额，非 0、最多两位小数；负数为反方向余额（预收 / 预付），create 必填",
    "account": "本系统受控的末级科目编码，create 必填",
    "department": "部门编码",
    "person": "业务员编码",
    "digest": "摘要",
    "currency": "币种名称，省略为本位币",
    "exch_rate": "汇率，外币必填",
}
_NUMBERS = ("amount", "exch_rate")


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser(COMMAND)
    _add_auth(cmd)
    cmd.add_argument("--module", required=True, choices=OPENING_POST_MODULES, help="pu 采购管理，ia 存货核算（登录日期要在存货核算启用年度）")
    cmd.add_argument("--unpost", action="store_true", help="取消期初记账（缺省是期初记账）")
    arap = sub.add_parser(ARAP_COMMAND)
    _add_auth(arap)
    arap.add_argument("--side", required=True, choices=OPENING_ARAP_SIDES, help="ar 应收，ap 应付")
    arap.add_argument("--action", required=True, choices=OPENING_ARAP_ACTIONS)
    arap.add_argument("--id", type=int, help="期初单据主键，delete、verify、unverify 必填")
    for name in OPENING_ARAP_CREATE:
        kind = float if name in _NUMBERS else str
        arap.add_argument("--" + name.replace("_", "-"), dest=name, type=kind, help=_ARAP_HELP[name])


def _cmd_post(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.post_openings(call, parsed.module, "unpost" if parsed.unpost else "post")


def _cmd_arap(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    create = {name: getattr(parsed, name) for name in OPENING_ARAP_CREATE if getattr(parsed, name) is not None}
    return client.openings_arap(call, parsed.side, parsed.action, parsed.id, **create)


HANDLERS = {COMMAND: _cmd_post, ARAP_COMMAND: _cmd_arap}
