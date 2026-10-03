"""经营管理查询 mgmt/sales、mgmt/arap、mgmt/cash_stock、mgmt/overview 的请求模型和接口说明。

公共字段（logins、fiscal_year、期间、consolidate）和响应信封在 co_models_mgmt。
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, field_validator

from u8co_api.co_clock import login_defaults
from u8co_api.co_models_mgmt import COMMON_HELP, MgmtIn
from u8co_api.co_models_gl import DATE

SalesDim = Literal["period", "customer", "inventory", "person", "department"]
ArapSide = Literal["ar", "ap", "both"]
PurchaseSource = Literal["auto", "invoice", "receipt"]
DEFAULT_BUCKETS = (30, 60, 90, 180)


def _distinct(values: list, what: str) -> list:
    if len(set(values)) != len(values):
        raise ValueError(f"{what} 不能重复")
    return values


def _real_date(value: str | None) -> str | None:
    if value is not None:
        try:
            dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("日期无效") from exc
    return value


class MgmtSalesIn(MgmtIn):
    group_by: list[SalesDim] = Field(
        default_factory=lambda: ["customer"],
        max_length=5,
        description="分组维度（不重复）：period 期间、customer 客户、inventory 存货、person 业务员、department 部门；"
        "缺省按客户，空数组只出合计",
    )
    top: StrictInt = Field(
        200, ge=1, le=500, description="每个账套按收入从高到低取前几组，1 到 500，缺省 200，其余并成 others"
    )
    include_unverified: StrictBool = Field(False, description="为 true 时含未复核的销售发票")

    @field_validator("group_by")
    @classmethod
    def _dims(cls, value: list[str]) -> list[str]:
        return _distinct(value, "group_by")


def default_as_of(fiscal_year: int, period: int) -> str:
    """往来截止日期的缺省值：period 的自然月末，晚于今天（配置时区）时取今天。"""
    last = dt.date(fiscal_year, period, calendar.monthrange(fiscal_year, period)[1]).isoformat()
    today = login_defaults()[0]
    return min(last, today)


class MgmtArapIn(MgmtIn):
    side: ArapSide = Field("both", description="ar 应收、ap 应付、both 两边（缺省）")
    as_of: str | None = Field(
        None,
        pattern=DATE,
        description="截止日期 yyyy-MM-dd（含当天），缺省为 period_to 的自然月末（晚于今天时取今天）",
    )
    buckets: list[StrictInt] = Field(
        default_factory=lambda: list(DEFAULT_BUCKETS),
        min_length=1,
        max_length=8,
        description="账龄分段的天数上限，严格递增，缺省 [30, 60, 90, 180]",
    )
    default_credit_days: StrictInt | None = Field(
        None, ge=0, le=3650, description="单据没有信用期时按几天算到期日，缺省同 arap_aging（0）"
    )
    top: StrictInt = Field(200, ge=1, le=500, description="每个账套按余额绝对值取前几个往来单位，1 到 500，缺省 200")

    @field_validator("as_of")
    @classmethod
    def _as_of(cls, value: str | None) -> str | None:
        return _real_date(value)

    @field_validator("buckets")
    @classmethod
    def _buckets(cls, value: list[int]) -> list[int]:
        if any(day < 1 or day > 3650 for day in value) or any(b <= a for a, b in zip(value, value[1:])):
            raise ValueError("buckets 必须是 1 到 3650 之间严格递增的天数")
        return value

    def sides(self) -> list[str]:
        return ["ar", "ap"] if self.side == "both" else [self.side]

    def effective_as_of(self) -> str:
        return self.as_of or default_as_of(self.fiscal_year, self.period_to)

    def audit_ref(self) -> str:
        return f"{self.fiscal_year}-{self.period_from}-{self.period_to}-{self.side}"


class MgmtCashStockIn(MgmtIn):
    """资金与存货按 period_to 一个期间出具（桥的 mgmt/cash_stock 只按一个期间）；period_from 只为与其他报表同形。"""

    top: StrictInt = Field(200, ge=1, le=500, description="产量、采购排行每个账套取前几个，1 到 500，缺省 200")
    purchase_source: PurchaseSource = Field(
        "auto", description="采购取数：auto（有采购发票时取发票，否则取采购入库，缺省）、invoice 发票、receipt 采购入库"
    )
    include_unverified: StrictBool = Field(False, description="为 true 时产成品入库、采购入库含未审核单据")

    def period_range(self) -> tuple[int, int] | None:
        return self.period_to, self.period_to


class MgmtOverviewIn(MgmtIn):
    as_of: str | None = Field(
        None,
        pattern=DATE,
        description="往来余额、账龄的截止日期 yyyy-MM-dd，缺省为 period_to 的自然月末（晚于今天时取今天）",
    )

    @field_validator("as_of")
    @classmethod
    def _as_of(cls, value: str | None) -> str | None:
        return _real_date(value)


SALES_SUMMARY = "经营管理：销售与毛利"
SALES_HELP = (
    "只读。按销售发票（缺省只含已复核）汇总所选期间的销售数量、收入（不含税、价税合计）和存货核算的销售成本，"
    "按 group_by 分组，算毛利和毛利率；每个账套按收入取前 top 组，其余并成 others，totals 是全部组的合计。"
    "收入以发票为准，与总账收入可能有期间、口径差，利润以 mgmt/pnl 为准。"
    "没有启用销售管理的账套 rows 为空、sa_enabled 为 false（见 warnings）。"
    "合并时：合计减去公司间内部销售（客户是本次所选的其他公司，按公司间对照的 as_customer），"
    "抵销明细见 eliminations；按存货分组时只按对照里的存货相加（编码相同不等于同一存货），"
    "没有对照的存货列在 unmapped；不按存货分组时 rows 是各账套去掉内部客户后的行（带 acc），"
    "客户、业务员、部门编码不跨账套合并。毛利率按合并数重算。" + COMMON_HELP
)
ARAP_SUMMARY = "经营管理：应收应付与账期"
ARAP_HELP = (
    "只读。按往来单位列出截至 as_of 的余额、预收付、按到期日的账龄与逾期、未结票据，"
    "近 12 个月单据的信用期分布、实际回款（付款）天数（金额加权的平均数与中位数）。"
    "每个账套按余额绝对值取前 top 个单位，其余并成 others。"
    "aging、overdue 是毛额（未核销的收款、付款单列在 prepaid）；aging_net、overdue_net 是按单位把 prepaid "
    "按先进先出冲抵最老账龄段后的净额（分段同 aging），逾期以净额为准；合计的净额是各单位净额之和。"
    "预收付超过账龄合计 10% 的账套见 warnings。"
    "周转天数：DSO（应收）/ DPO（应付）= 期末余额 ÷（近 12 个月单据金额 ÷ 天数），余额 ≤ 0 时为 null。"
    "没有启用应收（应付）款管理的账套 partners 为空（见 warnings）。"
    "as_of 缺省为 period_to 的自然月末（晚于今天时取今天）。"
    "合并时：往来单位是本次所选的其他公司（按公司间对照的 as_customer / as_vendor）的余额与单据金额"
    "（含净额账龄）从合计里去掉（内部往来抵销），DSO、DPO 按抵销后的余额和外部单据金额重算，抵销明细见 eliminations。" + COMMON_HELP
)
CASH_SUMMARY = "经营管理：资金与存货"
CASH_HELP = (
    "只读。按 period_to 一个会计期间出具（period_from 不参与取数）："
    "货币资金末级科目期末余额（只含已记账）、应收票据科目余额和当前未处理票据、"
    "存货核算的期末结存数量与金额、产成品入库按存货的数量排行、采购按供应商的金额排行。"
    "合并时：资金、票据、存货金额按账套相加（存货金额含期末存货中未实现的内部利润，未抵销，见 notes）；"
    "产量只按公司间对照里的存货相加，没有对照的列在 unmapped；"
    "采购去掉供应商是本次所选的其他公司（as_vendor）的部分，明细见 eliminations。" + COMMON_HELP
)
OVERVIEW_SUMMARY = "经营管理：关键指标"
OVERVIEW_HELP = (
    "只读。一次给出所选期间的关键指标：营业收入、毛利与毛利率、净利润（同 mgmt/pnl）、"
    "期末货币资金、存货金额（同 mgmt/cash_stock，取 period_to）、应收、应付余额与逾期应收、逾期应付（冲抵未核销预收付后的净额）、DSO、DPO"
    "（同 mgmt/arap，截至 as_of，缺省为 period_to 的自然月末）。"
    "by_account、consolidated 里是 kpis；合并口径同各报表（内部收入成本、内部往来抵销，未实现内部利润不抵销）。"
    "某一部分读取失败时该账套的指标不完整（missing），见 warnings。" + COMMON_HELP
)
