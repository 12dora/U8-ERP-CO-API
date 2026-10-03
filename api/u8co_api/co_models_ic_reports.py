"""公司间（数据归并）的多账套汇总（reports/aggregate）和合并报表（reports/consolidation）的请求、响应模型，
以及汇总里各内层报表的桥路由、入参模型和翻页上限。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_ic import IcLogin
from u8co_api.co_models_list import CoStockIn
from u8co_api.co_models_gl import PASS, Scalar
from u8co_api.co_models_reports import ReportAgingIn, ReportArapIn, ReportGlBalanceIn
from u8co_api.errors import bad_request
from u8co_api.http import validation_field

_FORBID = ConfigDict(extra="forbid")
AggReport = Literal["stock_current", "arap_balance", "arap_aging", "gl_balance"]
# 内层报表的这些字段由本接口填（登录来自 logins，翻页由本接口读完），params 里不能给。
RESERVED_PARAMS = frozenset(CoAuth.model_fields) | {"after", "limit"}


@dataclass(frozen=True)
class InnerReport:
    path: str
    model: type[CoAuth]
    page_size: int
    max_pages: int


INNER = {
    "stock_current": InnerReport("/v1/stock/current", CoStockIn, 500, 40),
    "arap_balance": InnerReport("/v1/reports/arap_balance", ReportArapIn, 1000, 20),
    "arap_aging": InnerReport("/v1/reports/arap_aging", ReportAgingIn, 1000, 20),
    "gl_balance": InnerReport("/v1/reports/gl_balance", ReportGlBalanceIn, 1000, 20),
}

AGGREGATE_SUMMARY = "多账套汇总"
AGGREGATE_HELP = (
    "只读。对 1 到 3 个同一公司组的账套各调一次同一张报表（stock_current 现存量、arap_balance 往来余额、"
    "arap_aging 账龄分析、gl_balance 科目余额表），params 按该报表的入参校验（不含登录字段和 after、limit，"
    "本接口自动读完全部页，每个账套最多 2 万行，超过时该账套记为 422 ic_too_many_rows）。"
    "by_account 按账套给出读到的 items，或该账套的 error。totals 只按公司间对照（U8CO_IC_MAP_FILE）合计："
    "现存量按对照里的存货 id，往来按往来单位所代表的公司（as_customer / as_vendor），科目余额按对照里的逻辑科目；"
    "对照之外的存货、往来单位和末级科目不相加，计数列在 unmapped。"
    "部分账套失败时仍返回 200，失败的账套不计入合计并写进 warnings；全部失败返回 503。"
    "令牌要能用其中每个账套，有一个不行就 403 account_not_allowed。"
    "没配置对照 404 ic_not_configured，账套不在同一组 400 ic_group_mismatch。"
)
CONSOLIDATION_SUMMARY = "合并报表（往来抵销）"
CONSOLIDATION_HELP = (
    "只读。对 2 到 3 个同一公司组的账套读科目余额表（只含已记账凭证、非零行），按公司间对照的逻辑科目（gl）"
    "列出各账套的映射试算（trial），再按对照里的抵销对（elim，rule=ar_ap）读辅助核算余额表"
    "（应收方按客户、应付方按供应商，科目取逻辑科目在该账套的编码）："
    "抵销额 = 应收、应付期末余额绝对值中较小的一个（两边方向相反时不抵销并提醒），差额另列。"
    "consolidated 按逻辑科目给出各账套期末净额之和、抵销分录和抵销后的余额。"
    "对照之外的末级科目不相加，计数列在 unmapped。未实现内部利润、收入成本没有抵销（见 notes）。"
    "部分账套失败时仍返回 200，complete 为 false，consolidated 为 null，涉及失败账套的抵销对跳过；全部失败返回 503。"
)


def inner_body(report: str, login: CoAuth, params: dict[str, Any]) -> dict[str, Any]:
    """按内层报表的入参模型校验 params（带上这个账套的登录），返回不含登录字段的桥请求体。

    不合法 400，field 以 params. 开头。"""
    reserved = sorted(RESERVED_PARAMS.intersection(params))
    if reserved:
        raise bad_request(f"params 不能含 {reserved[0]}", field=f"params.{reserved[0]}")
    inner = INNER[report]
    try:
        model = inner.model.model_validate({**login.model_dump(exclude_none=True), **params})
    except ValidationError as exc:
        errors = [dict(item, loc=("params", *item.get("loc", ()))) for item in exc.errors()]
        field = validation_field(errors) or "params"
        raise bad_request(f"请求参数无效：{field}", field=field) from None
    return model.model_dump(exclude_none=True, exclude=set(CoAuth.model_fields))


def _distinct(logins: list[IcLogin]) -> None:
    accs = [login.acc for login in logins]
    if len(set(accs)) != len(accs):
        raise ValueError("logins 里的账套不能重复")


class IcAggregateIn(BaseModel):
    model_config = _FORBID
    report: AggReport = Field(
        ...,
        description="内层报表：stock_current 现存量、arap_balance 往来余额、arap_aging 账龄分析、gl_balance 科目余额表",
    )
    logins: list[IcLogin] = Field(..., min_length=1, max_length=3, description="1 到 3 个账套的登录，账套不能重复")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="传给每个账套的内层报表参数，规则同该报表（例如 arap_balance 的 side、as_of；gl_balance 的 "
        "period_from、period_to）。不能含 acc、operator、password、year、date、after、limit",
    )

    @model_validator(mode="after")
    def _checked(self) -> IcAggregateIn:
        _distinct(self.logins)
        return self

    def audit_ref(self) -> str:
        return self.report


class IcAggregateOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="至少一个账套读到")
    report: Scalar = Field(None, description="内层报表")
    group: Scalar = Field(None, description="公司组 id")
    by_account: dict[str, dict[str, Any]] | None = Field(
        None,
        description="账套号 → 成功时 {ok:true, items}（全部页的 items，字段同内层报表），"
        "失败时 {ok:false, status, error}",
    )
    totals: list[dict[str, Any]] | None = Field(
        None,
        description="按对照的合计。stock_current：inventory（对照 id）、codes、qty、qty_available、by_account；"
        "arap_*：company（往来单位代表的账套）、name、side、debit、credit、balance"
        "（账龄另有 aging、prepaid、overdue）、"
        "by_account（partner 和该账套的金额）；gl_balance：logical、codes、open_dir、open_debit、open_credit、"
        "period_debit、period_credit、ytd_debit、ytd_credit、close_dir、close_debit、close_credit、by_account。"
        "期初、期末按借减贷的净额合计后再拆方向",
    )
    unmapped: list[dict[str, Any]] | None = Field(
        None,
        description="每个账套没进合计的部分：acc、kind（inventory 存货、partner 往来单位、subject 末级科目）、count、"
        "codes（最多 50 个），往来另有 balance（这些往来单位的余额合计）",
    )
    warnings: list[str] | None = Field(None, description="提醒，如失败的账套、不能按公司合计的分组")


class IcConsolidationIn(BaseModel):
    model_config = _FORBID
    logins: list[IcLogin] = Field(..., min_length=2, max_length=3, description="2 到 3 个账套的登录，账套不能重复")
    fiscal_year: StrictInt = Field(..., ge=2000, le=2099, description="会计年度，2000 到 2099")
    period_from: StrictInt = Field(..., ge=1, le=12, description="起始期间，1 到 12")
    period_to: StrictInt = Field(..., ge=1, le=12, description="截止期间，1 到 12，不小于 period_from")

    @model_validator(mode="after")
    def _checked(self) -> IcConsolidationIn:
        _distinct(self.logins)
        if self.period_from > self.period_to:
            raise ValueError("period_from 不能大于 period_to")
        return self

    def audit_ref(self) -> str:
        return f"{self.fiscal_year}-{self.period_from}-{self.period_to}"


class IcConsolidationOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="至少一个账套读到")
    group: Scalar = Field(None, description="公司组 id")
    fiscal_year: Scalar = Field(None, description="会计年度")
    period_from: Scalar = Field(None, description="起始期间")
    period_to: Scalar = Field(None, description="截止期间")
    complete: Scalar = Field(None, description="全部账套都读到时为 true")
    by_account: dict[str, dict[str, Any]] | None = Field(
        None, description="账套号 → 成功时 {ok:true}，失败时 {ok:false, status, error}"
    )
    trial: list[dict[str, Any]] | None = Field(
        None,
        description="每个账套：acc、name、balanced（末级科目期末借贷是否相等）、close_debit、close_credit（末级合计）、"
        "items（对照里的逻辑科目：logical、code、name、period_debit、period_credit、close_net 借减贷）",
    )
    eliminations: list[dict[str, Any]] | None = Field(
        None,
        description="抵销：rule、ar（acc、logical、code、partner、balance 借减贷）、ap（同，balance 贷减借）、amount、"
        "difference（应收减应付）、entries（logical、debit、credit），不能抵销时另有 skipped",
    )
    consolidated: list[dict[str, Any]] | None = Field(
        None,
        description="按逻辑科目：logical、codes、by_account（账套 → 期末净额）、period_debit、period_credit、"
        "close_net（各账套之和）、elim_debit、elim_credit、after_net、close_dir、close_debit、close_credit（抵销后）",
    )
    unmapped: list[dict[str, Any]] | None = Field(None, description="没进对照的末级科目：acc、kind、count、codes")
    warnings: list[str] | None = Field(None, description="提醒，如失败的账套、方向相反没有抵销的往来")
    notes: list[str] | None = Field(None, description="口径说明（没有做的抵销）")
