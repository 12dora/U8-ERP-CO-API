"""客户收货地址的新增与删除 customer_address_add、customer_address_delete：受控 SQL 写 CusDeliverAdd，不走 EAI。

改已有地址行见 co_models_arc_cusaddr（customer_address）；这里补上 U8 客户端能做而本接口原先做不到的增删。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StrictBool, StrictStr, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_arc import Code, check_read_code
from u8co_api.co_models_arc_cus import PERMISSION_RULES, check_text, only_read_only_rule
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

# 新增可给的键 → 列：name → cDeliverAdd、unit → cDeliverUnit（nvarchar(255)），default → bDefault。
ADD_KEYS = ("name", "unit", "default")
# 删除的 expect 必须给全的键（表没有行版本，expect 就是并发核对依据）。
EXPECT_KEYS = ("name", "unit", "default")
_TEXT_LIMIT = {"name": 255, "unit": 255}
_ADDRESS_MAX = 30
NewAddress = Annotated[
    str,
    Field(
        min_length=1,
        max_length=_ADDRESS_MAX,
        description="新地址编码（CusDeliverAdd.cAddCode），1 到 30 个字符，首尾不能有空白",
        examples=["02"],
    ),
]
OldAddress = Annotated[
    str,
    Field(
        min_length=1,
        max_length=_ADDRESS_MAX,
        description="要删除的地址当前的编码（CusDeliverAdd.cAddCode），1 到 30 个字符",
        examples=["02"],
    ),
]

CUSTOMER_ADDRESS_ADD_HELP = op_doc(
    "给客户新增一个收货地址行（CusDeliverAdd），不走 EAI；已有地址的改动用 customer_address。",
    (
        "用法",
        (
            "code 是客户编码，address 是新地址编码（1 到 30 个字符，首尾不能有空白）",
            "fields.name 必填，是地址名称，1 到 255 个字符，不能全是空白；unit 是收货单位，可省略，空串按空，最长 255",
            "default 缺省 false；为 true 时本行设为默认，同客户其余地址取消默认",
            "dry_run 为 true 时在事务里跑完全部检查后回滚，不写库（validate）",
        ),
    ),
    (
        "规则",
        (
            "同一请求重放（地址编码、名称、收货单位、默认标志都已一致）成功返回 changed=false，不写库",
            "响应 before、after 是该客户全部地址行（code、name、unit、default），可当作新增前后的留底",
            "新增的行只写 cAddCode、cDeliverAdd、cDeliverUnit、bDefault 并生成行 GUID，其余列为空",
            "不改客户档案的修改人、修改日期",
            "回滚用 customer_address_delete",
            *PERMISSION_RULES,
            only_read_only_rule("customer_address_add"),
        ),
    ),
    (
        "错误",
        (
            "404 not_found：客户不存在",
            "409 address_code_taken：该客户已有这个地址编码，且名称、收货单位、默认标志与请求不同",
            "409 address_name_taken：该客户的另一个地址已用同一名称",
            "504 outcome_unknown：写后回读不一致，先 get 核对再决定是否重试",
        ),
    ),
)

CUSTOMER_ADDRESS_DELETE_HELP = op_doc(
    "删除客户的一个收货地址行（CusDeliverAdd），不走 EAI；用于回滚 customer_address_add 或清理多余地址。",
    (
        "用法",
        (
            "code 是客户编码，address 是要删除的地址编码",
            "expect 必填：name、unit、default 三项都要给出当前值（地址表没有行版本，expect 就是并发核对依据）",
            "name、unit 的当前值为空时写空串",
            "dry_run 为 true 时在事务里跑完全部检查后回滚，不写库（validate）",
        ),
    ),
    (
        "规则",
        (
            "任何带 cAddCode 列的表引用该客户的这个地址编码时拒绝删除（与 customer_address 改编码前的检查相同）",
            "要删的是默认地址且客户还有其它地址时拒绝：先用 customer_address 把默认移到别的地址；只剩这一行时允许",
            "响应 before、after 是该客户全部地址行（code、name、unit、default），可当作删除前后的留底",
            "不改客户档案的修改人、修改日期",
            *PERMISSION_RULES,
            only_read_only_rule("customer_address_delete"),
        ),
    ),
    (
        "错误",
        (
            "404 not_found：客户不存在",
            "404 address_not_found：该客户没有这个地址编码",
            "409 state_mismatch：expect 与当前值不符（消息列出字段），或并发修改",
            "409 address_in_use：地址编码已被单据或档案引用（消息列出表名）",
            "409 address_is_default：这是默认地址且客户还有其它地址",
            "504 outcome_unknown：写后回读不一致，先 get 核对再决定是否重试",
        ),
    ),
)


def _check_add_fields(fields: dict[str, str | bool]) -> None:
    unknown = sorted(set(fields) - set(ADD_KEYS))
    if unknown:
        raise ValueError(f"fields 有不认识的键: {', '.join(unknown)}；可用 {'、'.join(ADD_KEYS)}")
    if "name" not in fields:
        raise ValueError("fields.name 必填")
    for key, value in fields.items():
        if key == "default":
            if not isinstance(value, bool):
                raise ValueError("fields.default 必须是布尔值")
        elif not isinstance(value, str):
            raise ValueError(f"fields.{key} 必须是字符串")
        else:
            check_text(value, f"fields.{key}", _TEXT_LIMIT[key], trimmed=False)
    if fields["name"].strip() == "":
        raise ValueError("fields.name 不能为空")


def _check_expect(expect: dict[str, str | bool]) -> None:
    missing = [key for key in EXPECT_KEYS if key not in expect]
    if missing:
        raise ValueError(f"expect 必须给出 {'、'.join(missing)} 的当前值（地址表没有行版本）")
    extra = sorted(set(expect) - set(EXPECT_KEYS))
    if extra:
        raise ValueError(f"expect 有不认识的键: {', '.join(extra)}；可用 {'、'.join(EXPECT_KEYS)}")
    for key, value in expect.items():
        if key == "default":
            if not isinstance(value, bool):
                raise ValueError("expect.default 必须是布尔值")
        elif not isinstance(value, str):
            raise ValueError(f"expect.{key} 必须是字符串")
        else:
            check_text(value, f"expect.{key}", _TEXT_LIMIT[key], trimmed=False)


class ArcCustomerAddressAddIn(CoAuth):
    code: Code
    address: NewAddress
    fields: dict[str, StrictStr | StrictBool] = Field(
        description="新地址的列：name 地址名称（必填）、unit 收货单位（空串按空）、default 是否默认（缺省 false）",
        examples=[{"name": "示例车间", "unit": "张三", "default": False}],
    )
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"customer:{self.code}"

    @model_validator(mode="after")
    def _check(self) -> ArcCustomerAddressAddIn:
        check_read_code("customer", self.code, "code")
        check_text(self.address, "address", _ADDRESS_MAX)
        _check_add_fields(self.fields)
        return self


class ArcCustomerAddressDeleteIn(CoAuth):
    code: Code
    address: OldAddress
    expect: dict[str, StrictStr | StrictBool] = Field(
        description="要删的地址行的当前值，name、unit、default 三项必填；空串表示当前为空",
        examples=[{"name": "示例车间", "unit": "张三", "default": False}],
    )
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"customer:{self.code}"

    @model_validator(mode="after")
    def _check(self) -> ArcCustomerAddressDeleteIn:
        check_read_code("customer", self.code, "code")
        check_text(self.address, "address", _ADDRESS_MAX, trimmed=False)
        _check_expect(self.expect)
        return self


class ArcCustomerAddressRowsOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    code: Scalar = Field(None, description="客户编码")
    address: Scalar = Field(None, description="请求里的地址编码")
    changed: bool | None = Field(None, description="是否写了库；新增重放（行已一致）时为 false")
    before: list[dict[str, Scalar]] | None = Field(
        None, description="修改前该客户全部地址行（code、name、unit、default）"
    )
    after: list[dict[str, Scalar]] | None = Field(None, description="修改后（写后回读）该客户全部地址行")
