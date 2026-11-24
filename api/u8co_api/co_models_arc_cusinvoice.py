"""客户开票单位 customer_invoice_company_add、customer_invoice_company_delete、customer_invoice_company：
受控 SQL 写 sa_invoicecustomers，不走 EAI。

U8 客户 EAI 的修改会按单据重写整张开票单位表（缺子表时只留一条自身默认行并删掉其余），所以新增、删除、设默认都走这三条。
读取仍是档案种类 customer_invoice_company 的 get、list（只读）。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_arc import check_read_code
from u8co_api.co_models_arc_cus import PERMISSION_RULES, only_read_only_rule
from u8co_api.co_models_arc_sa import INVOICE_COMPANY
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

# 编码两段都是客户编码（最长 20），整串 "<客户编码>:<开票单位编码>" 最长 41。
_CODE_MAX = 41
_CTRL = "\\x00-\\x1f\\x7f\\uFFFE\\uFFFF"
InvoiceCode = Annotated[
    str,
    Field(
        pattern=rf"^[^{_CTRL}]+$",
        min_length=3,
        max_length=_CODE_MAX,
        description="`<客户编码>:<开票单位编码>`，两段最长 20、20，首尾不能有空白，不含控制字符",
        examples=["C900001:C900002"],
    ),
]
_UFTS = Field(
    None,
    pattern=r"^[0-9]{1,19}$",
    description="客户当前 ufts（档案 get 返回的 pubufts 十进制串）；省略则不校验",
    examples=["7891234"],
)

_UFTS_ERROR = (
    "409 state_mismatch：expect.customer_ufts 与客户当前 ufts 不符"
    "（detail.current.customer_ufts 给当前值），或并发修改"
)
_BDEFAULT_ERROR = (
    "409 state_mismatch：expect.bdefault 与当前值不符（detail.current.bdefault 给当前值），"
    "或 customer_ufts 不符、并发修改"
)
_COMMON_RULES = (
    "开票单位必须是已有的客户档案（已停用的也可以）；两个编码都要有客户记录级权限",
    "默认开票单位变化时同事务更新客户档案的 cInvoiceCompany、修改人、修改日期，按 pubufts 乐观锁；不改任何单据",
    "写后同事务回读整个客户的开票单位行核对，响应 before、after 是该客户全部开票单位行（code、bdefault）",
    "为什么走 SQL：U8 客户 EAI 的修改会按单据重写整张开票单位表，缺子表时只留一条自身默认行并删掉其余",
    *PERMISSION_RULES,
)

CUSTOMER_INVOICE_COMPANY_ADD_HELP = op_doc(
    "给客户新增一个开票单位（sa_invoicecustomers 一行），不走 EAI。",
    (
        "用法",
        (
            "code 写成 `<客户编码>:<开票单位编码>`；default 缺省 false",
            "客户还没有默认开票单位（含第一行）或 default 为 true 时，新行成为唯一默认，其余行取消默认",
            "expect 可选，只收 customer_ufts",
            "dry_run 为 true 时在事务里跑完全部检查后回滚，不写库（validate）",
        ),
    ),
    (
        "规则",
        (
            "同一对已存在时按重放处理：成功返回 changed=false，不写库；仅当要求 default=true 而该行不是默认时 409",
            *_COMMON_RULES,
            "回滚用 customer_invoice_company_delete（非默认行）",
            only_read_only_rule("customer_invoice_company_add"),
        ),
    ),
    (
        "错误",
        (
            "404 not_found：客户不存在",
            "409 invoice_company_invalid：开票单位不是已有的客户档案",
            "409 invoice_company_taken：该对已存在但不是默认，而请求要求 default=true"
            "（改默认用 customer_invoice_company）",
            _UFTS_ERROR,
            "504 outcome_unknown：写后回读不一致，先 get 核对再决定是否重试",
        ),
    ),
)

CUSTOMER_INVOICE_COMPANY_DELETE_HELP = op_doc(
    "删除客户的一个开票单位（sa_invoicecustomers 一行），不走 EAI；"
    "用于回滚 customer_invoice_company_add 或清理多余行。",
    (
        "用法",
        (
            "code 写成 `<客户编码>:<开票单位编码>`",
            "expect.bdefault 必填，是这一行当前的默认标志（表没有行版本，expect 就是并发核对依据）；"
            "expect.customer_ufts 可选",
            "dry_run 为 true 时在事务里跑完全部检查后回滚，不写库（validate）",
        ),
    ),
    (
        "规则",
        (
            "默认开票单位不能删除，要先用 customer_invoice_company 把默认设到别的行；"
            "所以客户有了开票单位就至少留一行，且必有默认",
            *_COMMON_RULES,
            only_read_only_rule("customer_invoice_company_delete"),
        ),
    ),
    (
        "错误",
        (
            "404 not_found：客户不存在",
            "404 invoice_company_not_found：该客户没有这个开票单位",
            _BDEFAULT_ERROR,
            "409 invoice_company_is_default：默认开票单位不能删除，请先设置其他默认",
            "504 outcome_unknown：写后回读不一致，先 get 核对再决定是否重试",
        ),
    ),
)

CUSTOMER_INVOICE_COMPANY_HELP = op_doc(
    "把客户的某个开票单位设为默认（这一行 bdefault=1，同客户其余行 0），不走 EAI。",
    (
        "用法",
        (
            "code 写成 `<客户编码>:<开票单位编码>`，必须已在该客户的开票单位里",
            "expect.bdefault 必填，是这一行当前的默认标志；expect.customer_ufts 可选",
            "dry_run 为 true 时在事务里跑完全部检查后回滚，不写库（validate）",
        ),
    ),
    (
        "规则",
        (
            "这一行已是唯一默认时成功返回 changed=false，不写库",
            "改回原默认即可还原：对原默认行再调用本路由",
            *_COMMON_RULES,
            only_read_only_rule("customer_invoice_company"),
        ),
    ),
    (
        "错误",
        (
            "404 not_found：客户不存在",
            "404 invoice_company_not_found：该客户没有这个开票单位",
            "409 invoice_company_invalid：开票单位不是已有的客户档案",
            _BDEFAULT_ERROR,
            "504 outcome_unknown：写后回读不一致，先 get 核对再决定是否重试",
        ),
    ),
)


class InvoiceAddExpect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_ufts: str | None = _UFTS


class InvoiceExpect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bdefault: StrictBool = Field(description="这一行当前的默认标志", examples=[False])
    customer_ufts: str | None = _UFTS


class _InvoiceIn(CoAuth):
    code: InvoiceCode
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"customer:{self.code.partition(':')[0]}"

    @model_validator(mode="after")
    def _check(self) -> _InvoiceIn:
        check_read_code(INVOICE_COMPANY, self.code, "code")
        return self


class ArcCustomerInvoiceCompanyAddIn(_InvoiceIn):
    default: StrictBool | None = Field(None, description="为 true 时新行成为唯一默认开票单位；省略等同 false")
    expect: InvoiceAddExpect | None = Field(None, description="可选，只收 customer_ufts")

    @field_validator("default", mode="before")
    @classmethod
    def _explicit_default(cls, value: object) -> object:
        # 省略时不进桥请求体；显式给出就必须是布尔值（null 不接受）。
        if value is None:
            raise ValueError("default 必须是布尔值")
        return value


class ArcCustomerInvoiceCompanyDeleteIn(_InvoiceIn):
    expect: InvoiceExpect = Field(description="这一行当前的 bdefault（必填）；customer_ufts 可选")


class ArcCustomerInvoiceCompanyIn(_InvoiceIn):
    expect: InvoiceExpect = Field(description="这一行当前的 bdefault（必填）；customer_ufts 可选")


class ArcCustomerInvoiceCompanyOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    code: Scalar = Field(None, description="请求里的 `<客户编码>:<开票单位编码>`")
    changed: bool | None = Field(None, description="是否写了库；重放、已是唯一默认时为 false")
    before: list[dict[str, Scalar]] | None = Field(None, description="修改前该客户全部开票单位行（code、bdefault）")
    after: list[dict[str, Scalar]] | None = Field(None, description="修改后（写后回读）该客户全部开票单位行")
