"""期初余额报表 /v1/co/reports/opening_balance 的请求和响应。字段名与桥一致，响应放行桥多给的字段。

数量是数字（六位小数），金额是数字（本币，两位小数）。after 是桥给的不透明游标，原样传回。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import PASS, Scalar
from u8co_api.co_models_reports import _AFTER, _NEXT, After, CodePrefix, Dim, Side, Word

# 各 module 能带的字段（module、nonzero、after、limit 之外）。与桥 ReportsOpeningReq 一致。
_OWN = {
    "stock": ("wh", "inv", "batch"),
    "arap": ("side", "partner", "code_prefix"),
    "gl": ("fiscal_year", "code_prefix", "leaf_only", "dim"),
}


class ReportOpeningIn(CoAuth):
    module: Literal["stock", "arap", "gl"] = Field(
        ...,
        description="stock 库存期初（按仓库、存货、批号），arap 应收或应付期初（按往来单位、科目），gl 总账期初余额（按科目或辅助项）",
    )
    side: Side | None = Field(None, description="module=arap 时必填：ar 应收，ap 应付")
    fiscal_year: StrictInt | None = Field(
        None,
        ge=2000,
        le=2099,
        description="只用于 module=gl：会计年度，缺省为登录日期（date）的年份，不能早于总账启用年度",
    )
    wh: Word | None = Field(None, description="module=stock：仓库编码等于")
    inv: Word | None = Field(None, description="module=stock：存货编码等于")
    batch: Word | None = Field(None, description="module=stock：批号等于")
    partner: Word | None = Field(None, description="module=arap：客户或供应商编码等于")
    code_prefix: CodePrefix | None = Field(None, description="module=arap 或 gl：科目编码前缀")
    leaf_only: StrictBool | None = Field(None, description="module=gl：为 true 时只要末级科目")
    dim: Dim | None = Field(
        None,
        description="module=gl：按辅助项列期初（customer、vendor、dept、person、project）。缺省按科目",
    )
    nonzero: StrictBool | None = Field(None, description="缺省 true：去掉期初为 0 的行")
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description="每页条数，1 到 1000，缺省 200")

    @model_validator(mode="after")
    def _fields(self) -> ReportOpeningIn:
        own = _OWN[self.module]
        for module, fields in _OWN.items():
            for name in fields:
                if module != self.module and name not in own and getattr(self, name) is not None:
                    raise ValueError(f"{name} 不能和 module={self.module} 一起用")
        if self.module == "arap" and self.side is None:
            raise ValueError("module=arap 时 side 必填")
        return self

    def audit_ref(self) -> str:
        return self.module if self.side is None else f"{self.module}:{self.side}"


class ReportOpeningOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    module: Scalar = Field(None, description="stock、arap 或 gl")
    side: Scalar = Field(None, description="module=arap 时的 ar / ap，其余为 null")
    start_date: Scalar = Field(None, description="该模块的启用日期 yyyy-MM-dd；模块没有启用时为 null")
    opening_year: Scalar = Field(None, description="期初所在年度：库存、往来是启用年度，总账是 fiscal_year")
    posted: Scalar = Field(
        None,
        description="期初是否已记账（GL_mend 第 0 期的该模块标志）。已记账的期初在 U8 里不能再改",
    )
    fiscal_year: Scalar = Field(None, description="module=gl：会计年度")
    period: Scalar = Field(None, description="module=gl：取期初的期间（启用年度为启用月份，其余为 1）")
    dim: Scalar = Field(None, description="module=gl：辅助项维度，按科目时为 null")
    trial: dict[str, Any] | None = Field(
        None,
        description="module=gl 按科目的第一页：末级科目期初试算 debit、credit、difference、balanced；其余情况为 null",
    )
    next: Scalar = Field(None, description=_NEXT)
    items: list[dict[str, Any]] | None = Field(
        None,
        description="stock：按仓库、存货、批号排序：wh_code、wh_name、inv_code、inv_name、inv_std、batch、qty、qty_aux、"
        "amount、lines、unverified_lines。arap：按往来单位、科目排序：partner_code、partner_name、account、account_name、"
        "debit、credit、balance_dir、balance_debit、balance_credit、docs、unverified_docs。gl 按科目：code、name、grade、"
        "leaf、natural_dir、open_dir、open_debit、open_credit、pre_debit、pre_credit（启用前累计）；gl 按辅助项：code、name、"
        "dim_code、dim_name、project_class、open_dir、open_debit、open_credit",
    )
