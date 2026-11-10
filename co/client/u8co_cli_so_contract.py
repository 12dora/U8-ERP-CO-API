"""销售订单合同号回写命令：voucher-so-contract --type sale_order --id N --contract-no X [--customer-contract-no Y] [--replace]
--expect-contract-no A --expect-customer-contract-no B。两个 --expect-… 都必须给（空串表示当前为空）。
由 u8co_cli_gl_arc 并入。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_so_contract import SO_CONTRACT_KINDS, SoContract


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser("voucher-so-contract")
    _add_auth(cmd)
    cmd.add_argument("--type", required=True, choices=SO_CONTRACT_KINDS)
    cmd.add_argument("--id", required=True, type=int)
    cmd.add_argument("--contract-no", required=True)
    cmd.add_argument("--customer-contract-no", default=None)
    cmd.add_argument("--expect-contract-no", required=True)
    cmd.add_argument("--expect-customer-contract-no", required=True)
    cmd.add_argument("--replace", action="store_true", help="合同号已有别的值也覆盖（合同改号）")


def _change_from(parsed: argparse.Namespace) -> SoContract:
    expect = {
        "contract_no": parsed.expect_contract_no,
        "customer_contract_no": parsed.expect_customer_contract_no,
    }
    return SoContract(parsed.type, parsed.contract_no, expect, parsed.customer_contract_no, parsed.replace)


def _cmd_so_contract(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.voucher_so_contract(call, _change_from(parsed))


HANDLERS = {"voucher-so-contract": _cmd_so_contract}
