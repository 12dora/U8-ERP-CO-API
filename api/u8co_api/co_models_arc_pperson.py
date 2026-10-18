"""客户专管业务员 /v1/co/archives/customer_person：只改 Customer.cCusPPerson，不走 EAI。"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_arc import Code, check_read_code
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

# 与档案编码相同：不含控制字符，首尾不能有空白。业务员编码最长 20（Person.cPersonCode）。
_CTRL = "\\x00-\\x1f\\x7f\\uFFFE\\uFFFF"
_TRIMMED = re.compile(rf"[^\s{_CTRL}](?:[^{_CTRL}]*[^\s{_CTRL}])?")
Person = Annotated[
    str,
    Field(
        pattern=rf"^{_TRIMMED.pattern}$",
        min_length=1,
        max_length=20,
        description="目标专管业务员编码（Person.cPersonCode），1 到 20 个字符，首尾不能有空白，不含控制字符",
        examples=["EX040001"],
    ),
]

CUSTOMER_PERSON_HELP = op_doc(
    "只改客户档案的专管业务员（Customer.cCusPPerson），不走 EAI。",
    (
        "用法",
        (
            "code 是客户编码，person 是目标业务员编码",
            "expect 为调用方认为的当前业务员；空串表示当前为空；省略则不校验",
            "dry_run 为 true 时只校验，不写库（validate）",
            "当前专管业务员对应部门与客户分管部门一致（或分管部门为空）时，一并改成目标业务员的部门",
            "新部门须在 Department 中、末级且未停用，否则不改分管部门，响应 dept_note 说明原因",
            "分管部门实际改动时同步 Customer_Auth（Privilege_Type=0 的部门维度）",
        ),
    ),
    (
        "规则",
        (
            "EAI 客户修改会改动银行账号、最后收款日等无关列；本路由只更新 cCusPPerson 和条件满足时的 cCusDepart",
            "操作员须有客户档案编辑权限（AS011）；记录级客户权限同档案读取",
            "当前值已是 person 时成功返回，不写库、不改审计列",
            "写入分类 (archives, customer_person)：放行 archives/update 不连带放行本路由",
            "只读账套上仅当只读例外 type=archives、op=customer_person 且账套、操作员命中才穿过闸门",
        ),
    ),
    (
        "错误",
        (
            "404 not_found：客户不存在",
            "409 state_mismatch：expect 与当前值不符，或并发修改",
            "409 u8_rejected：业务员不存在",
            "504 outcome_unknown：写后回读不一致，先 get 核对再决定是否重试",
        ),
    ),
)


class ArcCustomerPersonIn(CoAuth):
    code: Code
    person: Person
    expect: str | None = Field(
        None,
        max_length=20,
        description="调用方认为的当前专管业务员编码。空串表示当前为空；省略则不校验",
        examples=["EX040002"],
    )
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"customer:{self.code}"

    @field_validator("expect")
    @classmethod
    def _expect(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        if _TRIMMED.fullmatch(value) is None:
            raise ValueError("expect 必须是 1 到 20 个字符，首尾不能有空白，不含控制字符")
        return value

    @model_validator(mode="after")
    def _code(self) -> ArcCustomerPersonIn:
        check_read_code("customer", self.code, "code")
        return self


class ArcCustomerPersonOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    code: Scalar = Field(None, description="客户编码")
    before: Scalar = Field(None, description="修改前的专管业务员编码；空则为 null")
    after: Scalar = Field(None, description="修改后的专管业务员编码")
    dept_before: Scalar = Field(None, description="修改前的分管部门编码；空则为 null")
    dept_after: Scalar = Field(None, description="修改后的分管部门编码；空则为 null")
    dept_note: Scalar = Field(
        None,
        description="未改分管部门的原因（新部门不存在、不是末级、已停用）；改了或无需改则为 null",
    )
