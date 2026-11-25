"""质量统计报表只读用例：检验指标统计 qm_indicators、检验合格率统计 qm_pass_rate。不改库。

每张报表一个用例，一步 call：成功（ok）并带着应有的键（kind、日期、items 是列表、count、truncated；
合格率另有 group_by 和 totals）。账套中没有检验数据时 items 为空，仍然通过。
起始日期取很早的 2000-01-01，免得因为账套日期不同而查不到数据；来料检验单量小，不会撞上桥的明细行上限。
"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec, call

_FROM = "2000-01-01"
_INDICATORS = (
    "/v1/reports/qm_indicators",
    "report-qm-indicators",
    "报表：检验指标统计（来料）",
)
_PASS_RATE = (
    "/v1/reports/qm_pass_rate",
    "report-qm-pass-rate",
    "报表：检验合格率统计（产品，按月）",
)


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


def rpt_qm_cases() -> list[CaseSpec]:
    indicators = _case(
        *_INDICATORS,
        (("kind", "incoming"), ("date_from", _FROM), ("limit", "5")),
        (
            ("ok", "true"),
            ("kind", "incoming"),
            ("date_from", _FROM),
            ("include_unverified", "false"),
            ("items#len", "$nonempty"),
            ("count", "$nonempty"),
            ("truncated", "$nonempty"),
        ),
    )
    pass_rate = _case(
        *_PASS_RATE,
        (("kind", "product"), ("date_from", _FROM), ("group_by", "month")),
        (
            ("ok", "true"),
            ("kind", "product"),
            ("date_from", _FROM),
            ("group_by", "month"),
            ("items#len", "$nonempty"),
            ("truncated", "$nonempty"),
            ("totals.docs", "$nonempty"),
            ("totals.fail_docs", "$nonempty"),
            ("totals.undecided_docs", "$nonempty"),
            ("totals.qty", "$nonempty"),
        ),
    )
    return [indicators, pass_rate]
