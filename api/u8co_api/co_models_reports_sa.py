"""销售类报表 /v1/co/reports/open_orders、sales_price_history、customer_churn 的请求和响应。
字段名与桥一致，响应放行桥多给的字段。

金额是数字（本币，两位小数），数量是数字（六位小数），单价是数字（六位小数）。三张报表都不翻页：
open_orders 和 sales_price_history 用 limit 截断，customer_churn 每张名单各取前 limit 个，counts 是全部个数。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import DATE, PASS, Scalar
from u8co_api.co_models_reports import Word, _real_date

_DB = "只查数据库，不调用 U8 组件"
_CUSTOMER = "客户编码，1 到 20 个，每个 1 到 60 个字符"
_INV = "存货编码，1 到 20 个，每个 1 到 60 个字符"

OPEN_ORDERS_SUMMARY = "在手订单"
OPEN_ORDERS_DOC = op_doc(
    "未执行完的销售订单行，按客户、存货或逐单汇总未发货（或未出库、未开票）的数量和金额。",
    (
        "用法",
        (
            "basis 选余量口径：ship 未发货（缺省）、out 未出库、invoice 未开票",
            "group_by 选汇总方式：customer 按客户（缺省）、inventory 按存货、order 逐订单行",
            "date_from、date_to 限订单日期；due_before 只要预发货日期早于这一天的行；都不给就是全部在手订单",
            "超过 limit 的行不返回，truncated 为 true；totals 覆盖全部符合条件的行，不受 limit 影响",
        ),
    ),
    (
        "规则",
        (
            "在手订单行：订单表头和表行都没有关闭人，且所选口径的余量（订单数量 − 累计发货、出库或开票数量）大于 0；"
            "订单是否审核不限，同订单执行（order_execution）",
            "open_amount = 余量 × 本币价税合计 ÷ 订单数量（元）",
            "overdue_lines 是预发货日期早于登录日期（date）的行数，as_of 就是该日期",
            "按客户、存货汇总按余额金额从大到小；逐订单行按预发货日期从早到晚",
            "open_qty 是存货主计量单位的数量；按客户汇总时混有不同计量单位，数量只作参考",
            _DB,
        ),
    ),
)

SALES_PRICE_HISTORY_SUMMARY = "销售价格历史"
SALES_PRICE_HISTORY_DOC = op_doc(
    "销售发票表体逐行列出日期、数量、金额和折算的单价，新的在前，用来看某客户或某存货的价格走势。",
    (
        "用法",
        (
            "customer、inv 至少要给一个；date_to 缺省为登录日期（date）",
            "超过 limit 的行不返回（最旧的被截掉），truncated 为 true",
            "include_unverified 为 true 时连销售未复核的发票一起列，缺省只含已复核的",
        ),
    ),
    (
        "规则",
        (
            "不含期初发票；红字发票的数量、金额是负数，照实列出",
            "amount 无税、amount_tax 价税合计，均为本币（元）；currency 是发票币种（外币发票的金额也已折成本币）",
            "unit_price = amount ÷ qty，unit_price_tax = amount_tax ÷ qty，六位小数；qty 为 0 时为 null",
            "qty 是存货主计量单位的数量",
            _DB,
        ),
    ),
)

CUSTOMER_CHURN_SUMMARY = "客户变动"
CUSTOMER_CHURN_DOC = op_doc(
    "按销售发票对比当前窗口和之前的基线窗口，找出新客户、流失客户和收入明显下降的客户。",
    (
        "用法",
        (
            "当前窗口是 date_from 到 date_to（缺省为登录日期）；基线窗口是 date_from 之前的 lookback_months 个月，缺省 6 个月",
            "decline_pct 是下降幅度阈值，缺省 30：当前月均收入低于基线月均收入的 (100 − decline_pct)% 才算下降",
            "new、lost、declining 三张名单各取前 limit 个；counts 是各名单的全部个数，active 是当前窗口有发票的客户数",
        ),
    ),
    (
        "规则",
        (
            "收入 = 已复核、非期初的销售发票的无税本币金额，红字发票是负数，照实相加",
            "new：当前窗口有发票且 revenue_current 大于 0，date_from 之前从没有过发票（first_date 不早于 date_from）",
            "lost：基线窗口收入为正，当前窗口没有任何发票",
            "declining：两个窗口都有发票，基线收入为正，且当前月均收入低于阈值",
            "月均 = 窗口收入 ÷ 窗口月数；窗口恰好是整月（月初到月末）时月数是整数，否则按天数 ÷ 30.4375 折算",
            "months_current、months_baseline 是窗口里有发票的自然月个数；first_date、last_date 是到 date_to 为止的第一张、最后一张发票日期",
            "person_name 是该客户最后一张发票上的业务员",
            "新客户按当前收入、流失客户按基线收入、下降客户按月均收入下降额，从大到小",
            _DB,
        ),
    ),
)


def _order(low: str, high: str | None) -> None:
    if high is not None and low > high:
        raise ValueError("date_from 不能晚于 date_to")


class _Dated(CoAuth):
    date_from: str = Field(..., pattern=DATE, description="起始日期 yyyy-MM-dd（含）")
    date_to: str | None = Field(None, pattern=DATE, description="截止日期 yyyy-MM-dd（含），缺省为 date")

    @field_validator("date_from", "date_to")
    @classmethod
    def _real(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _range(self) -> _Dated:
        _order(self.date_from, self.date_to)
        return self


class OpenOrdersIn(CoAuth):
    basis: Literal["ship", "out", "invoice"] | None = Field(
        None, description="余量口径：ship 未发货（缺省）、out 未出库、invoice 未开票"
    )
    group_by: Literal["customer", "inventory", "order"] | None = Field(
        None, description="汇总方式：customer 按客户（缺省）、inventory 按存货、order 逐订单行"
    )
    customer: list[Word] | None = Field(None, min_length=1, max_length=20, description=_CUSTOMER)
    inv: list[Word] | None = Field(None, min_length=1, max_length=20, description=_INV)
    person: list[Word] | None = Field(None, min_length=1, max_length=20, description="业务员编码，1 到 20 个")
    date_from: str | None = Field(None, pattern=DATE, description="订单日期起 yyyy-MM-dd（含）")
    date_to: str | None = Field(None, pattern=DATE, description="订单日期止 yyyy-MM-dd（含）")
    due_before: str | None = Field(None, pattern=DATE, description="只要预发货日期早于这一天（不含）的行 yyyy-MM-dd")
    limit: StrictInt | None = Field(None, ge=1, le=1000, description="返回的最多行数，1 到 1000，缺省 200")

    @field_validator("date_from", "date_to", "due_before")
    @classmethod
    def _real(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _range(self) -> OpenOrdersIn:
        if self.date_from is not None:
            _order(self.date_from, self.date_to)
        return self


class SalesPriceHistoryIn(_Dated):
    customer: list[Word] | None = Field(None, min_length=1, max_length=20, description=_CUSTOMER)
    inv: list[Word] | None = Field(None, min_length=1, max_length=20, description=_INV)
    include_unverified: StrictBool | None = Field(None, description="为 true 时连销售未复核的发票一起列。缺省只含已复核的")
    limit: StrictInt | None = Field(None, ge=1, le=2000, description="返回的最多行数，1 到 2000，缺省 500")

    @model_validator(mode="after")
    def _need_one(self) -> SalesPriceHistoryIn:
        if self.customer is None and self.inv is None:
            raise ValueError("customer 和 inv 至少要给一个")
        return self


class CustomerChurnIn(_Dated):
    lookback_months: StrictInt | None = Field(None, ge=1, le=24, description="基线窗口月数（date_from 之前），1 到 24，缺省 6")
    decline_pct: StrictInt | None = Field(None, ge=1, le=99, description="下降幅度阈值（百分数），1 到 99，缺省 30")
    limit: StrictInt | None = Field(None, ge=1, le=200, description="每张名单最多行数，1 到 200，缺省 50")


class _Page(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")


class OpenOrdersOut(_Page):
    basis: Scalar = Field(None, description="实际使用的余量口径")
    group_by: Scalar = Field(None, description="实际使用的汇总方式")
    as_of: Scalar = Field(None, description="判断逾期的日期（登录日期）")
    count: Scalar = Field(None, description="返回的行数")
    truncated: Scalar = Field(None, description="是否因 limit 而没有返回全部行")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="分组键（customer_code、customer_name；inv_code、inv_name、unit；逐单时 so_code、so_date、customer_name、"
        "inv_code、inv_name、unit、due_date）、lines、open_qty、open_amount、overdue_lines、earliest_due。"
        "金额为数字（本币，两位小数），数量为数字（六位小数）",
    )
    totals: dict[str, Any] | None = Field(None, description="全部符合条件的行的合计：lines、open_amount、customers")


class SalesPriceHistoryOut(_Page):
    date_from: Scalar = Field(None, description="起始日期")
    date_to: Scalar = Field(None, description="截止日期")
    count: Scalar = Field(None, description="返回的行数")
    truncated: Scalar = Field(None, description="是否因 limit 而没有返回全部行")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="新的在前：date、code、customer_code、customer_name、inv_code、inv_name、unit、qty、amount、amount_tax、"
        "unit_price、unit_price_tax、currency。金额为数字（本币，两位小数），单价为数字（六位小数）",
    )


_CHURN_ROW = (
    "按收入从大到小：customer_code、customer_name、person_name、first_date、last_date、revenue_current、"
    "revenue_baseline、months_current、months_baseline"
)


class CustomerChurnOut(_Page):
    lookback_months: Scalar = Field(None, description="实际使用的基线月数")
    decline_pct: Scalar = Field(None, description="实际使用的下降阈值")
    new: list[dict[str, Any]] | None = Field(None, description="新客户。" + _CHURN_ROW)
    lost: list[dict[str, Any]] | None = Field(None, description="流失客户。" + _CHURN_ROW)
    declining: list[dict[str, Any]] | None = Field(None, description="收入下降的客户（按月均下降额排序）。" + _CHURN_ROW)
    counts: dict[str, Any] | None = Field(None, description="各名单的全部个数：new、lost、declining，和当前窗口有发票的客户数 active")
    windows: dict[str, Any] | None = Field(None, description="实际使用的窗口：current_from、current_to、baseline_from、baseline_to")
