"""采购类报表只读用例：采购综合统计 purchase_analysis、采购到货及时率 arrival_otif。不改库。

每张报表一个用例，一步 call：成功（ok）并带着应有的键（回显的日期和分组、items 或 groups 是列表、合计）。
账套中没有采购数据时列表为空，仍然通过。起始日期取很早的 2000-01-01，免得因为账套日期不同而查不到数据。
"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec, call

_FROM = "2000-01-01"
_PURCHASE = "/v1/reports/purchase_analysis"
_ARRIVAL = "/v1/reports/arrival_otif"


def _case(route: str, name: str, title: str, fields: tuple, checks: tuple) -> CaseSpec:
    return CaseSpec(
        name,
        title,
        (),
        (call(route, fields, checks),),
        True,
        tags=frozenset(("wave:2", "rpt:trace")),
        routes=(route,),
    )


def rpt_pu_cases() -> list[CaseSpec]:
    purchase = _case(
        _PURCHASE,
        "report-purchase-analysis",
        "报表：采购综合统计（发票，按物料）",
        (("date_from", _FROM), ("limit", "5")),
        (
            ("ok", "true"),
            ("source", "invoice"),
            ("date_from", _FROM),
            ("date_to", "$nonempty"),
            ("group_by#len", "1"),
            ("items#len", "$nonempty"),
            ("count", "$nonempty"),
            ("truncated", "$nonempty"),
            ("totals.amount", "$nonempty"),
            ("totals.docs", "$nonempty"),
        ),
    )
    arrival = _case(
        _ARRIVAL,
        "report-arrival-otif",
        "报表：采购到货及时率（按供应商）",
        (("date_from", _FROM), ("overdue_limit", "5")),
        (
            ("ok", "true"),
            ("as_of", "$nonempty"),
            ("date_from", _FROM),
            ("group_by", "vendor"),
            ("arrival_basis", "arrival_or_receipt"),
            ("groups#len", "$nonempty"),
            ("truncated", "$nonempty"),
            ("totals.lines", "$nonempty"),
            ("totals.open_overdue", "$nonempty"),
            ("overdue#len", "$nonempty"),
            ("overdue_total", "$nonempty"),
        ),
    )
    return [purchase, arrival]
