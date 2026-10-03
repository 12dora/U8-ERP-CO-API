"""经营管理查询（/v1/co/mgmt/*）的公共请求模型、响应信封，以及 mgmt/meta、mgmt/pnl 的模型。

其它经营管理报表（sales、arap、cash_stock、overview）的模型在 co_models_mgmt_reports，继承这里的 MgmtIn。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from u8co_api.co_models_ic import IcLogin
from u8co_api.co_models_gl import PASS, Scalar

_FORBID = ConfigDict(extra="forbid")
PnlDim = Literal["dept", "item"]
PnlDetail = Literal["prefix4", "leaf"]

COMMON_HELP = (
    "需要经营管理权限（与读、写权限分开）。logins 是 1 到 3 个账套的登录，令牌要能用其中每个账套，"
    "有一个不行就 403 account_not_allowed。多个账套缺省合并（consolidate 缺省在 2 个以上账套时为 true）："
    "合并要求这些账套在公司间对照（U8CO_IC_MAP_FILE）的同一组里，没配置对照 404 ic_not_configured，"
    "不在同一组 400 ic_group_mismatch；不要合并时写 consolidate=false。"
    "每次请求先读各账套的月结状态和数据水位（mgmt/meta），结果按水位缓存：所选期间在每个账套都已总账结账时缓存 6 小时，"
    "否则 60 秒；响应的 cache 给出 hit、age_s。部分账套失败时仍返回 200，失败的账套写进 warnings，"
    "complete 为 false，consolidated 为 null；全部失败返回 503。"
)
META_SUMMARY = "经营管理：月结状态与数据水位"
META_HELP = (
    "只读。按账套列出会计年度各期间总账、销售、采购、库存、存货核算、应收、应付的结账状态，未记账凭证数，"
    "各模块是否启用和数据水位（最近的凭证、单据），用来判断数据是否已定稿。不缓存，不合并。" + COMMON_HELP
)
PNL_SUMMARY = "经营管理：利润表"
PNL_HELP = (
    "只读。按账套和期间汇总损益科目（不含作废凭证和期末结转本年利润的凭证），缺省只含已记账凭证。"
    "按利润表行定义（缺省为新会计准则一级科目，现场可用 U8CO_MGMT_LINES_FILE 改）得到营业收入、营业成本、"
    "各项费用和派生的毛利、营业利润、利润总额、净利润，收入类为贷减借，费用类为借减贷。"
    "行定义之外的损益科目列在各账套的 unmapped。合并时各行按账套相加，再按公司间对照的 rev_cogs 抵销内部收入和成本，"
    "毛利率、净利率按合并数重算；期末存货中未实现的内部利润不抵销（见 notes）。" + COMMON_HELP
)


def _distinct(logins: list[IcLogin]) -> None:
    accs = [login.acc for login in logins]
    if len(set(accs)) != len(accs):
        raise ValueError("logins 里的账套不能重复")


class MgmtYearIn(BaseModel):
    """经营管理请求的公共部分：登录和会计年度。"""

    model_config = _FORBID
    logins: list[IcLogin] = Field(..., min_length=1, max_length=3, description="1 到 3 个账套的登录，账套不能重复")
    fiscal_year: StrictInt = Field(..., ge=2000, le=2099, description="会计年度，2000 到 2099")

    @model_validator(mode="after")
    def _logins(self) -> MgmtYearIn:
        _distinct(self.logins)
        return self

    def wants_consolidation(self) -> bool:
        return False

    def period_range(self) -> tuple[int, int] | None:
        """缓存时效看的期间（含两端）；None 表示不按期间判断，一律短时缓存。"""
        return None

    def audit_ref(self) -> str:
        return str(self.fiscal_year)

    def cache_params(self) -> dict[str, Any]:
        """进缓存键的参数：除登录以外的全部字段（登录另按账套和操作员进键）。"""
        return self.model_dump(mode="json", exclude={"logins"})


class MgmtIn(MgmtYearIn):
    """按期间查询的经营管理请求：一个会计年度内的期间区间，最多 12 个期间。"""

    period_from: StrictInt = Field(..., ge=1, le=12, description="起始期间，1 到 12")
    period_to: StrictInt = Field(..., ge=1, le=12, description="截止期间，1 到 12，不小于 period_from")
    consolidate: StrictBool | None = Field(
        None, description="是否合并。缺省在 2 个以上账套时为 true；只有 1 个账套时不合并"
    )

    @model_validator(mode="after")
    def _periods(self) -> MgmtIn:
        if self.period_from > self.period_to:
            raise ValueError("period_from 不能大于 period_to")
        return self

    def wants_consolidation(self) -> bool:
        if len(self.logins) < 2:
            return False
        return self.consolidate is not False

    def period_range(self) -> tuple[int, int] | None:
        return self.period_from, self.period_to

    def audit_ref(self) -> str:
        return f"{self.fiscal_year}-{self.period_from}-{self.period_to}"


class MgmtMetaIn(MgmtYearIn):
    pass


class MgmtPnlIn(MgmtIn):
    include_unposted: StrictBool = Field(False, description="为 true 时含未记账凭证（不含作废）")
    dims: list[PnlDim] = Field(
        default_factory=list,
        max_length=1,
        description="另按一个维度拆分各行：dept 部门、item 项目。缺省不拆",
    )


class MgmtOut(BaseModel):
    """经营管理查询的响应信封。"""

    model_config = PASS
    ok: bool = Field(description="至少一个账套读到")
    report: Scalar = Field(None, description="报表：meta、pnl、sales、arap、cash_stock、overview")
    fiscal_year: Scalar = Field(None, description="会计年度")
    period_from: Scalar = Field(None, description="起始期间")
    period_to: Scalar = Field(None, description="截止期间")
    complete: Scalar = Field(None, description="全部账套都读到时为 true")
    accounts: list[dict[str, Any]] | None = Field(
        None,
        description="每个账套：acc、name（公司间对照里的名称，没有时为账套号）、close（所选期间的结账状态，"
        "字段同 mgmt/meta 的 periods；该账套读取失败时为 null）",
    )
    by_account: dict[str, dict[str, Any]] | None = Field(
        None, description="账套号 → 成功时 {ok:true, ...该报表的数据}，失败时 {ok:false, status, error}"
    )
    consolidated: dict[str, Any] | None = Field(
        None, description="合并数（字段同 by_account 里的数据）；不合并或有账套失败时为 null"
    )
    eliminations: list[dict[str, Any]] | None = Field(None, description="合并时做的抵销")
    warnings: list[str] | None = Field(None, description="提醒，如失败的账套、没有对照的科目")
    notes: list[str] | None = Field(None, description="口径说明，如没有抵销的未实现内部利润")
    cache: dict[str, Any] | None = Field(None, description="hit（是否命中缓存）、age_s（缓存结果的秒数）")


PNL_LINES_HELP = (
    "lines：每行 id、name、kind（line 取数行、derived 派生行）、sign（income、expense，派生行没有）、"
    "total（期间合计）、periods（期间 → 金额）；拆维度时取数行另有 by_dim（维度编码 → 期间合计）。"
    "unmapped：行定义之外的损益科目 code、name、net（贷减借）。ratios：gross_margin_pct、net_margin_pct（百分比）"
)
