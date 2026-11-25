"""造数的最后一步（close_prior_months）：把造数日期之前的月份逐月结账，让本月的总账凭证能记账。

桥的记账闸门只许记本年第一个未结账期间（记 1 月时上年度也要结完）；新建的空账套从起始年度起各月都没结账，
本月的凭证就记不了账。这一步经桥的 periods/close（action=close、through=true）把各已启用模块从起始年度起、
到造数日期的上个月为止的未结账期间按 U8 的顺序结账（采购、销售 → 库存 → 存货核算 → 应收、应付 → 总账）。
排在全部单据之后：造的单据都在本月，本月保持未结账；结账前各月必须没有单据（库存、存货核算只支持空月份）。

可选：periods/close 只对桥 config.json 的 testAccounts 里的账套开放；不在名单里（403 test_account_only）或桥总开关
enableReplicatedWrites 未打开（403 feature_disabled）时这一步跳过、不算失败。桥拒绝结账（409，任何错误码；例如重用的通道里前几次造数的单据、未记账凭证落在了已过去的月份）
时也只打印原因、跳过，不让整次造数失败。也可以用 --skip close_prior_months 跳过。
幂等：先按 SQL 看总账（bflag）到上个月为止还有没有未结账期间，没有就跳过。结账后再按 SQL 回读确认。
SQL 里没有拼进任何调用方的值。
"""

from __future__ import annotations

from co.e2e.casespec.tier2 import TIER2_REFUSALS
from co.e2e.seed.base import Probe, Step, fail_text
from co.e2e.seed.context import SeedContext, SeedError

NAME = "close_prior_months"
ROUTE = "v1/periods/close"

# 每行：iyear、iperiod、gl（总账结账标志 0 / 1，空值按 0）。只取第 1 到 12 期。
STATE_SQL = (
    "SELECT m.iyear AS iyear, m.iperiod AS iperiod, CONVERT(int, ISNULL(m.bflag, 0)) AS gl"
    " FROM GL_mend m WHERE m.iperiod BETWEEN 1 AND 12 ORDER BY m.iyear, m.iperiod"
)


def target(day: str) -> tuple[int, int]:
    """造数日期的上个月（年度, 期间）；1 月时是上年 12 月。"""
    year, month = int(day[:4]), int(day[5:7])
    return (year - 1, 12) if month == 1 else (year, month - 1)


def open_before(ctx: SeedContext, upto: tuple[int, int]) -> list[str]:
    """到 upto 为止总账未结账的期间，形如 2026-09，按时间先后。"""
    found = []
    for row in ctx.rows(STATE_SQL):
        year, period, flag = row.get("iyear", "").strip(), row.get("iperiod", "").strip(), row.get("gl", "").strip()
        if not (year.isdigit() and period.isdigit()) or flag == "1":
            continue
        if (int(year), int(period)) <= upto:
            found.append(f"{int(year):04d}-{int(period):02d}")
    return found


def probe(ctx: SeedContext) -> Probe:
    upto = target(ctx.day)
    missing = open_before(ctx, upto)
    if missing:
        return Probe((), tuple(NAME + ":" + item for item in missing))
    return Probe((NAME + f":{upto[0]:04d}-{upto[1]:02d}",), ())


def apply(ctx: SeedContext) -> tuple[int, int]:
    upto = target(ctx.day)
    if not open_before(ctx, upto):
        return 0, 1
    body = {"action": "close", "through": True, "fiscal_year": upto[0], "period": upto[1]}
    response = ctx.call(ROUTE, body)
    if response.get("status") == 403 and response.get("code") in TIER2_REFUSALS:
        print(f"[步骤] {NAME}：桥没开第二级写入或账套不在 testAccounts 里（{response.get('code')}），跳过（之前月份的结账在 U8 里做）")
        return 0, 0
    if response.get("status") == 409:
        print(f"[步骤] {NAME}：桥拒绝逐月结账，跳过（之前月份的结账在 U8 里做）：{fail_text(response)}")
        return 0, 0
    if response.get("ok") is not True:
        raise SeedError("逐月结账失败：" + fail_text(response), response, ctx.last_call)
    left = open_before(ctx, upto)
    if left:
        raise SeedError("结账后回读仍有总账未结账的期间：" + "、".join(left[:6]), response, ctx.last_call)
    count = response.get("count")
    return (count if isinstance(count, int) and count > 0 else 1), 0


STEPS = (Step(NAME, "逐月结账到上个月（全部单据之后，可选）", probe, apply),)
