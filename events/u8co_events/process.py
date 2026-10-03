"""应收应付处理事件（类型 ar_process / ap_process）：桥的 arap/process/list（只读 SQL，不登录 U8）。

一个事件对应一批处理：同一侧（AR / AP）的（处理方式 cProcStyle, 处理号 cCancelNo），不是一行往来明细。
应收冲应付、并账、票据背书等两边都记账的处理，两侧各发一条。
往来明细没有 rowversion，所以分两步：
- 新批次：按自增号 Auto_ID 增量读。在途事务可能先占小号、后提交，每轮回看一个窗口：
  lag = max(auto_id_lag, ident − watermark)，水位只推进到 max(0, watermark − lag)、不后退；窗口里已知的批次不重发。
- 制单 / 取消制单（cPZid 原地改）、取消处理（行被删）：每轮按期间（未结账期间 + 最近一个已结账期间）取批次摘要，
  与快照比对。更早的已结账期间不会再变，那里的批次滑出窗口后从快照里静默去掉。
首轮（还没有水位）只记快照，backfill_events 时对已有批次发 processed。ident 比上一轮的小（多半是库被还原）时
同首轮一样静默重记，记 watermark_reset，并把 epoch 加一混进之后的指纹（还原后自增号回退、重做的批次 event_id 不重复）。
水位文本见 process_diff.ProcMark。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from u8co_events.diff import Scope
from u8co_events.entities import EntityCommit, EntityEvent, EntitySnap
from u8co_events.process_diff import (
    FLAGS,
    Batch,
    Period,
    batch_from_item,
    change_kinds,
    checked,
    covered_periods,
    group_rows,
    ProcMark,
    first_fingerprint,
    lagged_mark,
    make_snap,
    next_fingerprint,
    number_field,
    parse_mark,
    row_from_item,
    snap_period,
    state_from_batch,
    state_from_rows,
)
from u8co_events.source import BridgeContractError, SourceContext, StopRequested, make_event

log = logging.getLogger("u8co_events")


# ---- 一轮 ----


@dataclass(frozen=True)
class Window:
    """一轮增量：处理行（已映射）、查询前的水位、IDENTITY 当前值、要做摘要的期间。"""

    rows: list[dict[str, Any]]
    watermark: int
    ident: int
    periods: tuple[Period, ...]


@dataclass
class _Round:
    scope: Scope
    flag: str
    known: dict[str, EntitySnap]
    mark: int
    # 首轮或还原后的静默重记；首轮回填；还原次数；历史批次的界（见 ProcMark）。
    quiet: bool = False
    backfill: bool = False
    epoch: int = 0
    base: int = 0
    upserts: dict[str, EntitySnap] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    events: list[EntityEvent] = field(default_factory=list)
    # 这一轮新记下的键：这一轮不再拿摘要比对它们（摘要只用来校正合计），下一轮再比。
    fresh: set[str] = field(default_factory=set)

    def emit(self, kind: str, prev: EntitySnap | None, curr: EntitySnap | None) -> None:
        self.events.append(make_event(self.scope, kind, prev, curr))

    def put(self, snap: EntitySnap, fresh: bool = False) -> None:
        self.upserts[snap.key] = snap
        if fresh:
            self.fresh.add(snap.key)


class ProcessSource:
    def __init__(self, ctx: SourceContext) -> None:
        self._ctx = ctx

    def poll(self, scope: Scope) -> None:
        ctx = self._ctx
        flag = FLAGS[scope.type]
        stored = parse_mark(ctx.store.watermark(scope.acc, scope.type))
        since = 0 if stored is None else stored.mark
        win = self._window(scope.acc, flag, since, probe=stored is None)
        run = self._round(scope, flag, stored, win)
        if run.quiet:
            # 首轮或库被还原：窗口里的批次只记快照，下一轮重读窗口时就不会当成新批次。
            rows = [row for row in self._rows(scope.acc, flag, run.mark) if row["id"] <= win.watermark]
            self._absorb(run, rows, emit=run.backfill)
        else:
            self._absorb(run, win.rows, emit=True)
        self._compare(run, self._digest(scope.acc, flag, win.periods), set(win.periods))
        self._prune(run, set(win.periods))
        note = None
        if stored is not None and run.quiet:
            last = stored.mark if stored.ident is None else stored.ident
            log.error("账套 %s %s 自增计数倒退（%s->%s），疑似库被还原，已静默重记", scope.acc, scope.type, last, win.ident)
            note = f"{stored.mark}->{run.mark}"
        ctx.store.commit_entities(
            scope,
            EntityCommit(
                upserts=list(run.upserts.values()),
                deleted=run.deleted,
                events=run.events,
                watermark=ProcMark(run.mark, win.ident, run.epoch, run.base).text(),
                scanned=True,
                reset_note=note,
            ),
        )

    def _round(self, scope: Scope, flag: str, stored: ProcMark | None, win: Window) -> _Round:
        """这一轮的范围：静默与否、记下的水位、epoch、历史批次的界。"""
        ctx = self._ctx
        reset = stored is not None and stored.restored(win.ident)
        quiet = stored is None or reset
        floor = 0 if stored is None or reset else stored.mark
        mark = lagged_mark(win.watermark, win.ident, ctx.cfg.auto_id_lag, floor)
        epoch = 0 if stored is None else stored.epoch + (1 if reset else 0)
        base = mark if stored is None or reset else stored.base
        backfill = stored is None and ctx.cfg.backfill_events and ctx.store.backfill_allowed(scope.acc, scope.type)
        known = ctx.store.all_entity_snaps(scope.acc, scope.type)
        return _Round(scope, flag, known, mark, quiet=quiet, backfill=backfill, epoch=epoch, base=base)

    def scan(self, scope: Scope) -> None:
        """每轮都做完整的期间摘要比对，删除扫描没有另外要做的，只记一次扫描时间。"""
        self._ctx.store.commit_entities(scope, EntityCommit(polled=False, scanned=True))

    # ---- 读桥 ----

    def _pages(self, acc: str, fields: Mapping[str, Any], limit: int | None = None) -> Iterator[dict[str, Any]]:
        after: object = None
        size = limit or self._ctx.cfg.page_limit
        while True:
            body = {**fields, "limit": size}
            if after is not None:
                body["after"] = after
            response = checked(self._ctx.lister.list_process(acc, body))
            yield response
            following = response.get("next")
            if following is None:
                return
            if isinstance(following, bool) or not isinstance(following, (int, str)) or following == after:
                raise BridgeContractError("应收应付处理列表响应的 next 没有前进")
            after = following

    def _window(self, acc: str, flag: str, since: int, probe: bool) -> Window:
        """增量读 (since, watermark]。probe 时只读一页一行，为的是水位、ident 和期间（首轮）。"""
        fields: dict[str, Any] = {"flag": flag, "changed_since": str(since), "open_only": False}
        if probe:
            fields["keys_only"] = True
        rows: list[dict[str, Any]] = []
        head: dict[str, Any] | None = None
        for response in self._pages(acc, fields, limit=1 if probe else None):
            if head is None:
                # 水位取第一页的：桥在查询前取，之后提交的行下一轮还能看到。
                head = response
            if probe:
                break
            rows.extend(row_from_item(item) for item in response["items"])
        if head is None:
            raise BridgeContractError("应收应付处理列表没有返回任何一页")
        return Window(rows, number_field(head, "watermark"), number_field(head, "ident"), covered_periods(head))

    def _rows(self, acc: str, flag: str, since: int, until: int | None = None) -> list[dict[str, Any]]:
        """(since, 桥的水位] 的完整处理行；给了 until 时读到 Auto_ID ≥ until 的那页为止。"""
        rows: list[dict[str, Any]] = []
        fields = {"flag": flag, "changed_since": str(since), "open_only": False}
        for response in self._pages(acc, fields):
            page = [row_from_item(item) for item in response["items"]]
            rows.extend(page)
            if until is not None and page and page[-1]["id"] >= until:
                break
        return rows

    def _digest(self, acc: str, flag: str, periods: tuple[Period, ...]) -> dict[str, Batch]:
        """逐期间取摘要（这样知道每批属于哪个期间），跨期间的同一批合并。"""
        merged: dict[str, Batch] = {}
        for year, period in periods:
            fields = {"flag": flag, "digest": True, "fiscal_year": year, "periods": [period]}
            for response in self._pages(acc, fields, limit=500):
                for item in response["items"]:
                    batch = batch_from_item(item, (year, period))
                    old = merged.get(batch.key)
                    merged[batch.key] = batch if old is None else old.merge(batch)
        return merged

    def _hydrated(self, run: _Round, batch: Batch, base: Mapping[str, Any] | None) -> dict[str, Any]:
        """按 Auto_ID 区间读回这一批的明细，补全 docs 和总账字段；合计仍以摘要为准。读不到（刚被取消）就用 base。"""
        rows = self._rows(run.scope.acc, run.flag, max(batch.min_id - 1, 0), until=batch.max_id)
        mine = [row for row in rows if row["key"] == batch.key and row["id"] <= batch.max_id]
        start = state_from_rows(run.flag, mine) if mine else base
        return state_from_batch(run.flag, batch, start)

    # ---- 对比 ----

    def _absorb(self, run: _Round, rows: list[dict[str, Any]], emit: bool) -> None:
        """增量行里快照没有的批次：记快照，emit 时发 processed。"""
        for key, group in group_rows(rows).items():
            if key in run.known or key in run.upserts:
                continue
            fingerprint = first_fingerprint(group[0]["id"], run.epoch)
            snap = make_snap(key, state_from_rows(run.flag, group), fingerprint, group[0]["code"])
            run.put(snap, fresh=True)
            if emit:
                run.emit("processed", None, snap)

    def _compare(self, run: _Round, batches: dict[str, Batch], covered: set[Period]) -> None:
        for key, batch in batches.items():
            if key in run.fresh:
                self._settle(run, run.upserts[key], batch)
            elif key in run.known:
                self._changed(run, run.known[key], batch)
            elif batch.min_id <= run.mark:
                self._unknown(run, batch)
            # 否则是增量读完之后才提交的批次：下一轮的增量（从 mark 起）会读到，届时发 processed。
        gone = [snap for key, snap in run.known.items() if key not in batches and snap_period(snap) in covered]
        if gone:
            self._cancel(run, gone, empty=not batches)

    def _settle(self, run: _Round, snap: EntitySnap, batch: Batch) -> None:
        """这一轮新记的批次：合计改用摘要的（之后的比对口径一致），不发事件。

        增量读完到取摘要之间又制单 / 取消制单了（凭证号不同）：凭证字段保留增量里的，下一轮比对时发 vouchered / unvouchered。
        """
        if snap.state.get("min_id") != batch.min_id:
            return
        state = state_from_batch(run.flag, batch, snap.state)
        if (snap.state.get("voucher_id") or None) != batch.pz:
            state.update({name: snap.state.get(name) for name in ("voucher_id", "gl_sign", "gl_no")})
        run.put(make_snap(snap.key, state, snap.fingerprint, batch.code))

    def _unknown(self, run: _Round, batch: Batch) -> None:
        """摘要里有、快照里没有、又早于水位的批次。

        - 首轮回填：发 processed；首轮（不回填）或还原后：静默记快照。
        - 平时、最小 Auto_ID 大于历史界 base：在途超过回看窗口才提交的批次，补发 processed（晚到）。
        - 平时、不大于 base：首轮之前的老批次（如期间反结账后重现），静默记快照。
        指纹按最小 Auto_ID 和 epoch 定，同一批再次出现时 event_id 不变，消费方按 event_id 去重。
        """
        fingerprint = first_fingerprint(batch.min_id, run.epoch)
        if run.backfill or (not run.quiet and batch.min_id > run.base):
            snap = make_snap(batch.key, self._hydrated(run, batch, None), fingerprint, batch.code)
            run.put(snap, fresh=True)
            run.emit("processed", None, snap)
            return
        run.put(make_snap(batch.key, state_from_batch(run.flag, batch, None), fingerprint, batch.code), fresh=True)

    def _changed(self, run: _Round, prev: EntitySnap, batch: Batch) -> None:
        kinds = change_kinds(prev, batch)
        if not kinds:
            quiet = state_from_batch(run.flag, batch, prev.state)
            if quiet != dict(prev.state):
                run.put(make_snap(prev.key, quiet, prev.fingerprint, batch.code))
            return
        if kinds == ("cancelled", "processed"):
            run.emit("cancelled", prev, None)
            fingerprint = first_fingerprint(batch.min_id, run.epoch)
            curr = make_snap(prev.key, self._hydrated(run, batch, None), fingerprint, batch.code)
            run.put(curr)
            run.emit("processed", None, curr)
            return
        state = self._hydrated(run, batch, prev.state)
        curr = make_snap(prev.key, state, next_fingerprint(prev.fingerprint, batch, run.epoch), batch.code)
        run.put(curr)
        run.emit(kinds[0], prev, curr)

    def _cancel(self, run: _Round, gone: list[EntitySnap], empty: bool) -> None:
        if empty:
            self._confirm(run, gone)
        for snap in gone:
            run.deleted.append(snap.key)
            run.emit("cancelled", snap, None)

    def _confirm(self, run: _Round, gone: list[EntitySnap]) -> None:
        """摘要整轮为空却要取消快照里的批次：逐批按最小 Auto_ID 回读，确认都读不到才算取消。

        没有按键的证明路由；回读与摘要走同一路由、同样的数据权限，能挡住账套或期间出错，挡不住操作员数据权限被收窄。
        """
        head = "应收应付处理摘要没有返回任何批次"
        limit = self._ctx.cfg.empty_scan_confirm_max
        if limit == 0 or len(gone) > limit:
            raise BridgeContractError(f"{head}，快照有 {len(gone)} 批待取消，超过逐批确认上限 {limit}，未发取消事件")
        for done, snap in enumerate(gone):
            if self._ctx.stopping():
                raise StopRequested(f"{head}，逐批确认到第 {done + 1} 批时收到停止信号")
            if self._still_there(run, snap):
                raise BridgeContractError(f"{head}，但批次 {snap.code}（Auto_ID {snap.doc_id}）仍能读到，未发取消事件")

    def _still_there(self, run: _Round, snap: EntitySnap) -> bool:
        fields = {"flag": run.flag, "changed_since": str(max(snap.doc_id - 1, 0)), "open_only": False, "keys_only": True}
        response = next(self._pages(run.scope.acc, fields, limit=1))
        rows = [row_from_item(item) for item in response["items"]]
        return any(row["id"] == snap.doc_id and row["key"] == snap.key for row in rows)

    def _prune(self, run: _Round, covered: set[Period]) -> None:
        """不在摘要期间里（更早的已结账期间）且已滑出回看窗口的批次：从快照里静默去掉。"""
        for key, snap in run.known.items():
            if key in run.upserts or key in run.deleted or snap_period(snap) in covered:
                continue
            if int(snap.state.get("max_id") or snap.doc_id) <= run.mark:
                run.deleted.append(key)


def build(ctx: SourceContext) -> ProcessSource:
    return ProcessSource(ctx)
