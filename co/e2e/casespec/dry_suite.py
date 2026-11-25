"""接线：用例（dry_suite_cases，进 catalog）、自定义步骤（DRY_SUITE_OPS，进 ops_run）、补偿（DRY_SUITE_CLEANUP，进 runner）。

正文构造器 DRY_SUITE_BUILDERS（dry_bodies）和应付自动核销的挑单 DRY_AP_PACKS（dry_sql）经 wiring 汇总模块接线。
用例说明见各模块：dry_sale / dry_stock / dry_more / dry_gaps（写预演）、ap_writeoff_voucher（应付一侧）、
resolve（名称解析）、idem_get（幂等结果查询）。字段标签、单据搜索、批量读取、全部写路由的幂等键见 meta_batch。
"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.ap_writeoff_voucher import ap_cases
from co.e2e.casespec.ap_writeoff_voucher_run import DRY_AP_OPS
from co.e2e.casespec.dry_cleanup import cleanups
from co.e2e.casespec.dry_gaps import dry_gap_cases
from co.e2e.casespec.dry_more import dry_more_cases
from co.e2e.casespec.dry_sale import dry_sale_cases
from co.e2e.casespec.dry_stock import dry_stock_cases
from co.e2e.casespec.dry_fin_run import DRY_FIN_OPS
from co.e2e.casespec.idem_get import DRY_IDEM_OPS, idem_get_case
from co.e2e.casespec.resolve import DRY_RESOLVE_OPS, resolve_case
from co.e2e.casespec.dry_ops import DRY_RUN_OPS
from co.e2e.casespec.meta_batch import MB_CLEANUP, MB_OPS, mb_cases

__all__ = ["DRY_SUITE_CLEANUP", "DRY_SUITE_OPS", "dry_suite_cases"]

DRY_SUITE_OPS = {**DRY_RUN_OPS, **DRY_FIN_OPS, **DRY_AP_OPS, **DRY_RESOLVE_OPS, **DRY_IDEM_OPS}
DRY_SUITE_OPS.update(MB_OPS)


def dry_suite_cases() -> list[CaseSpec]:
    rows = dry_sale_cases() + dry_stock_cases() + dry_more_cases()
    rows += dry_gap_cases()  # 预演补缺
    return rows + ap_cases() + [resolve_case(), idem_get_case()] + mb_cases()


DRY_SUITE_CLEANUP = cleanups([spec.name for spec in dry_suite_cases()])
DRY_SUITE_CLEANUP.update(MB_CLEANUP)  # 幂等键用例：失败时尽力撤回真建的销售订单、凭证和提交
