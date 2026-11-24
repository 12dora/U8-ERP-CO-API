"""客户开票单位的新增、删除、设默认（受控 SQL，不走客户 EAI）：arc_customer_invoice_company_add、
arc_customer_invoice_company_delete、arc_customer_invoice_company。混入 U8CoClient。

code 写成 "<客户编码>:<开票单位编码>"（两段最长 20）；读取仍用 arc_get / arc_list（archive=customer_invoice_company）。
删除、设默认必须给出这一行当前的 bdefault（表没有行版本，expect 就是并发核对依据）；customer_ufts 可选，
是客户当前 ufts（档案 get 返回），对不上时桥 409 state_mismatch。默认开票单位变化时桥同事务改客户的 cInvoiceCompany。
细的校验（客户、开票单位是否存在、默认行不能删）在桥。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _arc_code, _common
from co.client.u8co_idem import with_key

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

_INVOICE_ARCHIVE = "customer_invoice_company"
_PART_MAX = 20
_UFTS = re.compile(r"[0-9]{1,19}")


@dataclass(frozen=True)
class CustomerInvoiceAdd:
    """新增一个开票单位；default 为 true 时新行成为唯一默认（客户还没有默认时无论如何都是默认）。"""

    code: str
    default: bool | None = None
    customer_ufts: str | None = None


@dataclass(frozen=True)
class CustomerInvoiceRow:
    """删除或设默认的目标：bdefault 是这一行当前的默认标志。"""

    code: str
    bdefault: bool
    customer_ufts: str | None = None


class U8CoArcInvoiceMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def arc_customer_invoice_company_add(
        self, call: U8Call, add: CustomerInvoiceAdd, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        body = _common(call)
        body["code"] = _pair_code(add.code)
        if add.default is not None:
            body["default"] = _flag(add.default, "default")
        expect = _expect_ufts(add.customer_ufts)
        if expect:
            body["expect"] = expect
        return self.call("/v1/archives/customer_invoice_company_add", with_key(body, idempotency_key))

    def arc_customer_invoice_company_delete(
        self, call: U8Call, row: CustomerInvoiceRow, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        body = with_key(_row_body(call, row), idempotency_key)
        return self.call("/v1/archives/customer_invoice_company_delete", body)

    def arc_customer_invoice_company(
        self, call: U8Call, row: CustomerInvoiceRow, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self.call("/v1/archives/customer_invoice_company", with_key(_row_body(call, row), idempotency_key))


def _row_body(call: U8Call, row: CustomerInvoiceRow) -> dict[str, Any]:
    body = _common(call)
    body["code"] = _pair_code(row.code)
    body["expect"] = {"bdefault": _flag(row.bdefault, "expect.bdefault"), **_expect_ufts(row.customer_ufts)}
    return body


def _pair_code(value: object) -> str:
    text = _arc_code(_INVOICE_ARCHIVE, value, "档案编码 code")
    customer, _, company = text.partition(":")
    for part in (customer, company):
        if not 1 <= len(part) <= _PART_MAX or part != part.strip():
            raise ValueError(f"code 必须写成 <客户编码>:<开票单位编码>，两段长度 1 到 {_PART_MAX}，首尾不能有空白")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError("code 不能含控制字符")
    return text


def _flag(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} 必须是布尔值")
    return value


def _expect_ufts(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if type(value) is not str or _UFTS.fullmatch(value) is None:
        raise ValueError("customer_ufts 必须是 1 到 19 位数字串（客户档案 get 返回的 ufts）")
    return {"customer_ufts": value}
