"""销售发货单表头修正（`voucher-head-fix`）：在测试账套上数据驱动挑已审核的发货单，快照发货单、下游销售出库单、销售发票、
发票往来明细（含同发票的其他发货单、销售订单表头）的整行 → vouchers/head_fix 改发货地址和业务员 → 逐格核对只变了目标列
（变的行由响应的 undo 给出，再和 downstream、往来明细的归属规则互相核对）→ 用反向调用（字段与 expect 对调，并带上正向响应的
undo）改回，整份快照与起始一致（目标列按桥文档的归一化比较：去首尾空白、不分大小写、空串与 NULL 相同；其余列逐字比较，
行版本列除外）。

五类样本各一轮，账套里挑不到的记在说明里跳过：
- single：一张出库单、一张发票，都只由它供货（出库单、发票、往来明细跟着变）；
- merged：发票合并了两张以上发货单，另一张还是旧值（发票被跳过）；回滚时把被跳过的发票列进 undo 应 409；
- merged_new：发票合并的另一张发货单已经是新值（发票被更新）；反向调用带 undo 恢复，不能靠重新判断；
- already_new：出库单已经是新值（记 skipped / already_new，不动它），反向调用不碰它；
- null：地址原本是 NULL，先填上一个编码，再用 addr_code 空串清回 NULL，严格比较。
另有表头业务员为空的发货单改业务员 → 409 person_empty；原收货地址不在档案里 / 原业务员无效的发货单改那一项 → 409 not_reversible；
未审核的发货单（真写和预演）→ 409 not_audited。
所有样本的原值都要可回滚（原地址为空或在客户的收货地址档案里，原业务员存在、在发货日期有效且部门是发货单部门）。
否定路径（在 single 上）：预演不改行、无事可做 changed=false 不写库、expect 不符 409、地址不存在 409、业务员不存在 / 不在
发货单部门 409、type 不是 dispatch、多余字段、缺 expect、expect 多余键、业务员空串、undo 不合法都是 400、单据不存在 404。
单据与人员编码全部来自 SQL 查询结果，代码里不写任何真实编码。用例自己按快照差异还原；步骤 kind 为 core_headfix，
挂在 arc_write.CORE_HANDLERS。
"""

from __future__ import annotations

from collections.abc import Callable

from co.e2e.casespec.head_fix_bodies import (
    already_new_problem,
    Snap,
    ar_report,
    change_problem,
    downstream_of,
    fingerprint,
    hf_body,
    reply_hf,
    reply_hf_dry,
    row_ids,
    same_problem,
    skipped_problem,
    undo_of,
    updated_problem,
    view_of,
    want_changes,
)
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.head_fix_ctx import (
    OK,
    Ctx,
    NotApplicable,
    Pick,
    Trip,
    pick_one,
    post,
    same,
    snap,
)
from co.e2e.casespec.head_fix_refuse import empty_person, not_audited, not_reversible, refusals, undo_mismatch
from co.e2e.casespec.head_fix_sql import (
    TABLES,
    already_new_pick_sql,
    merged_new_pick_sql,
    merged_pick_sql,
    null_pick_sql,
    reset_sql,
    single_pick_sql,
)
from co.e2e.casespec.sqlexec import run_guarded

_NAME = "voucher-head-fix"
_ROUTES = ("/v1/vouchers/head_fix",)
_TABLE = {"RD": "rdrecord32", "SB": "SaleBillVouch"}
# 样本类别 → 说明。
_KINDS = (
    ("single", "出库单与发票只由它供货的发货单"),
    ("merged", "发票合并多张发货单的发货单"),
    ("merged_new", "发票的其它来源发货单已是新值的发货单"),
    ("already_new", "出库单已是新值的发货单"),
    ("null", "地址为 NULL 的发货单"),
)
# 正向之后应被更新的下游表头。
_UPDATED = {"single": ("RD", "SB"), "merged_new": ("SB",), "already_new": ("SB",)}


def _open(ctx: Ctx, pick: Pick, kind: str) -> Trip:
    start = snap(ctx, pick.dlid)
    ctx.start[pick.dlid] = start
    if kind == "null":
        ctx.strict.add(pick.dlid)
    fields = {"addr_code": pick.addr} if kind == "null" else {"addr_code": pick.addr, "person_code": pick.person}
    return Trip(ctx, pick, kind, start, view_of(start), fields)


def _dry(trip: Trip) -> str:
    body = hf_body(trip.dlid, trip.fields, trip.want, True)
    bad, payload = post(trip.ctx, body, OK, "预演")
    return bad or reply_hf_dry(payload, trip.dlid, trip.want) or same(trip, "预演")


def _updates_ok(trip: Trip, down: list[dict]) -> str:
    """单据链上的行是否按本类样本的预期被更新、跳过或记 already_new。"""
    if trip.kind == "merged":
        rows = row_ids(trip.cur, "SB")
        return skipped_problem(down, "SaleBillVouch", rows[0]) if len(rows) == 1 else "合并发票的样本应恰有一张发票"
    need = tuple(trip.fields)
    for tag in _UPDATED.get(trip.kind, ()):
        for row in row_ids(trip.cur, tag):
            bad = updated_problem(down, _TABLE[tag], row, need)
            if bad:
                return bad
    if trip.kind == "already_new":
        return next((bad for row in row_ids(trip.cur, "RD") if (bad := already_new_problem(down, "rdrecord32", row))), "")
    return ""


def _forward(trip: Trip) -> str:
    body = hf_body(trip.dlid, trip.fields, trip.want)
    bad, payload = post(trip.ctx, body, OK, "正向修正")
    if bad:
        return bad
    undo = undo_of(payload)
    if undo is None:
        return "正向修正：响应缺少 undo 或形状不对"
    after = snap(trip.ctx, trip.dlid)
    down = downstream_of(payload)
    want, why = want_changes(trip.cur, trip.fields, down, undo)
    bad = (
        reply_hf(payload, trip.dlid, True, (trip.want, trip.fields))
        or why
        or _updates_ok(trip, down)
        or change_problem(trip.cur, after, want)
    )
    report = ar_report(trip.cur, undo)
    if report and not bad:
        trip.ctx.reports.append(f"{trip.kind} {report}")
    trip.undo, trip.cur = undo, after
    return f"正向修正：{bad}"[:200] if bad else ""


def _noop(trip: Trip) -> str:
    """值已经是目标值：changed=false，不写库。"""
    body = hf_body(trip.dlid, trip.fields, trip.fields)
    bad, payload = post(trip.ctx, body, OK, "无事可做")
    bad = bad or reply_hf(payload, trip.dlid, False, (trip.fields, trip.fields))
    return bad or same(trip, "无事可做")


def _reverse(trip: Trip) -> str:
    """字段与 expect 对调，并带上正向响应的 undo；只恢复 undo 列出的行，整份快照回到起始。"""
    body = hf_body(trip.dlid, trip.want, trip.fields, undo=trip.undo)
    bad, payload = post(trip.ctx, body, OK, "反向调用")
    after = snap(trip.ctx, trip.dlid)
    down = downstream_of(payload)
    back = undo_of(payload)
    bad = bad or reply_hf(payload, trip.dlid, True, (trip.fields, trip.want))
    if not bad and back != trip.undo:
        bad = f"回滚响应的 undo {back!r} 应与正向的 {trip.undo!r} 相同（再回滚用）"[:200]
    if not bad:
        want, why = want_changes(trip.cur, trip.want, down, trip.undo, False)
        bad = why or change_problem(trip.cur, after, want)
    bad = bad or same_problem(trip.ctx.start[trip.dlid], after, not trip.strict)
    trip.cur = after
    return f"反向调用：{bad}"[:200] if bad else ""


def _clear_noop(trip: Trip) -> str:
    """地址已经是 NULL，再用 addr_code 空串清空：无事可做，不写库。"""
    body = hf_body(trip.dlid, {"addr_code": ""}, {"addr_code": ""})
    bad, payload = post(trip.ctx, body, OK, "空串清空已是 NULL 的地址")
    bad = bad or reply_hf(payload, trip.dlid, False, ({"addr_code": ""}, {"addr_code": ""}))
    return bad or same(trip, "空串清空已是 NULL 的地址")


def _trip(ctx: Ctx, pick: Pick, kind: str) -> str:
    trip = _open(ctx, pick, kind)
    if kind == "null" and trip.old["addr_code"] != "":
        ctx.notes.append("地址为 NULL 的发货单（挑到的地址不是 NULL）")
        return ""
    ctx.done.append(kind)
    steps: list[Callable[[Trip], str]] = [_dry]
    if kind == "single":
        steps.append(refusals)
    steps.append(_forward)
    if kind == "merged":
        steps.append(undo_mismatch)
    steps += [_noop, _reverse]
    if kind == "null":
        steps.append(_clear_noop)
    for run in steps:
        bad = run(trip)
        if bad:
            return f"{kind}：{bad}"[:200]
    return ""


def _queries(kind: str, used: tuple[str, ...]) -> tuple[str, ...]:
    return {
        "single": (single_pick_sql(True), single_pick_sql(False)),
        "merged": (merged_pick_sql(used),),
        "merged_new": (merged_new_pick_sql(used),),
        "already_new": (already_new_pick_sql(used),),
        "null": (null_pick_sql(True), null_pick_sql(False)),
    }[kind]


def _cycle(ctx: Ctx) -> str:
    picks, used = [], []
    for kind, label in _KINDS:
        found = pick_one(ctx, _queries(kind, tuple(used)))
        if found is None:
            ctx.notes.append(f"没有{label}")
            continue
        used.append(found.dlid)
        picks.append((kind, found))
    for kind, found in picks:
        bad = _trip(ctx, found, kind)
        if bad:
            return bad
    if not ctx.done:
        raise NotApplicable("账套没有可用于表头修正的已审核发货单")
    skip = tuple(used)
    return empty_person(ctx, skip) or not_reversible(ctx, skip) or not_audited(ctx, skip)


def _repair(ctx: Ctx, before: Snap, now: Snap) -> None:
    """路由还原不了时的兜底（测试账套上）：把变了的目标列按起始快照写回。"""
    for tag, (_table, _key, cols) in TABLES.items():
        for row, vals in before.get(tag, {}).items():
            if row not in now.get(tag, {}) or not cols:
                continue
            back = {c: vals[c.lower()] for c in cols if c.lower() in vals and vals[c.lower()] != now[tag][row].get(c.lower())}
            if back:
                run_guarded(ctx.env, reset_sql(tag, row, back), ctx.spec.name)


def _restore(ctx: Ctx) -> str:
    """每张动过的发货单：快照与起始一致才算还原；不一致先兜底写回，再报失败。"""
    left: list[str] = []
    for dlid, before in ctx.start.items():
        now = snap(ctx, dlid)
        hit = same_problem(before, now, dlid not in ctx.strict)
        if hit:
            _repair(ctx, before, now)
            left.append(hit)
    return "还原后快照仍有差异 " + "；".join(left) if left else ""


def _guarded(step: Callable[[], str]) -> str:
    try:
        return step()
    except (RuntimeError, ValueError, KeyError) as exc:
        return str(exc)[:200]


def _verdict(problem: str, undo: str, note: str, ctx: Ctx) -> tuple[str, str]:
    if problem or undo:
        return "FAIL", "；".join(item for item in (problem, undo[:100]) if item)[:240]
    if note:
        return "N/A", note
    skipped = "；跳过：" + "、".join(ctx.notes) if ctx.notes else ""
    reports = "；" + "；".join(ctx.reports) if ctx.reports else ""
    prints = ",".join(fingerprint(item) for item in ctx.start.values())
    return "PASS", f"head_fix {'+'.join(ctx.done)} 快照指纹 {prints}{reports}{skipped}"[:400]


def step_headfix(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    ctx = Ctx(env, spec)
    note = ""
    try:
        problem = _cycle(ctx)
    except NotApplicable as exc:
        problem, note = "", str(exc)
    except (RuntimeError, ValueError, KeyError) as exc:
        problem = str(exc)[:200]
    undo = _guarded(lambda: _restore(ctx))
    return _verdict(problem, undo, note, ctx)


HEADFIX_HANDLERS = {"core_headfix": step_headfix}


def headfix_cases() -> list[CaseSpec]:
    step = Step("core_headfix", id_from="none")
    title = "已审核发货单改发货地址与业务员（下游出库单、发票、往来明细跟着变）并用带 undo 的反向调用还原"
    tags = frozenset({"w:dispatch", "w:sale_out", "w:sale_invoice", "arc:customer_address"})
    return [CaseSpec(_NAME, title, (), (step,), True, tags=tags, routes=_ROUTES)]
