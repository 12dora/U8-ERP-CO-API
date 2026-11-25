"""库龄分析只读用例（`report-stock-age`）：缺省参数调用一次，核对 ok、登录日期、分段和合计都在。不改库。

账套里没有现存量时 items 为空也算通过：只核对响应形状（分段数 = 缺省 3 个上界 + 1，合计带 items 个数）。
"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec, call

_ROUTE = "/v1/reports/stock_age"


def rpt_stock_age_cases() -> list[CaseSpec]:
    step = call(
        _ROUTE,
        (("limit", "5"),),
        (
            ("ok", "true"),
            ("as_of", "$nonempty"),
            ("items", "$nonempty"),
            ("buckets#len", "4"),
            ("total.items", "$nonempty"),
            ("idle_total.items", "$nonempty"),
        ),
    )
    return [
        CaseSpec(
            "report-stock-age",
            "报表：库龄分析",
            (),
            (step,),
            True,
            tags=frozenset(("wave:2", "rpt:stock")),
            routes=(_ROUTE,),
        )
    ]
