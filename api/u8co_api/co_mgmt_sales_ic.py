"""mgmt/sales 的关联方标注：行上的 ic_account 和每个账套的 ic_totals。

关联方 = 公司间对照（U8CO_IC_MAP_FILE，IcGroup）里本账套的客户编码所代表的公司组内其他账套（as_customer），
与本次选了哪些账套无关：只查账套 B 的销售时，B 里代表账套 A 的客户照样标出。每个账套各自找自己所在的组，
所以 consolidate=false 时跨组的多个账套也各自标注。

ic_totals 的取法：把这些客户编码（最多 20 个）放进桥请求的 ic_customers，由桥多跑一遍只含这些客户、不分组的合计
（同一套期间、复核、分类过滤和数据权限），不受 top 影响，所以关联方客户落在 top 之外也算得准。
没有走「按客户 top 500 再在这里相加」的办法：500 组以外的关联方客户会漏掉，漏了又看不出来。
桥的这一遍要再扫一次发票和核算明细，所以只在 group_by 含 customer 时才要（ic_totals 是客户视角的数）；
其他分组 ic_totals 为 null。被字段权限遮蔽的度量保持 null，不当 0。
账套不在任何对照组里、对照里没有登记其他公司的客户编码、或桥太旧不认识 ic_customers 时 ic_totals 为 null。
"""

from __future__ import annotations

from typing import Any

from u8co_api.co_ic_core import IcGroup
from u8co_api.co_mgmt_core import MgmtRun
from u8co_api.errors import ApiError

# 与桥的 ic_customers 上限一致。
IC_CUSTOMERS_MAX = 20
IC_MEASURES = ("qty", "revenue", "revenue_tax_incl", "cost_qty", "cogs")


def group_of_acc(run: MgmtRun, acc: str) -> IcGroup | None:
    """账套 acc 所在的公司组；没有配置对照或不在任何组里时为 None。"""
    if run.group is not None and acc in run.group.accounts:
        return run.group
    found = getattr(run.settings(), "ic_map", None)
    if found is None:
        return None
    try:
        return found.group_of({acc})
    except ApiError:
        return None


def group_partners(run: MgmtRun, acc: str) -> dict[str, str]:
    """账套 acc 里代表公司组内其他账套的客户：客户编码 → 公司账套。没有对照组时为空。"""
    group = group_of_acc(run, acc)
    if group is None:
        return {}
    return {code: company for company, code in group.as_customer.get(acc, {}).items() if company != acc}


def plan_partners(run: MgmtRun, accs: set[str] | list[str]) -> tuple[dict[str, dict[str, str]], list[str]]:
    """各账套的关联方客户，以及超过桥上限（只能送前 20 个）的提醒。"""
    partners = {acc: group_partners(run, acc) for acc in accs}
    warnings = [
        f"账套 {acc} 的关联方客户超过 {IC_CUSTOMERS_MAX} 个，ic_totals 只含前 {IC_CUSTOMERS_MAX} 个"
        for acc, found in partners.items()
        if len(found) > IC_CUSTOMERS_MAX
    ]
    return partners, warnings


def ic_params(params: dict[str, Any], partners: dict[str, str]) -> dict[str, Any]:
    """桥请求参数：按客户分组且有关联方客户时加 ic_customers（前 20 个）。"""
    if partners and "customer" in params.get("group_by", ()):
        return {**params, "ic_customers": list(partners)[:IC_CUSTOMERS_MAX]}
    return dict(params)


def mark_rows(rows: list[dict], partners: dict[str, str]) -> None:
    """按客户分组的行（有 customer_code）加 ic_account：关联方的公司账套，不是则 None。就地修改。"""
    for row in rows:
        if "customer_code" in row:
            row["ic_account"] = partners.get(row["customer_code"]) if isinstance(row["customer_code"], str) else None


def ic_totals_of(found: dict) -> dict[str, Any] | None:
    """桥返回的 ic_totals 补齐五个度量（被遮蔽的为 None）；桥没给（没有请求）时为 None。"""
    raw = found.get("ic_totals")
    if not isinstance(raw, dict):
        return None
    return {key: raw.get(key) for key in IC_MEASURES}
