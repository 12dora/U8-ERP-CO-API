"""销售订单表头自定义项写入（`voucher-so-defines`）：在测试账套上数据驱动挑已审核、cDefine1 / cDefine2 为空或「无」的销售订单
（未关闭且没有发货单的、已关闭的、已有发货单的各一张），再挑一张 cDefine1 已有值的（照样覆盖）；挑不到的记在说明里跳过。
每张订单先快照订单表头、订单行、下游发货单和销售发票表头的整行，再走 vouchers/so_defines（fields 为 cdefine1 + cdefine2，
empty_values ["无"]，即调用方把两列一起写、把「无」当作空值）：

- 预演（dry_run）不改库、ufts 不变；expect 不符 409 state_mismatch，detail.current 的键恰好是请求的两列、值是库里当前值，库不变；
- 真写：只有 cDefine1、cDefine2 和桥盖的修改人、修改日期、修改时间列（cModifier、dModDate、dmodifysystime）变了，ufts 变了，
  响应 current 是写后的两列；订单行、发货单、发票一格不动；空的各种写法（NULL、空串、「无」）作 expect 都算同一个空；
- 同一幂等键、同一正文重放：响应一模一样、库不再变；已经是目标值：changed=false、不写库；
- 第一张订单另走：否定路径（不认识的键、cdefine17、值为「无」、空白、空串、type 不是 sale_order、超长、非文本列都是 400，
  后两者 field=fields.cdefineN；订单不存在 404）；SQL 把锁定人写成别人时 409 state_mismatch（detail.reason=locked、detail.locker），
  库不变，再按起始值写回；大小写不同算真改（只有 cDefine1 变）；只写 cdefine3：响应只有 cdefine3，cDefine1 / cDefine2 不动。
之后再走连带下游（cascade=true）一轮，见 so_defines_cascade。

路由只写不清空，所以还原用 SQL 把写过的列（cDefine1–3）和修改人类列按起始快照写回（测试账套上），整份快照（行版本列除外）与起始一致才算还原。
订单、编码全部来自 SQL 查询结果，代码里不写任何真实编码。步骤 kind 为 core_sodefines，挂在 arc_write.CORE_HANDLERS。
"""

from __future__ import annotations

from collections.abc import Callable

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.head_fix_bodies import Snap, fingerprint
from co.e2e.casespec.so_defines_bodies import (
    fits,
    hits_of,
    mismatch_problem,
    parse_cols,
    reply_dry,
    reply_write,
    sod_body,
    write_problem,
)
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.so_defines_cascade import CascState, cascade_cycle, cascade_restore
from co.e2e.casespec.so_defines_trip import (
    OK,
    WIDTH,
    Ctx,
    Trip,
    case_change,
    invalid,
    locked,
    number,
    post,
    same,
    single,
    snap,
)
from co.e2e.casespec.so_defines_sql import columns_sql, pick_sql, restore_sql, writable
from co.e2e.casespec.sqlexec import run_guarded

_NAME = "voucher-so-defines"
_ROUTES = ("/v1/vouchers/so_defines",)
_ID_MAX = 19
# 样本类别 → (说明, 依次试的挑单 SQL 类别)。
_KINDS = (
    ("plain", "两列为空、未关闭且没有发货单的已审核订单", ("plain", "plain_any")),
    ("closed", "两列为空的已关闭订单", ("closed",)),
    ("shipped", "两列为空、已有发货单的订单", ("shipped",)),
    ("filled", "cDefine1 已有值、cDefine2 为空的订单", ("filled",)),
)
# 用「另一种空写法」当 expect 的样本（验证 NULL、空串、「无」等价）。
_SWAP = frozenset({"closed", "filled"})


class NotApplicable(Exception):
    """账套里没有这个用例需要的数据：记 N/A，不是失败。"""


def _pick(ctx: Ctx, kinds: tuple[str, ...], used: tuple[str, ...]) -> str:
    for kind in kinds:
        cell = first_cell(run_guarded(ctx.env, pick_sql(kind, used), ctx.spec.name)).strip()
        if cell.isdigit() and 0 < len(cell) <= _ID_MAX and cell[0] != "0":
            return cell
    return ""


def _open(ctx: Ctx, so_id: str, kind: str) -> Trip:
    start = snap(ctx, so_id)
    ctx.start[so_id] = start
    return Trip(ctx, so_id, kind, start, {"cdefine1": number("E2E"), "cdefine2": number("E2I")}, kind in _SWAP)


# ---- 步骤 ----


def _dry(trip: Trip) -> str:
    bad, payload = post(trip, sod_body(trip.so_id, trip.new, trip.expect, dry=True), OK, "预演")
    if bad:
        return bad
    wrong = reply_dry(payload, True, trip.now)
    return f"预演：{wrong}"[:200] if wrong else same(trip, "预演")


def _mismatch(trip: Trip) -> str:
    wrong = {**trip.expect, "cdefine1": number("NOPE")}
    bad, payload = post(trip, sod_body(trip.so_id, trip.new, wrong), (409, "state_mismatch"), "expect 不符")
    return bad or mismatch_problem(payload, trip.now) or same(trip, "expect 不符")


def _forward(trip: Trip) -> str:
    """真写两列：只有这两列和修改人类列变了，响应 current 是写后的值。"""
    body = sod_body(trip.so_id, trip.new, trip.expect)
    bad, payload = post(trip, body, OK, "写入")
    if bad:
        return bad
    after = snap(trip.ctx, trip.so_id)
    problem, stamps = write_problem(trip.cur, after, trip.new)
    trip.ctx.stamps.update(stamps)
    trip.body, trip.reply, trip.cur = body, payload, after
    return reply_write(payload, True, trip.new) or problem


def _replay(trip: Trip) -> str:
    """同一幂等键、同一正文：响应一模一样，库不再变。"""
    bad, payload = post(trip, trip.body, OK, "幂等重放")
    if bad:
        return bad
    if payload != trip.reply:
        return f"幂等重放的响应应与第一次相同：{payload!r} ≠ {trip.reply!r}"[:200]
    return same(trip, "幂等重放")


def _already(trip: Trip) -> str:
    """库里已经是目标值：changed=false，不写库（行版本也不变）。"""
    bad, payload = post(trip, sod_body(trip.so_id, trip.new, dict(trip.new)), OK, "已是目标值")
    return bad or reply_write(payload, False, trip.new) or same(trip, "已是目标值")


def _plan(first: bool) -> list[Callable[[Trip], str]]:
    steps: list[Callable[[Trip], str]] = [_dry, _mismatch]
    if first:
        steps += [invalid, locked]
    steps += [_forward, _replay, _already]
    if first:
        steps += [case_change, single]
    return steps


def _trip(ctx: Ctx, so_id: str, kind: str, first: bool) -> str:
    trip = _open(ctx, so_id, kind)
    ctx.done.append(kind)
    for run in _plan(first):
        bad = run(trip)
        if bad:
            return f"{kind}：{bad}"[:200]
    return ""


def _cycle(ctx: Ctx) -> str:
    ctx.cols = parse_cols(sql_cells(run_guarded(ctx.env, columns_sql(), ctx.spec.name)))
    narrow = [key for key in ("cdefine1", "cdefine2") if not fits(ctx.cols, key, WIDTH)]
    if narrow:
        raise NotApplicable(f"{'、'.join(narrow)} 不是文本列或列宽不到 {WIDTH}")
    picks: list[tuple[str, str]] = []
    for kind, label, queries in _KINDS:
        found = _pick(ctx, queries, tuple(item for _kind, item in picks))
        if found:
            picks.append((kind, found))
        else:
            ctx.notes.append(f"没有{label}")
    if not picks:
        raise NotApplicable("账套没有 cDefine1 / cDefine2 可写的已审核销售订单")
    for index, (kind, found) in enumerate(picks):
        bad = _trip(ctx, found, kind, index == 0)
        if bad:
            return bad
    ctx.casc = CascState(ctx.env, ctx.spec, ctx.notes)
    return cascade_cycle(ctx.casc, tuple(ctx.start))


# ---- 还原 ----


def _repair(ctx: Ctx, so_id: str, before: Snap, now: Snap) -> None:
    """把变了的自定义项、修改人 / 日期 / 时间列按起始快照写回（测试账套上用 SQL，路由只写不清空）。"""
    start = before["SO"][so_id]
    cols = {
        col: start[col]
        for (tag, key, col) in hits_of(before, now)
        if tag == "SO" and key == so_id and col in start and writable(col)
    }
    if cols:
        run_guarded(ctx.env, restore_sql(so_id, cols), ctx.spec.name)


def _restore(ctx: Ctx) -> str:
    """每张动过的订单：快照与起始一致（行版本列除外）才算还原；不一致先写回，再报仍有差异。"""
    left: list[str] = []
    for so_id, before in ctx.start.items():
        now = snap(ctx, so_id)
        if not hits_of(before, now):
            continue
        _repair(ctx, so_id, before, now)
        hits = hits_of(before, snap(ctx, so_id))
        if hits:
            left.append(f"订单 {so_id} {sorted(hits)[:3]}")
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
    casc = ctx.casc or CascState(ctx.env, ctx.spec, [])
    touched = sorted(ctx.stamps | casc.stamps)
    stamps = "；写入还改了 " + "、".join(touched) if touched else "；没有修改人类列变化"
    prints = ",".join(fingerprint(item) for item in [*ctx.start.values(), *casc.start.values()])
    return "PASS", f"so_defines {'+'.join(ctx.done + casc.done)} 快照指纹 {prints}{stamps}{skipped}"[:400]


def step_sodefines(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    ctx = Ctx(env, spec)
    note = ""
    try:
        problem = _cycle(ctx)
    except NotApplicable as exc:
        problem, note = "", str(exc)
    except (RuntimeError, ValueError, KeyError) as exc:
        problem = str(exc)[:200]
    undo = "；".join(item for item in (_guarded(lambda: _restore(ctx)), _guarded(lambda: cascade_restore(ctx.casc))) if item)
    return _verdict(problem, undo, note, ctx)


SODEFINES_HANDLERS = {"core_sodefines": step_sodefines}


def sodefines_cases() -> list[CaseSpec]:
    step = Step("core_sodefines", id_from="none")
    title = (
        "已审核销售订单写表头自定义项（cdefine1 + cdefine2、empty_values「无」，另有单列 cdefine3）：只改请求的列，"
        "下游单据不动，幂等重放；连带下游只改请求的列、混合来源跳过；并还原"
    )
    tags = frozenset({"w:sale_order"})
    return [CaseSpec(_NAME, title, (), (step,), True, tags=tags, routes=_ROUTES)]
