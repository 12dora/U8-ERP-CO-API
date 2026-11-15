"""固定资产只读报表 /v1/co/reports/fa_changes（变动单）、fa_depreciation（折旧）的请求和响应。

字段名与桥一致，响应放行桥多给的字段。金额是数字（本币，两位小数）；after 是桥给的不透明游标，原样传回。
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, StrictBool, StrictInt

from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import PASS, Scalar
from u8co_api.co_models_reports import _AFTER, _NEXT, After

_FISCAL = (
    "会计年度，2000 到 2099，指固定资产业务年度（折旧年度、变动日期的年份），不是账套库年份 year。"
    "缺省为登录日期（date）的年份"
)
_LIMIT = "每页条数，1 到 1000，缺省 200"
# 与桥一致：卡片编号、变动单号只收字母、数字、点和减号。
CardCode = Annotated[str, Field(pattern=r"^[0-9A-Za-z.\-]{1,20}$")]
ChangeCode = Annotated[str, Field(pattern=r"^[0-9A-Za-z.\-]{1,10}$")]


class _Fa(CoAuth):
    fiscal_year: StrictInt | None = Field(None, ge=2000, le=2099, description=_FISCAL)
    period: StrictInt | None = Field(None, ge=1, le=12, description="会计期间 1 到 12，缺省全年")
    card: CardCode | None = Field(None, description="卡片编号（fa_card 的 code）")
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description=_LIMIT)


class ReportFaChangesIn(_Fa):
    code: ChangeCode | None = Field(None, description="变动单号")
    change_type: StrictInt | None = Field(
        None,
        ge=1,
        le=99,
        description="变动单类型（U8 的 iVoucherType，如 1 原值增加、11 计提减值准备），名称见响应的 change_name",
    )


class ReportFaDepreciationIn(_Fa):
    nonzero: StrictBool | None = Field(None, description="为 true 时去掉当月折旧为 0 的行。缺省 false")


class _Page(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    fiscal_year: Scalar = Field(None, description="会计年度")
    next: Scalar = Field(None, description=_NEXT)


class ReportFaChangesOut(_Page):
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按变动单号排序：code（变动单号）、card_code、asset_name（变动后版本的资产名称）、opt_id（变动后卡片版本）、"
        "pre_opt_id（变动前版本）、change_type、change_name、before_value、after_value、reason、change_date、period、"
        "operator、currency、exchange_rate、site_after、keeper_after、effective（当期生效）、gl_sign、gl_num，"
        "depts（dept_code、dept_name、before_value、after_value）",
    )


class ReportFaDepreciationOut(_Page):
    posted_periods: list[Any] | None = Field(None, description="该年度已计提折旧的期间；没出现在这里的期间没有折旧行")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按卡片编号、期间排序，只含已计提期间：card_code、asset_num、asset_name、period、depr_date、"
        "amount（当月折旧）、accumulated（当月末累计折旧）、rate（月折旧率）、month_value（月初原值）、depr_months、"
        "used_months",
    )
