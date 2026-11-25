"""读路由性能测量的各组调用。只读：列表、档案、搜索、账龄、总账摘要、往来处理记录，以及模拟的一轮事件轮询和删除扫描。

参数取自事件服务的实际用法（docs/events.md）和常见的逾期提醒查询。全部顺序执行，测的是单个调用方的延迟。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from co.client.u8co_arap_proc_list import ProcDigestQuery, ProcListQuery
from co.client.u8co_client import U8Call
from co.client.u8co_gl_digest import GlDigestQuery
from co.client.u8co_kinds import KIND_NAMES, NOTE_KINDS
from co.client.u8co_gl_arc import READ_ARCHIVES, ArcQuery, VoucherQuery
from co.client.u8co_lookup import SearchQuery
from co.client.u8co_reports import ArapQuery
from co.e2e.perf_reads_probe import Probe, params_text, walk

PAGE = 500
LIST_TYPES = tuple(KIND_NAMES) + tuple(NOTE_KINDS)
# 没有 rowversion 的档案：带 changed_since 时桥 400，事件服务按删除扫描的节奏整表比对（与 events 的 PLAIN_ARCHIVES 一致）。
NO_TS_ARCHIVES = (
    "voucher_sign", "project", "customer_address", "customer_invoice_company", "customer_bank", "vendor_bank",
    "fa_card", "operator", "role",
)
# 有 rowversion 的档案：事件服务缺省轮询的就是这些（含 position）。
TS_ARCHIVES = tuple(name for name in READ_ARCHIVES if name not in NO_TS_ARCHIVES)
SEARCH_TYPES = ("sale_order", "purchase_in")
SEARCH_LIMIT = 200
AGING_BUCKETS = (30, 60, 90, 180)
AGING_LIMIT = 1000
CREDIT_DAYS = 60
GL_BASE_YEAR = 2024
PROC_FLAGS = ("AR", "AP")
# 事件服务处理记录增量的回退量（auto_id_lag 缺省值）。
PROC_LAG = 500

R_LIST = "vouchers/list"
R_ARC = "archives/list"
R_SEARCH = "vouchers/search"
R_AGING = "reports/arap_aging"
R_GL = "gl/vouchers/digest"
R_PROC = "arap/process/list"


@dataclass
class PerfCtx:
    probe: Probe
    call: U8Call
    today: str
    repeat: int = 1
    marks: dict[str, Any] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def client(self) -> Any:
        return self.probe.client

    @property
    def year(self) -> int:
        return int(self.today[:4])


# ---- 单据列表 ----


def list_page(ctx: PerfCtx, query: VoucherQuery) -> dict | None:
    label = params_text(type=query.kind, limit=query.limit, keys_only=query.keys_only,
                        changed_since=query.changed_since, after=query.after)
    return ctx.probe.call(R_LIST, label, lambda: ctx.client.list_vouchers(ctx.call, query))


def voucher_keys(ctx: PerfCtx, kind: str) -> list[dict]:
    return walk(
        ctx.probe, R_LIST,
        lambda after: params_text(type=kind, keys_only=True, limit=PAGE, after=after),
        lambda after: ctx.client.list_vouchers(ctx.call, VoucherQuery(kind, keys_only=True, after=after, limit=PAGE)),
    )


def remember(ctx: PerfCtx, key: str, payload: dict | None) -> str:
    mark = payload.get("watermark") if isinstance(payload, dict) else None
    if isinstance(mark, (str, int)) and str(mark).isdigit():
        ctx.marks[key] = str(mark)
    return ctx.marks.get(key, "")


def run_lists(ctx: PerfCtx) -> None:
    for kind in LIST_TYPES:
        mark = remember(ctx, "list:" + kind, list_page(ctx, VoucherQuery(kind, limit=PAGE)))
        voucher_keys(ctx, kind)
        if mark:
            list_page(ctx, VoucherQuery(kind, changed_since=mark, limit=PAGE))


# ---- 基础档案 ----


def arc_page(ctx: PerfCtx, query: ArcQuery) -> dict | None:
    label = params_text(archive=query.archive, limit=query.limit, keys_only=query.keys_only,
                        changed_since=query.changed_since, after=query.after)
    return ctx.probe.call(R_ARC, label, lambda: ctx.client.arc_list(ctx.call, query))


def archive_keys(ctx: PerfCtx, archive: str) -> list[dict]:
    return walk(
        ctx.probe, R_ARC,
        lambda after: params_text(archive=archive, keys_only=True, limit=PAGE, after=after),
        lambda after: ctx.client.arc_list(ctx.call, ArcQuery(archive, keys_only=True, after=after or "", limit=PAGE)),
    )


def run_archives(ctx: PerfCtx) -> None:
    for archive in READ_ARCHIVES:
        mark = remember(ctx, "arc:" + archive, arc_page(ctx, ArcQuery(archive, limit=PAGE)))
        archive_keys(ctx, archive)
        if mark and archive in TS_ARCHIVES:
            arc_page(ctx, ArcQuery(archive, changed_since=mark, limit=PAGE))


# ---- 搜索、账龄 ----


def run_search(ctx: PerfCtx) -> None:
    for kind in SEARCH_TYPES:
        query = SearchQuery(kind, limit=SEARCH_LIMIT)
        ctx.probe.call(R_SEARCH, params_text(type=kind, limit=SEARCH_LIMIT), lambda: ctx.client.search(ctx.call, query))


def aging_query(side: str, today: str) -> ArapQuery:
    # 常见的逾期提醒查询：按到期日、按往来单位加业务员分组、只要逾期。
    return ArapQuery(
        side=side, as_of=today, limit=AGING_LIMIT, basis="due", buckets=AGING_BUCKETS,
        group_by="partner_person", overdue_only=True, default_credit_days=CREDIT_DAYS,
    )


def run_aging(ctx: PerfCtx) -> None:
    for side in ("ar", "ap"):
        query = aging_query(side, ctx.today)
        label = params_text(side=side, basis="due", group_by="partner_person", overdue_only=True,
                            buckets="30/60/90/180", limit=AGING_LIMIT, default_credit_days=CREDIT_DAYS)
        ctx.probe.call(R_AGING, label, lambda: ctx.client.report_arap_aging(ctx.call, query))


# ---- 总账凭证摘要 ----


def _int_periods(value: object) -> list[int] | None:
    if isinstance(value, list) and value and all(type(item) is int for item in value):
        return value
    return None


def gl_year(ctx: PerfCtx, year: int) -> list[dict]:
    """一个年度的摘要：首页按未结账期间加最近 1 个已结账期间，翻页时带首页返回的 periods。"""
    scope: dict[str, Any] = {}

    def page(after: Any) -> dict:
        if after is None:
            query = GlDigestQuery(fiscal_year=year, closed_periods=1, limit=PAGE)
        else:
            query = GlDigestQuery(fiscal_year=year, periods=scope.get("periods"), after=after, limit=PAGE)
        payload = ctx.client.gl_digest(ctx.call, query)
        if after is None and isinstance(payload, dict):
            scope["periods"] = _int_periods(payload.get("periods"))
        return payload

    return walk(ctx.probe, R_GL, lambda after: params_text(fiscal_year=year, closed_periods=1, limit=PAGE, after=after), page)


def run_gl(ctx: PerfCtx) -> None:
    for year in sorted({GL_BASE_YEAR, ctx.year}):
        gl_year(ctx, year)


# ---- 往来处理记录 ----


def proc_increment(ctx: PerfCtx, flag: str, since: str) -> list[dict]:
    pages = walk(
        ctx.probe, R_PROC,
        lambda after: params_text(flag=flag, changed_since=since, limit=PAGE, after=after),
        lambda after: ctx.client.arap_process_list(ctx.call, ProcListQuery(flag, changed_since=since, after=after, limit=PAGE)),
    )
    if pages:
        remember(ctx, "proc:" + flag, pages[0])
    return pages


def proc_digest(ctx: PerfCtx, flag: str) -> list[dict]:
    year = ctx.year
    return walk(
        ctx.probe, R_PROC,
        lambda after: params_text(flag=flag, digest=True, fiscal_year=year, limit=PAGE, after=after),
        lambda after: ctx.client.arap_process_digest(ctx.call, ProcDigestQuery(flag, fiscal_year=year, after=after, limit=PAGE)),
    )


def run_process(ctx: PerfCtx) -> None:
    for flag in PROC_FLAGS:
        proc_increment(ctx, flag, "0")
    for flag in PROC_FLAGS:
        proc_digest(ctx, flag)
