"""销售订单合同号回写 voucher_so_contract（受控 SQL，不走单据修改）：只改销售订单表头的合同号 cDefine1，
可选同时改客户合同号 cDefine2，已审核、已有下游单据的订单也能写。混入 U8CoClient。

contract_no 非空、不是「无」；customer_contract_no 可选，规则相同，给了则订单当前的客户合同号必须为空。
expect 必须给出 contract_no、customer_contract_no 两个键的当前值（None、空串、空白、「无」都表示当前为空）。
长度的细校验（自定义项档案的 iMaxLen）、订单是否被别人锁定、expect 是否相符都在桥；这里只挡明显的错误。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common
from co.client.u8co_idem import with_key

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

SO_CONTRACT_KINDS = ("sale_order",)
SO_CONTRACT_EXPECT_KEYS = ("contract_no", "customer_contract_no")
# 请求值的上限，与 API 一致（实际长度是自定义项档案的 iMaxLen，由桥核对）。
SO_CONTRACT_LIMIT = 120
_PLACEHOLDER = "无"


@dataclass(frozen=True)
class SoContract:
    """回写一张销售订单（call.doc_id）的合同号：kind 目前只有 sale_order。

    expect 是调用方认为的当前值，必须有 contract_no、customer_contract_no 两个键；值为 None 或字符串。
    customer_contract_no 为 None 表示不动客户合同号。
    """

    kind: str
    contract_no: str
    expect: dict[str, str | None]
    customer_contract_no: str | None = None


class U8CoSoContractMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def voucher_so_contract(
        self, call: U8Call, change: SoContract, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        if type(call.doc_id) is not int or call.doc_id <= 0:
            raise ValueError("单据 id 必须是正整数")
        if change.kind not in SO_CONTRACT_KINDS:
            raise ValueError(f"type 目前只能是 {'、'.join(SO_CONTRACT_KINDS)}")
        body = _common(call)
        body["type"] = change.kind
        body["id"] = call.doc_id
        body["contract_no"] = _target(change.contract_no, "contract_no")
        if change.customer_contract_no is not None:
            body["customer_contract_no"] = _target(change.customer_contract_no, "customer_contract_no")
        body["expect"] = _expect(change.expect)
        return self.call("/v1/vouchers/so_contract", with_key(body, idempotency_key))


def _target(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} 必须是非空字符串")
    if len(value) > SO_CONTRACT_LIMIT:
        raise ValueError(f"{label} 最长 {SO_CONTRACT_LIMIT} 个字符")
    if _has_control(value):
        raise ValueError(f"{label} 不能含控制字符")
    if value != value.strip():
        raise ValueError(f"{label} 首尾不能有空白")
    if value == _PLACEHOLDER:
        raise ValueError(f"{label} 不能是「{_PLACEHOLDER}」")
    return value


def _expect(values: object) -> dict[str, str | None]:
    if not isinstance(values, dict):
        raise ValueError("expect 必须是对象")
    _expect_keys(values)
    for key in SO_CONTRACT_EXPECT_KEYS:
        _expect_value(values[key], f"expect.{key}")
    return {key: values[key] for key in SO_CONTRACT_EXPECT_KEYS}


def _expect_keys(values: dict) -> None:
    unknown = sorted(str(key) for key in values if key not in SO_CONTRACT_EXPECT_KEYS)
    if unknown:
        raise ValueError(f"expect 有不允许的键: {', '.join(unknown)}；可用 {'、'.join(SO_CONTRACT_EXPECT_KEYS)}")
    missing = [key for key in SO_CONTRACT_EXPECT_KEYS if key not in values]
    if missing:
        raise ValueError(f"expect 必须给出 {'、'.join(missing)} 的当前值（None 或空串表示当前为空）")


def _expect_value(value: object, where: str) -> None:
    # 当前值只核对，不要求写法合规（旧数据可以有首尾空白）。
    if value is None:
        return
    if type(value) is not str or len(value) > SO_CONTRACT_LIMIT:
        raise ValueError(f"{where} 必须是 None 或不超过 {SO_CONTRACT_LIMIT} 个字符的字符串")
    if _has_control(value):
        raise ValueError(f"{where} 不能含控制字符")


def _has_control(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
