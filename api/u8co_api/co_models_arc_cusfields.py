"""客户字段 /v1/co/archives/customer_fields：只改 Customer 的简称、地区分类、停用日期，不走 EAI。"""

from __future__ import annotations

from pydantic import BaseModel, Field, StrictStr, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_arc import Code, check_read_code
from u8co_api.co_models_arc_cus import PERMISSION_RULES, check_text, check_ymd, only_read_only_rule
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

# 可改的键 → 列（Customer.cCusAbbName nvarchar(60)、cDCCode nvarchar(12)、dEndDate）。
FIELD_KEYS = ("abbrname", "district", "end_date")
_LIMIT = {"abbrname": 60, "district": 12, "end_date": 10}

CUSTOMER_FIELDS_HELP = op_doc(
    "改客户档案的简称、地区分类、停用日期（Customer.cCusAbbName、cDCCode、dEndDate），不走 EAI。",
    (
        "用法",
        (
            "code 是客户编码；fields 至少一项，键只收 abbrname、district、end_date",
            "abbrname 去掉首尾空白后不能为空，最长 60；district 写空串清空，否则须是 DistrictClass 里的末级地区分类",
            "end_date 写空串清空，否则 yyyy-MM-dd，不能早于客户建档日期",
            "expect 为调用方认为的当前值（键须在 fields 里；空串表示当前为空；日期按 yyyy-MM-dd）；强烈建议带上",
            "dry_run 为 true 时在事务里跑完全部检查后回滚，不写库（validate）",
        ),
    ),
    (
        "规则",
        (
            "EAI 客户修改会清空开户银行、银行账号并改写最后收款日期，本路由只更新请求里列出的列和修改人、修改日期",
            "全部请求值已等于当前值时成功返回 changed=false，不写库、不改审计列",
            *PERMISSION_RULES,
            only_read_only_rule("customer_fields"),
        ),
    ),
    (
        "错误",
        (
            "404 not_found：客户不存在",
            "409 state_mismatch：expect 与当前值不符（消息列出字段），或并发修改",
            "409 district_invalid：地区分类不存在或不是末级",
            "504 outcome_unknown：写后回读不一致，先 get 核对再决定是否重试",
        ),
    ),
)


def _check_value(key: str, value: str) -> None:
    if key == "end_date":
        if value != "":
            check_ymd(value, "fields.end_date")
        return
    label = f"fields.{key}"
    if key == "abbrname" and value.strip() == "":
        raise ValueError("fields.abbrname 不能为空")
    check_text(value, label, _LIMIT[key], trimmed=key == "district")


def _check_expect(key: str, value: str) -> None:
    # 当前值只核对，不要求写法合规（库里的旧数据可以有首尾空白）；日期仍须是 yyyy-MM-dd 才可能相等。
    if key == "end_date":
        if value != "":
            check_ymd(value, "expect.end_date")
        return
    if len(value) > _LIMIT[key]:
        raise ValueError(f"expect.{key} 最长 {_LIMIT[key]} 个字符")


class ArcCustomerFieldsIn(CoAuth):
    code: Code
    fields: dict[str, StrictStr] = Field(
        description="要改的列：abbrname 简称、district 地区分类（空串清空）、end_date 停用日期（空串清空）",
        examples=[{"abbrname": "示例简称", "district": "33", "end_date": "2026-10-20"}],
    )
    expect: dict[str, StrictStr] | None = Field(
        None,
        description="调用方认为的当前值，键须在 fields 里；空串表示当前为空。省略则不校验（不建议）",
        examples=[{"abbrname": "示例客户有限公司", "district": "", "end_date": ""}],
    )
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"customer:{self.code}"

    @model_validator(mode="after")
    def _check(self) -> ArcCustomerFieldsIn:
        check_read_code("customer", self.code, "code")
        if not self.fields:
            raise ValueError("fields 至少要有一项")
        _check_keys(self.fields, "fields", FIELD_KEYS)
        for key, value in self.fields.items():
            _check_value(key, value)
        if self.expect is not None:
            _check_keys(self.expect, "expect", tuple(self.fields))
            for key, value in self.expect.items():
                _check_expect(key, value)
        return self


def _check_keys(values: dict[str, str], label: str, allowed: tuple[str, ...]) -> None:
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise ValueError(f"{label} 有不认识的键: {', '.join(unknown)}；可用 {'、'.join(allowed)}")


class ArcCustomerFieldsOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    code: Scalar = Field(None, description="客户编码")
    changed: bool | None = Field(None, description="是否写了库；请求值都已等于当前值时为 false")
    before: dict[str, Scalar] | None = Field(None, description="修改前的 abbrname、district、end_date（空为空串）")
    after: dict[str, Scalar] | None = Field(None, description="修改后（写后回读）的 abbrname、district、end_date")
