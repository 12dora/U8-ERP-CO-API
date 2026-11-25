"""账务核对报表只读用例：月末结账检查 close_check、存货核算检查 ia_anomalies、完工产品单位成本 cost_unit、
成本核算期间数据 ca_inputs。不改库。

每张报表一个用例，一步 call，缺省参数调用一次：成功（ok）并带着应有的键。
测试账套里没有对应数据（没有成本核算数据、没有未审核单据）时清单为空，仍然通过——只核对响应形状。
"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec, call

_CLOSE = "/v1/reports/close_check"
_IA = "/v1/reports/ia_anomalies"
_COST = "/v1/reports/cost_unit"
_CA_INPUTS = "/v1/reports/ca_inputs"


def _case(route: str, name: str, title: str, fields: tuple, checks: tuple) -> CaseSpec:
    return CaseSpec(
        name,
        title,
        (),
        (call(route, fields, checks),),
        True,
        tags=frozenset(("wave:2", "rpt:close", "rpt:gl", "rpt:stock")),
        routes=(route,),
    )


def _close_case() -> CaseSpec:
    return _case(
        _CLOSE,
        "report-close-check",
        "报表：月末结账检查（缺省为登录月份）",
        (),
        (
            ("ok", "true"),
            ("fiscal_year", "$nonempty"),
            ("period", "$nonempty"),
            ("closed.SA", "$nonempty"),
            ("closed.GL", "$nonempty"),
            ("closed.FA", "$nonempty"),
            ("checks#len", "$nonempty"),
        ),
    )


def _ia_case() -> CaseSpec:
    return _case(
        _IA,
        "report-ia-anomalies",
        "报表：存货核算检查（暂估未回冲、未记账单据）",
        (("limit", "5"),),
        (
            ("ok", "true"),
            ("fiscal_year", "$nonempty"),
            ("period", "$nonempty"),
            ("period_end", "$nonempty"),
            ("billing_as_of", "$nonempty"),
            ("unbilled.count", "$nonempty"),
            ("unbilled.est_amount", "$nonempty"),
            ("unbilled.items#len", "$nonempty"),
            ("unbooked.count", "$nonempty"),
            ("unbooked.items#len", "$nonempty"),
        ),
    )


def _cost_case() -> CaseSpec:
    return _case(
        _COST,
        "report-cost-unit",
        "报表：完工产品单位成本（缺省为最近已结账期间）",
        (),
        (
            ("ok", "true"),
            ("fiscal_year", "$nonempty"),
            ("items#len", "$nonempty"),
            ("closed_periods#len", "$nonempty"),
        ),
    )


def _ca_inputs_case() -> CaseSpec:
    return _case(
        _CA_INPUTS,
        "report-ca-inputs",
        "报表：成本核算期间数据（2026 年第 6 期，成本计算到该期间；每张表最多 5 行）",
        (("fiscal_year", "2026"), ("period", "6"), ("limit", "5")),
        (
            ("ok", "true"),
            ("fiscal_year", "$nonempty"),
            ("period", "$nonempty"),
            ("status.calculated_through", "$nonempty"),
            ("status.closed", "$nonempty"),
            ("skipped#len", "0"),
            ("tables.work_hours.rows#len", "$gt0"),
            ("tables.labor.rows.0.cost_center_name", "$nonempty"),
            ("tables.overhead.rows.0.item_name", "$nonempty"),
            ("tables.product_qty.rows.0.inv_code", "$nonempty"),
            ("tables.product_qty.rows.0.mo_code", "$nonempty"),
            ("tables.product_qty.rows.0.values.iQua", "$gt0"),
        ),
    )


def rpt_acct_cases() -> list[CaseSpec]:
    return [_close_case(), _ia_case(), _cost_case(), _ca_inputs_case()]
