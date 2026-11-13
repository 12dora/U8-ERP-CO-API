"""销售订单合同号回写命令：voucher-so-contract --type sale_order --id N --contract-no X [--customer-contract-no Y] [--replace]
--expect-contract-no A --expect-customer-contract-no B。两个 --expect-… 都必须给（空串表示当前为空）。
成对设置：--mode pair --contract-no X --internal-contract-no Y [--cascade] --expect-contract-no A --expect-internal-contract-no B。
由 u8co_cli_gl_arc 并入。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_so_contract import SO_CONTRACT_KINDS, SoContract, SoContractPair


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser("voucher-so-contract")
    _add_auth(cmd)
    cmd.add_argument("--type", required=True, choices=SO_CONTRACT_KINDS)
    cmd.add_argument("--id", required=True, type=int)
    cmd.add_argument("--contract-no", required=True)
    cmd.add_argument("--customer-contract-no", default=None)
    cmd.add_argument("--expect-contract-no", required=True)
    cmd.add_argument("--expect-customer-contract-no", default=None, help="不带 --mode 时必填")
    cmd.add_argument("--replace", action="store_true", help="合同号已有别的值也覆盖（合同改号）")
    cmd.add_argument("--mode", choices=("pair",), default=None, help="pair：合同号与内部合同号成对设置")
    cmd.add_argument("--internal-contract-no", default=None, help="--mode pair 时必填")
    cmd.add_argument("--expect-internal-contract-no", default=None, help="--mode pair 时必填")
    cmd.add_argument("--cascade", action="store_true", help="--mode pair 时连带更新下游单据表头")


def _pair_from(parsed: argparse.Namespace) -> SoContractPair:
    if parsed.customer_contract_no is not None or parsed.expect_customer_contract_no is not None or parsed.replace:
        raise SystemExit("--mode pair 不收 --customer-contract-no、--expect-customer-contract-no、--replace")
    if parsed.internal_contract_no is None or parsed.expect_internal_contract_no is None:
        raise SystemExit("--mode pair 需要 --internal-contract-no 和 --expect-internal-contract-no")
    expect = {"contract_no": parsed.expect_contract_no, "internal_contract_no": parsed.expect_internal_contract_no}
    return SoContractPair(parsed.type, parsed.contract_no, parsed.internal_contract_no, expect, parsed.cascade)


def _change_from(parsed: argparse.Namespace) -> SoContract:
    if parsed.internal_contract_no is not None or parsed.expect_internal_contract_no is not None or parsed.cascade:
        raise SystemExit("--internal-contract-no、--expect-internal-contract-no、--cascade 只在 --mode pair 时可用")
    if parsed.expect_customer_contract_no is None:
        raise SystemExit("需要 --expect-customer-contract-no（空串表示当前为空）")
    expect = {
        "contract_no": parsed.expect_contract_no,
        "customer_contract_no": parsed.expect_customer_contract_no,
    }
    return SoContract(parsed.type, parsed.contract_no, expect, parsed.customer_contract_no, parsed.replace)


def _cmd_so_contract(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    if parsed.mode == "pair":
        return client.voucher_so_contract_pair(call, _pair_from(parsed))
    return client.voucher_so_contract(call, _change_from(parsed))


HANDLERS = {"voucher-so-contract": _cmd_so_contract}
