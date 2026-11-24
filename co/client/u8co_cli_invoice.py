"""客户开票单位命令：arc-customer-invoice-company-add --code C900001:C900002 [--default] [--customer-ufts N]；
arc-customer-invoice-company-delete、arc-customer-invoice-company（设默认）--code C900001:C900002
--expect-bdefault true|false [--customer-ufts N]。--expect-bdefault 是这一行当前的默认标志。由 u8co_cli_gl_arc 并入。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_arc_invoice import CustomerInvoiceAdd, CustomerInvoiceRow
from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient

_ADD = "arc-customer-invoice-company-add"
_DELETE = "arc-customer-invoice-company-delete"
_DEFAULT = "arc-customer-invoice-company"
_UFTS_HELP = "客户当前 ufts（档案 get 返回），对不上时桥 409"


def add_commands(sub: Any) -> None:
    add = sub.add_parser(_ADD)
    _add_auth(add)
    add.add_argument("--code", required=True)
    add.add_argument("--default", action="store_true")
    add.add_argument("--customer-ufts", default=None, help=_UFTS_HELP)
    for name in (_DELETE, _DEFAULT):
        row = sub.add_parser(name)
        _add_auth(row)
        row.add_argument("--code", required=True)
        row.add_argument("--expect-bdefault", required=True, choices=("true", "false"))
        row.add_argument("--customer-ufts", default=None, help=_UFTS_HELP)


def _row(parsed: argparse.Namespace) -> CustomerInvoiceRow:
    return CustomerInvoiceRow(parsed.code, parsed.expect_bdefault == "true", parsed.customer_ufts)


def _cmd_add(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    add = CustomerInvoiceAdd(parsed.code, True if parsed.default else None, parsed.customer_ufts)
    return client.arc_customer_invoice_company_add(call, add)


def _cmd_delete(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.arc_customer_invoice_company_delete(call, _row(parsed))


def _cmd_default(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.arc_customer_invoice_company(call, _row(parsed))


HANDLERS = {_ADD: _cmd_add, _DELETE: _cmd_delete, _DEFAULT: _cmd_default}
