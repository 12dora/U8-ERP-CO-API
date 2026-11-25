"""生产类报表只读用例：产成品入库日报 production_daily、生产订单完工状况 mo_progress、生产订单用料分析 mo_consumption。不改库。

每张报表一个用例，一步 call：成功（ok）并带着应有的键（items 是列表、count、truncated；日报另有 days）。
账套中没有生产数据时 items 为空，仍然通过。起始日期取很早的 2000-01-01，免得因为账套日期不同而查不到数据；
limit 取 5，只核对形状，不拉全量。
"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec, call

_FROM = "2000-01-01"
_DAILY = (
    "/v1/reports/production_daily",
    "report-production-daily",
    "报表：产成品入库日报（按日、按产品）",
)
_PROGRESS = ("/v1/reports/mo_progress", "report-mo-progress", "报表：生产订单完工状况")
_CONSUMPTION = (
    "/v1/reports/mo_consumption",
    "report-mo-consumption",
    "报表：生产订单用料分析",
)
_SHAPE = (
    ("ok", "true"),
    ("items#len", "$nonempty"),
    ("count", "$nonempty"),
    ("truncated", "$nonempty"),
)


def _case(route: str, name: str, title: str, fields: tuple, checks: tuple) -> CaseSpec:
    return CaseSpec(
        name,
        title,
        (),
        (call(route, fields, checks),),
        True,
        tags=frozenset(("wave:2", "rpt:stock", "rpt:trace")),
        routes=(route,),
    )


def rpt_mfg_cases() -> list[CaseSpec]:
    window = (("date_from", _FROM), ("limit", "5"))
    daily = _case(
        *_DAILY,
        window + (("group_by", "day"), ("by", "product")),
        _SHAPE + (("days", "$nonempty"),),
    )
    progress = _case(*_PROGRESS, window, _SHAPE)
    consumption = _case(*_CONSUMPTION, window, _SHAPE)
    return [daily, progress, consumption]
