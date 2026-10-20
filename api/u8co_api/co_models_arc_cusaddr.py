"""客户收货地址 /v1/co/archives/customer_address：只改 CusDeliverAdd 已有的行，不走 EAI（U8 没有收货地址组件）。"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StrictBool, StrictStr, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_arc import Code, check_read_code
from u8co_api.co_models_arc_cus import PERMISSION_RULES, check_text, only_read_only_rule
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

# 可改的键 → 列：code → cAddCode（nvarchar(30)），name → cDeliverAdd、unit → cDeliverUnit（nvarchar(255)），default → bDefault。
FIELD_KEYS = ("code", "name", "unit", "default")
_TEXT_LIMIT = {"code": 30, "name": 255, "unit": 255}
_NEEDS_EXPECT = ("name", "unit", "default")
Address = Annotated[
    str,
    Field(min_length=1, max_length=30, description="要改的地址当前的编码（CusDeliverAdd.cAddCode），1 到 30 个字符", examples=["1"]),
]

CUSTOMER_ADDRESS_HELP = op_doc(
    "改客户已有收货地址的编码、名称、收货单位、默认标志（CusDeliverAdd），不走 EAI，只改不增不删。",
    (
        "用法",
        (
            "code 是客户编码，address 是要改的地址当前的编码；fields 至少一项，键只收 code、name、unit、default",
            "code 是新地址编码（1 到 30 个字符，首尾不能有空白）；name 是地址名称、unit 是收货单位，空串清空，最长 255",
            "default 为 true 时本行设为默认，同客户其余地址取消默认；为 false 只取消本行",
            "expect 必填：fields 里除 code 以外的每一项都要给出当前值（CusDeliverAdd 没有行版本，expect 就是并发核对依据）",
            "dry_run 为 true 时在事务里跑完全部检查后回滚，不写库（validate）",
        ),
    ),
    (
        "规则",
        (
            "改 code 前：新编码不能已存在于该客户；旧编码不能被任何单据引用（按库里带 cAddCode 列的表逐一检查）",
            "全部请求值已等于当前值时成功返回 changed=false，不写库",
            "响应 before、after 是该客户全部地址行（code、name、unit、default），可当作改前留底",
            "不改客户档案的修改人、修改日期",
            *PERMISSION_RULES,
            only_read_only_rule("customer_address"),
        ),
    ),
    (
        "错误",
        (
            "404 not_found：客户不存在",
            "404 address_not_found：该客户没有这个地址编码",
            "409 state_mismatch：expect 与当前值不符（消息列出字段），或并发修改",
            "409 address_code_taken：新编码已被该客户的另一个地址使用",
            "409 address_in_use：旧编码已被单据引用，不能改编码（消息列出表名）",
            "504 outcome_unknown：写后回读不一致，先 get 核对再决定是否重试",
        ),
    ),
)


def _check_code(value: object) -> None:
    if not isinstance(value, str) or value == "":
        raise ValueError("fields.code 必须是 1 到 30 个字符")
    check_text(value, "fields.code", _TEXT_LIMIT["code"])


def _check_value(key: str, value: object, label: str) -> None:
    if key == "default":
        if not isinstance(value, bool):
            raise ValueError(f"{label}.default 必须是布尔值")
        return
    if not isinstance(value, str):
        raise ValueError(f"{label}.{key} 必须是字符串")
    check_text(value, f"{label}.{key}", _TEXT_LIMIT[key], trimmed=False)


def _check_fields(fields: dict[str, str | bool]) -> None:
    if not fields:
        raise ValueError("fields 至少要有一项")
    unknown = sorted(set(fields) - set(FIELD_KEYS))
    if unknown:
        raise ValueError(f"fields 有不认识的键: {', '.join(unknown)}；可用 {'、'.join(FIELD_KEYS)}")
    for key, value in fields.items():
        if key == "code":
            _check_code(value)
        else:
            _check_value(key, value, "fields")


def _check_expect(fields: dict[str, str | bool], expect: dict[str, str | bool] | None) -> None:
    wanted = [key for key in _NEEDS_EXPECT if key in fields]
    given = expect or {}
    missing = [key for key in wanted if key not in given]
    if missing:
        raise ValueError(f"fields 改 {'、'.join(missing)} 时必须在 expect 里给出当前值（地址表没有行版本）")
    extra = sorted(set(given) - set(wanted))
    if extra:
        raise ValueError(f"expect 有不在 fields 里的键: {', '.join(extra)}")
    for key, value in given.items():
        _check_value(key, value, "expect")


class ArcCustomerAddressIn(CoAuth):
    code: Code
    address: Address
    fields: dict[str, StrictStr | StrictBool] = Field(
        description="要改的列：code 新编码、name 地址名称、unit 收货单位（空串清空）、default 是否默认",
        examples=[{"code": "01", "name": "示例车间", "unit": "张三", "default": True}],
    )
    expect: dict[str, StrictStr | StrictBool] | None = Field(
        None,
        description="fields 里除 code 以外每一项的当前值（必填）；空串表示当前为空",
        examples=[{"name": "3车间", "unit": "", "default": False}],
    )
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"customer:{self.code}"

    @model_validator(mode="after")
    def _check(self) -> ArcCustomerAddressIn:
        check_read_code("customer", self.code, "code")
        check_text(self.address, "address", _TEXT_LIMIT["code"], trimmed=False)
        _check_fields(self.fields)
        _check_expect(self.fields, self.expect)
        return self


class ArcCustomerAddressOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    code: Scalar = Field(None, description="客户编码")
    address: Scalar = Field(None, description="请求里的当前地址编码")
    new_address: Scalar = Field(None, description="改后的地址编码；未改编码时与 address 相同（库里的写法）")
    changed: bool | None = Field(None, description="是否写了库；请求值都已等于当前值时为 false")
    before: list[dict[str, Scalar]] | None = Field(None, description="修改前该客户全部地址行：code、name、unit、default")
    after: list[dict[str, Scalar]] | None = Field(None, description="修改后（写后回读）该客户全部地址行")
