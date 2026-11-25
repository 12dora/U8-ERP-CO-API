"""读路由性能测量：健康检查、模拟一轮事件轮询、模拟一次删除扫描。轮询的数据源和参数与事件服务一致（docs/events.md）。"""

from __future__ import annotations

from typing import Any

from co.client.u8co_arap_proc_list import ProcListQuery
from co.client.u8co_gl_arc import ArcQuery, VoucherQuery
from co.e2e.perf_reads_groups import (
    LIST_TYPES,
    PROC_FLAGS,
    PROC_LAG,
    R_PROC,
    TS_ARCHIVES,
    PAGE,
    PerfCtx,
    arc_page,
    archive_keys,
    gl_year,
    list_page,
    proc_digest,
    proc_increment,
    remember,
    voucher_keys,
)
from co.e2e.perf_reads_probe import params_text

PRIME_GROUP = "poll_prime"


def run_health(ctx: PerfCtx) -> None:
    ctx.probe.call("health", "", ctx.client.health)


def _prime_proc(ctx: PerfCtx, flag: str) -> None:
    query = ProcListQuery(flag, changed_since="0", limit=1, keys_only=True)
    label = params_text(flag=flag, changed_since="0", limit=1, keys_only=True)
    remember(ctx, "proc:" + flag, ctx.probe.call(R_PROC, label, lambda: ctx.client.arap_process_list(ctx.call, query)))


def prime(ctx: PerfCtx) -> None:
    """没跑 lists、archives、process 组时，先各取一页拿到水位；记在 poll_prime 组，不计入轮询耗时。"""
    group = ctx.probe.group
    ctx.probe.group = PRIME_GROUP
    try:
        for kind in LIST_TYPES:
            if "list:" + kind not in ctx.marks:
                remember(ctx, "list:" + kind, list_page(ctx, VoucherQuery(kind, keys_only=True, limit=1)))
        for archive in TS_ARCHIVES:
            if "arc:" + archive not in ctx.marks:
                remember(ctx, "arc:" + archive, arc_page(ctx, ArcQuery(archive, keys_only=True, limit=1)))
        for flag in PROC_FLAGS:
            if "proc:" + flag not in ctx.marks:
                _prime_proc(ctx, flag)
    finally:
        ctx.probe.group = group


def poll_round(ctx: PerfCtx) -> list[str]:
    """一轮空闲轮询：每类单据、每类档案一次 changed_since，处理记录增量和期间摘要，总账摘要。返回没有水位而跳过的项。"""
    skipped: list[str] = []
    for kind in LIST_TYPES:
        mark = ctx.marks.get("list:" + kind)
        if mark:
            list_page(ctx, VoucherQuery(kind, changed_since=mark, limit=PAGE))
        else:
            skipped.append("list:" + kind)
    for archive in TS_ARCHIVES:
        mark = ctx.marks.get("arc:" + archive)
        if mark:
            arc_page(ctx, ArcQuery(archive, changed_since=mark, limit=PAGE))
        else:
            skipped.append("arc:" + archive)
    for flag in PROC_FLAGS:
        mark = ctx.marks.get("proc:" + flag)
        if mark:
            proc_increment(ctx, flag, str(max(0, int(mark) - PROC_LAG)))
        else:
            skipped.append("proc:" + flag)
    for flag in PROC_FLAGS:
        proc_digest(ctx, flag)
    gl_year(ctx, ctx.year)
    return skipped


def _ms(ctx: PerfCtx, start: float) -> float:
    return round((ctx.probe.clock() - start) * 1000.0, 1)


def run_poll(ctx: PerfCtx) -> None:
    prime(ctx)
    rounds: list[float] = []
    skipped: list[str] = []
    for _ in range(max(1, ctx.repeat)):
        start = ctx.probe.clock()
        skipped = poll_round(ctx)
        rounds.append(_ms(ctx, start))
    ctx.notes["poll"] = {"rounds_ms": rounds, "skipped": skipped}


def _key_count(pages: list[dict]) -> int:
    total = 0
    for page in pages:
        items: Any = page.get("items")
        total += len(items) if isinstance(items, list) else 0
    return total


def run_deletescan(ctx: PerfCtx) -> None:
    start = ctx.probe.clock()
    keys = 0
    for kind in LIST_TYPES:
        keys += _key_count(voucher_keys(ctx, kind))
    for archive in TS_ARCHIVES:
        keys += _key_count(archive_keys(ctx, archive))
    ctx.notes["deletescan"] = {"wall_ms": _ms(ctx, start), "keys": keys}
