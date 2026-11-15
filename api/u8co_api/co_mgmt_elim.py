"""经营管理合并时的内部往来抵销（公司间对照的 ar_ap 抵销对），沿用合并报表（co_ic_consol）的取数和算法：
抵销对两边账套都在本次请求里时，读两边的辅助核算余额表（应收方按客户、应付方按供应商），
抵销额 = 应收、应付期末余额绝对值中较小的一个，方向相反时不抵销并提醒。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from u8co_api.co_ic_aggregate import plain
from u8co_api.co_ic_consol import AUX_PATH, _aux_sum, _eliminations, aux_key, needs_of, pairs_of
from u8co_api.co_ic_core import paged, run_jobs
from u8co_api.co_mgmt_core import MgmtRun
from u8co_api.co_models_ic import IcLogin

_AUX_PAGE, _AUX_PAGES = 1000, 5


def arap_eliminations(run: MgmtRun, period: dict[str, int]) -> tuple[list[dict[str, Any]], list[str]]:
    """period 是 {fiscal_year, period_from, period_to}（期末余额取 period_to）。返回（抵销列表, 提醒）。
    抵销列表每项：rule、ar、ap（acc、logical、code、partner、balance）、amount、difference、entries，不能抵销时有 skipped。
    没有合并（run.consolidating 为 false）或没有适用的抵销对时返回空列表。"""
    if not run.consolidating or run.group is None:
        return [], []
    group = run.group
    accs = set(run.ok_accs)
    pairs = pairs_of(group, accs)
    if not pairs:
        return [], []
    jobs = {}
    for login in run.body.logins:
        needs = needs_of(group, pairs, login.acc)
        if login.acc in accs and needs:
            jobs[login.acc] = _job(run.request, login, period, needs)
    results = run_jobs(run.request, jobs)
    warnings = [
        f"账套 {acc} 的往来辅助余额读取失败（{(result.error or {}).get('code')}），涉及该账套的往来未抵销"
        for acc, result in results.items()
        if not result.ok
    ]
    bodies = {acc: {"aux": {}} for acc in accs}
    bodies.update({acc: result.body or {} for acc, result in results.items() if result.ok})
    for acc, result in results.items():
        if not result.ok:
            bodies.pop(acc, None)
    elims, more = _eliminations(group, pairs, bodies)
    return plain(elims), warnings + more


def _job(request, login: IcLogin, period: dict[str, int], needs: list[tuple[str, str, str]]) -> Callable[[], dict]:
    def job() -> dict:
        aux: dict[str, Any] = {}
        for dim, code, partner in needs:
            body = dict(period, dim=dim, code_prefix=code, dim_code=partner)
            rows = paged(request, login, AUX_PATH, body, page_size=_AUX_PAGE, max_pages=_AUX_PAGES)
            aux[aux_key(dim, code, partner)] = _aux_sum(rows, partner)
        return {"aux": aux}

    return job
