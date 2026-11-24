"""客户字段 /v1/co/archives/customer_fields：按白名单改 Customer 的列（键表见 keys 模块），不走 EAI。"""

from __future__ import annotations

from pydantic import BaseModel, Field, StrictStr, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_arc import Code, check_read_code
from u8co_api.co_models_arc_cus import PERMISSION_RULES, only_read_only_rule
from u8co_api.co_models_arc_cusfields_keys import (
    FIELD_KEYS,
    KEYS,
    check_date_order,
    check_expect_value,
    check_key_names,
    check_value,
    key_help_lines,
)
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

CUSTOMER_FIELDS_HELP = op_doc(
    "按白名单改客户档案的单个或多个列（名称、简称、联系方式、银行、结算、协议、分类、自定义项、比率等，"
    "键 → Customer 列见「用法」），不走 EAI。",
    (
        "用法",
        (
            "code 是客户编码；fields 至少一项，键只收白名单里的键（键名全小写，区分大小写，不认识的键 400）",
            *key_help_lines(),
            "必填的键（abbrname、name、ccusexch_name、sort_code）不能为空；其余文本、编码、日期、数值键写空串清空",
            "expect 为调用方认为的当前值（键须是 fields 里的键；空串表示当前为空；日期按 yyyy-MM-dd；"
            "编码类不分大小写，其余逐字比较）；强烈建议带上",
            "dry_run 为 true 时在事务里跑完全部检查后回滚，不写库（validate）",
        ),
    ),
    (
        "规则",
        (
            "EAI 客户修改会清空开户银行、银行账号并改写最后收款日期，本路由只更新请求里列出的列和修改人、修改日期",
            "响应的 before、after 与 409 的 error.detail.current 只含请求 fields 里的键",
            "改名称不改历史单据（发货单、销售订单、发票）上的客户名称副本，也不重算助记码；"
            "name 仅在账套开启客户名称唯一（AccInformation AA RptCusName=1）时查重",
            "head_corp_code 写空串表示总公司为本客户，否则须是已有客户；客户的开票单位既不是本客户也不是新总公司时拒绝",
            "ccustradeccode 同时写行业名称；crelvendor 同时维护供应商的关联客户；"
            "sa_protocol 与四个协议键按协议类别（iLZYJ）过滤；自定义项按账套的用户自定义设置校验",
            "end_date 不得早于 seed_date（同一请求都给了值时按请求值比较，否则与库里的值比较）",
            "全部请求值已等于当前值时成功返回 changed=false，不写库、不改审计列",
            *PERMISSION_RULES,
            "客户档案读取不按字段权限遮蔽，detail.current 与 archives/get 返回的数据相同；本路由只读取请求里的列",
            only_read_only_rule("customer_fields") + "；该例外覆盖本路由的整张白名单",
        ),
    ),
    (
        "错误",
        (
            "400：键不认识、值格式 / 长度 / 区间不对、必填键为空、end_date 早于 seed_date",
            "404 not_found：客户不存在",
            "409 state_mismatch：expect 与当前值不符（消息列出字段；error.detail.current 给 fields 各键的当前值，"
            "空为空串），或并发修改（没有 detail）",
            "409 <键>_invalid：引用的档案不存在、已停用、不是末级或不符合协议类别（例如 district_invalid、"
            "sa_protocol_invalid、ccustradeccode_invalid、self_define3_invalid；请求值已等于当前值时不检查）",
            "409 abbrname_taken、name_taken、crelvendor_taken：简称 / 名称 / 供应商已被其他客户使用",
            "409 head_corp_invoice_company：客户的开票单位既不是本客户也不是新总公司",
            "504 outcome_unknown：写后回读不一致，先 get 核对再决定是否重试",
        ),
    ),
)


def _check_fields(fields: dict[str, str]) -> None:
    check_key_names(fields, "fields", FIELD_KEYS)
    for key, value in fields.items():
        check_value(KEYS[key], value, f"fields.{key}")
    check_date_order(fields)


def _check_expect(expect: dict[str, str], fields: dict[str, str]) -> None:
    check_key_names(expect, "expect", tuple(fields))
    for key, value in expect.items():
        check_expect_value(KEYS[key], value, f"expect.{key}")


class ArcCustomerFieldsIn(CoAuth):
    code: Code
    fields: dict[str, StrictStr] = Field(
        description=(
            "要改的列：键 → Customer 列见接口说明（全小写，区分大小写）；值都是字符串，空串清空（必填键、"
            "0/1 键除外）；日期 yyyy-MM-dd；数值写十进制小数"
        ),
        examples=[{"abbrname": "示例简称", "contact": "联系人甲", "bank_open": "示例银行", "end_date": "2026-10-20"}],
    )
    expect: dict[str, StrictStr] | None = Field(
        None,
        description="调用方认为的当前值，键须在 fields 里；空串表示当前为空。省略则不校验（不建议）",
        examples=[{"abbrname": "示例客户有限公司", "contact": "", "bank_open": "", "end_date": ""}],
    )
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"customer:{self.code}"

    @model_validator(mode="after")
    def _check(self) -> ArcCustomerFieldsIn:
        check_read_code("customer", self.code, "code")
        if not self.fields:
            raise ValueError("fields 至少要有一项")
        _check_fields(self.fields)
        if self.expect is not None:
            _check_expect(self.expect, self.fields)
        return self


class ArcCustomerFieldsOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    code: Scalar = Field(None, description="客户编码")
    changed: bool | None = Field(None, description="是否写了库；请求值都已等于当前值时为 false")
    before: dict[str, Scalar] | None = Field(None, description="修改前请求 fields 里各键的值（空为空串）")
    after: dict[str, Scalar] | None = Field(None, description="修改后（写后回读）请求 fields 里各键的值")
